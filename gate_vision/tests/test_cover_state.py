"""Exact Home Assistant cover-state behaviour for `cover.main_gate`."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.cover_state import Availability, get_effective_cover_state, get_fault, get_vision_state
from app.gate_controller import GateController
from app.main import Runtime
from app.models import (
    CoverState,
    GatePosition,
    GateTarget,
    Observation,
    OperationResult,
    VisionState,
)
from app.vision import RollingVision


HA_COVER_STATES = {"open", "closed", "opening", "closing", "unknown"}


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


class SlowRelay:
    """Relay whose pulse never resolves, so the operation stays active."""

    def __init__(self) -> None:
        self.released = asyncio.Event()

    async def pulse_gate(self):
        await self.released.wait()
        raise AssertionError("test never releases the pulse")


class Harness:
    """Rolling vision plus a controller, exposing only the effective state."""

    def __init__(self) -> None:
        self.vision = RollingVision(window=4, required=3)
        self.relay = SlowRelay()
        self.controller = GateController(
            self.relay,
            self.vision,
            travel_timeout_seconds=5,
            partial_stall_seconds=10,
            wrong_endpoint_seconds=5,
            max_attempts=3,
        )
        self.confirmed = GatePosition.UNKNOWN
        self.confirmation_current = False
        self.latest = Observation(GatePosition.UNKNOWN, 0.0)
        self.base = datetime.now(UTC)
        self._frames = 0

    async def see(
        self,
        position: GatePosition,
        count: int = 1,
        *,
        confidence: float = 0.96,
        moving: bool = False,
    ) -> None:
        for _ in range(count):
            self._frames += 1
            observation = Observation(
                position,
                confidence,
                is_moving=moving,
                timestamp=self.base + timedelta(seconds=self._frames),
            )
            snapshot = await self.vision.add(observation)
            self.latest = observation
            self.confirmed = snapshot.confirmed
            self.confirmation_current = snapshot.confirmation_current

    def state(self) -> str:
        return get_effective_cover_state(
            active_target=self.controller.requested_target,
            confirmed=self.confirmed,
            confirmation_current=self.confirmation_current,
        ).value

    def vision_state(self) -> VisionState:
        return get_vision_state(
            latest=self.latest,
            confirmed=self.confirmed,
            confirmation_current=self.confirmation_current,
        )

    async def request(self, target: GateTarget) -> asyncio.Task:
        """Start a request and return once the controller has accepted it."""
        task = asyncio.create_task(self.controller.request_state(target, source="test"))
        while not self.controller.active:
            await asyncio.sleep(0)
        return task

    async def abandon(self, task: asyncio.Task) -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_only_home_assistant_cover_states_can_be_produced() -> None:
    assert {state.value for state in CoverState} == HA_COVER_STATES
    for state in ("partial", "moving", "fault", "stopped", "error"):
        assert state not in HA_COVER_STATES


@pytest.mark.asyncio
async def test_confirmed_closed_and_idle_reports_closed() -> None:
    harness = Harness()
    await harness.see(GatePosition.CLOSED, 3)
    assert harness.controller.active is False
    assert harness.state() == "closed"


@pytest.mark.asyncio
async def test_accepted_open_request_reports_opening_before_any_frame() -> None:
    harness = Harness()
    await harness.see(GatePosition.CLOSED, 3)
    task = await harness.request(GateTarget.OPEN)
    # No camera frame has arrived since the request was accepted.
    assert harness.latest.position is GatePosition.CLOSED
    assert harness.state() == "opening"
    await harness.abandon(task)


@pytest.mark.asyncio
async def test_rolling_confirmation_of_open_reports_open() -> None:
    harness = Harness()
    await harness.see(GatePosition.CLOSED, 3)
    task = await harness.request(GateTarget.OPEN)
    await harness.see(GatePosition.PARTIALLY_OPEN, 2, moving=True)
    assert harness.state() == "opening"
    await harness.abandon(task)
    await harness.see(GatePosition.OPEN, 3)
    assert harness.state() == "open"


@pytest.mark.asyncio
async def test_accepted_close_request_reports_closing_before_any_frame() -> None:
    harness = Harness()
    await harness.see(GatePosition.OPEN, 3)
    task = await harness.request(GateTarget.CLOSED)
    assert harness.latest.position is GatePosition.OPEN
    assert harness.state() == "closing"
    await harness.abandon(task)


@pytest.mark.asyncio
async def test_rolling_confirmation_of_closed_reports_closed() -> None:
    harness = Harness()
    await harness.see(GatePosition.OPEN, 3)
    task = await harness.request(GateTarget.CLOSED)
    assert harness.state() == "closing"
    await harness.abandon(task)
    await harness.see(GatePosition.CLOSED, 3)
    assert harness.state() == "closed"


@pytest.mark.asyncio
async def test_idle_without_confident_confirmation_reports_unknown() -> None:
    harness = Harness()
    await harness.see(GatePosition.UNKNOWN, 4)
    assert harness.controller.active is False
    assert harness.state() == "unknown"


@pytest.mark.asyncio
async def test_stale_confirmation_is_not_reused_indefinitely() -> None:
    harness = Harness()
    await harness.see(GatePosition.CLOSED, 3)
    assert harness.state() == "closed"
    await harness.see(GatePosition.UNKNOWN, 2)
    assert harness.confirmed is GatePosition.CLOSED
    assert harness.state() == "unknown"


@pytest.mark.asyncio
async def test_partial_reports_unknown_and_exposes_partial_diagnostics() -> None:
    harness = Harness()
    await harness.see(GatePosition.PARTIALLY_OPEN, 4, confidence=0.85)
    assert harness.state() == "unknown"
    assert harness.vision_state() is VisionState.PARTIAL
    assert (
        get_fault(
            available=True,
            availability_reason=None,
            controller_active=False,
            vision_state=harness.vision_state(),
            last_result=None,
        )
        == "gate_stopped_mid_travel"
    )


@pytest.mark.asyncio
async def test_moving_gate_is_a_diagnostic_not_a_cover_state() -> None:
    harness = Harness()
    await harness.see(GatePosition.OPEN, 3)
    await harness.see(GatePosition.PARTIALLY_OPEN, 2, moving=True)
    assert harness.vision_state() is VisionState.MOVING
    assert harness.state() in HA_COVER_STATES
    assert harness.state() == "unknown"


def test_sustained_camera_failure_makes_the_entity_unavailable() -> None:
    clock = Clock()
    availability = Availability(15.0, clock)
    assert availability.update(camera_online=True, monitoring_ready=True) is True

    # A single failed frame must not withdraw the entity.
    assert availability.update(camera_online=False, monitoring_ready=True) is True
    clock.now = 14.9
    assert availability.update(camera_online=False, monitoring_ready=True) is True
    clock.now = 15.0
    assert availability.update(camera_online=False, monitoring_ready=True) is False
    assert availability.reason == "camera_offline"


def test_camera_recovery_restores_availability() -> None:
    clock = Clock()
    availability = Availability(15.0, clock)
    availability.update(camera_online=False, monitoring_ready=True)
    clock.now = 20.0
    assert availability.update(camera_online=False, monitoring_ready=True) is False

    clock.now = 21.0
    assert availability.update(camera_online=True, monitoring_ready=True) is True
    assert availability.reason is None
    # The grace period restarts, so a later dropout is debounced again.
    assert availability.update(camera_online=False, monitoring_ready=True) is True


def test_lost_gate_state_monitoring_is_immediately_unavailable() -> None:
    availability = Availability(15.0, Clock())
    assert availability.update(camera_online=True, monitoring_ready=False) is False
    assert availability.reason == "gate_state_monitoring_unavailable"


def test_unavailable_entity_reports_its_reason_as_the_fault() -> None:
    assert (
        get_fault(
            available=False,
            availability_reason="camera_offline",
            controller_active=False,
            vision_state=VisionState.UNKNOWN,
            last_result=None,
        )
        == "camera_offline"
    )


def test_confident_endpoint_clears_a_spent_operation_fault() -> None:
    assert (
        get_fault(
            available=True,
            availability_reason=None,
            controller_active=False,
            vision_state=VisionState.UNKNOWN,
            last_result=OperationResult.FAILED_AFTER_3_ATTEMPTS,
        )
        == "target_not_reached_after_three_attempts"
    )
    assert (
        get_fault(
            available=True,
            availability_reason=None,
            controller_active=False,
            vision_state=VisionState.OPEN,
            last_result=OperationResult.FAILED_AFTER_3_ATTEMPTS,
        )
        is None
    )


class FakeCameraStatus:
    def __init__(self) -> None:
        self.online = True
        self.last_frame_at = None
        self.last_error = None


class FakeCamera:
    def __init__(self) -> None:
        self.status = FakeCameraStatus()


class FakeClassifier:
    ready = True
    count = 4
    reference_files = ()


def build_runtime(harness: Harness, clock: Clock) -> Runtime:
    from app.config import Settings
    from app.mqtt_cover import MqttCover

    runtime = Runtime(Settings(), FakeCamera(), FakeClassifier(), harness.vision)
    runtime.availability = Availability(15.0, clock)
    runtime.attach(harness.controller, MqttCover(lambda: {}))
    return runtime


def sync(runtime: Runtime, harness: Harness) -> dict:
    runtime.latest_observation = harness.latest
    runtime.confirmed_state = harness.confirmed
    runtime.confirmation_current = harness.confirmation_current
    runtime.update_availability()
    return runtime.status_payload()


@pytest.mark.asyncio
async def test_status_payload_only_ever_carries_valid_cover_states() -> None:
    harness = Harness()
    runtime = build_runtime(harness, Clock())

    await harness.see(GatePosition.CLOSED, 3)
    assert sync(runtime, harness)["cover_state"] == "closed"

    task = await harness.request(GateTarget.OPEN)
    payload = sync(runtime, harness)
    assert payload["cover_state"] == "opening"
    assert payload["target_state"] == "OPEN"

    await harness.see(GatePosition.PARTIALLY_OPEN, 2, moving=True)
    payload = sync(runtime, harness)
    assert payload["cover_state"] == "opening"
    assert payload["vision_state"] == "MOVING"

    await harness.abandon(task)
    await harness.see(GatePosition.OPEN, 3)
    payload = sync(runtime, harness)
    assert payload["cover_state"] == "open"
    assert payload["fault"] is False
    assert payload["available"] is True


@pytest.mark.asyncio
async def test_sustained_camera_failure_then_recovery_through_the_status_payload() -> None:
    harness = Harness()
    clock = Clock()
    runtime = build_runtime(harness, clock)
    await harness.see(GatePosition.CLOSED, 3)
    assert sync(runtime, harness)["available"] is True

    runtime.camera.status.online = False
    assert sync(runtime, harness)["available"] is True
    clock.now = 15.0
    payload = sync(runtime, harness)
    assert payload["available"] is False
    assert payload["availability_reason"] == "camera_offline"
    assert payload["fault_reason"] == "camera_offline"

    runtime.camera.status.online = True
    clock.now = 16.0
    await harness.see(GatePosition.CLOSED, 3)
    payload = sync(runtime, harness)
    assert payload["available"] is True
    assert payload["cover_state"] == "closed"
    assert payload["fault"] is False
