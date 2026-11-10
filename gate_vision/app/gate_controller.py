from __future__ import annotations

import asyncio
import inspect
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Awaitable, Callable, Protocol

from app.homeassistant import PulseResult
from app.models import (
    ControllerState,
    GatePosition,
    GateTarget,
    Operation,
    OperationResult,
)
from app.vision import RollingVision


LOGGER = logging.getLogger(__name__)


class Relay(Protocol):
    async def pulse_gate(self) -> PulseResult: ...


class OperationInProgressError(RuntimeError):
    pass


ChangeCallback = Callable[[], Awaitable[None] | None]


class GateController:
    """One target-state path for HA, the web UI, and future automations."""

    def __init__(
        self,
        relay: Relay,
        vision: RollingVision,
        *,
        travel_timeout_seconds: float = 35.0,
        on_change: ChangeCallback | None = None,
    ) -> None:
        self.relay = relay
        self.vision = vision
        self.travel_timeout_seconds = travel_timeout_seconds
        self.on_change = on_change
        self.operation = Operation()
        self.events: deque[dict[str, str]] = deque(maxlen=100)
        self._lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        """True while an accepted operation is still working towards its target."""
        return self._lock.locked() and self.operation.result is OperationResult.IN_PROGRESS

    @property
    def requested_target(self) -> GateTarget | None:
        return self.operation.target if self.active else None

    async def request_state(
        self,
        target: GateTarget,
        *,
        source: str = "home_assistant",
    ) -> OperationResult:
        if self._lock.locked():
            raise OperationInProgressError("A gate operation is already in progress")
        await self._lock.acquire()
        # Accepting the request is what makes the cover opening/closing, so the
        # operation is recorded and pushed before any camera work is awaited.
        self.operation = Operation(
            target=target,
            source=source,
            state=ControllerState.VALIDATING_STATE,
            result=OperationResult.IN_PROGRESS,
            status="Checking camera state",
            started_at=datetime.now(UTC),
        )
        self._event(f"{target.value} requested by {source}")
        try:
            await self._notify()
            return await self._run(target)
        finally:
            self._lock.release()
            await self._notify()

    async def manual_pulse(self, *, source: str = "web_ui") -> OperationResult:
        """Send one relay pulse without requiring a known gate state.

        Camera verification can leave the gate state unknown, and refusing every
        command in that case leaves no way to work the gate at all. This is the
        deliberate human override: it is a bare toggle, so it does not claim a
        target or verify one, and it still refuses to fire while the gate is
        moving.
        """
        if self._lock.locked():
            raise OperationInProgressError("A gate operation is already in progress")
        await self._lock.acquire()
        self.operation = Operation(
            source=source,
            state=ControllerState.SENDING_COMMAND,
            result=OperationResult.IN_PROGRESS,
            max_attempts=1,
            status="Sending manual relay pulse",
            started_at=datetime.now(UTC),
        )
        self._event(f"Manual relay pulse requested by {source}")
        try:
            await self._notify()
            snapshot = await self.vision.snapshot()
            if snapshot.latest.is_moving:
                return await self._finish(
                    OperationResult.GATE_MOVING,
                    ControllerState.ABORTED,
                    "Gate is moving; no pulse sent",
                )
            pulse = await self.relay.pulse_gate()
            self.operation.attempt = 1
            self.operation.last_relay_pulse = pulse.sent_at
            if not pulse.success:
                return await self._finish(
                    OperationResult.RELAY_FAILED,
                    ControllerState.FAILED,
                    pulse.detail or "Home Assistant relay script failed",
                )
            return await self._finish(
                OperationResult.MANUAL_PULSE_SENT,
                ControllerState.SUCCESS,
                "Manual pulse sent; gate state is not verified",
            )
        finally:
            self._lock.release()
            await self._notify()

    async def _run(self, target: GateTarget) -> OperationResult:
        snapshot = await self.vision.snapshot()
        if snapshot.latest.is_moving:
            return await self._finish(
                OperationResult.GATE_MOVING,
                ControllerState.ABORTED,
                "Gate is moving; no command sent",
            )
        target_position = GatePosition(target.value)
        clear_endpoint = (
            snapshot.confirmed in (GatePosition.OPEN, GatePosition.CLOSED)
            and snapshot.latest.position == snapshot.confirmed
            and snapshot.latest.confidence >= 0.80
        )
        clear_partial = (
            snapshot.latest.position == GatePosition.PARTIALLY_OPEN
            and snapshot.latest.confidence >= 0.70
        )
        if snapshot.confirmed == target_position and clear_endpoint:
            return await self._finish(
                OperationResult.ALREADY_AT_TARGET,
                ControllerState.SUCCESS,
                f"Gate is already {target.value}",
            )
        if not clear_endpoint and not clear_partial:
            return await self._finish(
                OperationResult.STATE_UNKNOWN,
                ControllerState.ABORTED,
                "Camera state is unknown; no command sent",
            )

        latest = await self.vision.snapshot()
        if latest.latest.is_moving:
            return await self._finish(
                OperationResult.GATE_MOVING,
                ControllerState.ABORTED,
                "Gate started moving before the command; no pulse sent",
            )

        self.operation.attempt = 1
        self.operation.state = ControllerState.SENDING_COMMAND
        self.operation.status = "Sending relay pulse"
        await self._notify()
        pulse = await self.relay.pulse_gate()
        self.operation.last_relay_pulse = pulse.sent_at
        if not pulse.success:
            return await self._finish(
                OperationResult.RELAY_FAILED,
                ControllerState.FAILED,
                pulse.detail or "Home Assistant relay script failed",
            )
        self._event("Relay pulse sent")
        self.operation.state = ControllerState.WAITING_FOR_TARGET
        self.operation.status = f"Waiting for confirmed {target.value}"
        await self._notify()

        if await self._wait_after_pulse(target_position):
            return await self._finish(
                OperationResult.SUCCESS,
                ControllerState.SUCCESS,
                f"Gate confirmed {target.value}",
            )
        return await self._finish(
            OperationResult.ABORTED_UNCERTAIN,
            ControllerState.ABORTED,
            "Target not confirmed before timeout; no further pulse sent",
        )

    async def _wait_after_pulse(self, target: GatePosition) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.travel_timeout_seconds
        snapshot = await self.vision.snapshot()
        while True:
            if (
                not snapshot.latest.is_moving
                and snapshot.confirmation_current
                and snapshot.confirmed == target
                and snapshot.latest.position == target
                and snapshot.latest.confidence >= 0.80
            ):
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                snapshot = await self.vision.wait_for_update(snapshot.version, remaining)
            except TimeoutError:
                return False

    async def _finish(
        self,
        result: OperationResult,
        state: ControllerState,
        status: str,
    ) -> OperationResult:
        self.operation.result = result
        self.operation.state = state
        self.operation.status = status
        self.operation.completed_at = datetime.now(UTC)
        self._event(f"Operation result: {result.value}")
        await self._notify()
        return result

    def _event(self, message: str) -> None:
        LOGGER.info(message)
        self.events.append({"timestamp": datetime.now(UTC).isoformat(), "message": message})

    async def _notify(self) -> None:
        if self.on_change is None:
            return
        result = self.on_change()
        if inspect.isawaitable(result):
            await result
