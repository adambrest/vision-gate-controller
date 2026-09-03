from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.gate_controller import GateController, OperationInProgressError
from app.homeassistant import PulseResult
from app.models import GatePosition, GateTarget, Observation, OperationResult
from app.vision import RollingVision


class FakeRelay:
    def __init__(self) -> None:
        self.pulses: list[PulseResult] = []
        self.base = datetime.now(UTC)

    async def pulse_gate(self) -> PulseResult:
        sent_at = self.base + timedelta(seconds=len(self.pulses) * 20)
        result = PulseResult(True, sent_at)
        self.pulses.append(result)
        return result


async def add(
    vision: RollingVision,
    position: GatePosition,
    count: int = 1,
    *,
    moving: bool = False,
    start: datetime | None = None,
    step: float = 1.0,
) -> None:
    start = start or datetime.now(UTC)
    for index in range(count):
        await vision.add(
            Observation(
                position,
                0.96,
                is_moving=moving,
                timestamp=start + timedelta(seconds=index * step),
            )
        )


async def wait_for_pulses(relay: FakeRelay, count: int) -> None:
    async def waiting() -> None:
        while len(relay.pulses) < count:
            await asyncio.sleep(0)
    await asyncio.wait_for(waiting(), timeout=1)


def controller(relay: FakeRelay, vision: RollingVision, timeout: float = 0.2) -> GateController:
    return GateController(
        relay,
        vision,
        travel_timeout_seconds=timeout,
        partial_stall_seconds=10,
        wrong_endpoint_seconds=5,
        max_attempts=3,
    )


@pytest.mark.asyncio
async def test_closed_to_open_succeeds_with_one_pulse() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await add(vision, GatePosition.CLOSED, 3)
    gate = controller(relay, vision)
    task = asyncio.create_task(gate.request_state(GateTarget.OPEN))
    await wait_for_pulses(relay, 1)
    await add(vision, GatePosition.OPEN, 3, start=relay.pulses[0].sent_at, step=1)
    assert await task == OperationResult.SUCCESS
    assert len(relay.pulses) == 1


@pytest.mark.asyncio
async def test_open_to_closed_succeeds_with_one_pulse() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await add(vision, GatePosition.OPEN, 3)
    gate = controller(relay, vision)
    task = asyncio.create_task(gate.request_state(GateTarget.CLOSED))
    await wait_for_pulses(relay, 1)
    await add(vision, GatePosition.CLOSED, 3, start=relay.pulses[0].sent_at, step=1)
    assert await task == OperationResult.SUCCESS
    assert len(relay.pulses) == 1


@pytest.mark.asyncio
async def test_already_at_target_uses_no_pulse() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await add(vision, GatePosition.OPEN, 3)
    assert await controller(relay, vision).request_state(GateTarget.OPEN) == OperationResult.ALREADY_AT_TARGET
    assert not relay.pulses


@pytest.mark.asyncio
async def test_unknown_refuses_command() -> None:
    vision, relay = RollingVision(), FakeRelay()
    assert await controller(relay, vision).request_state(GateTarget.OPEN) == OperationResult.STATE_UNKNOWN
    assert not relay.pulses


@pytest.mark.asyncio
async def test_low_confidence_partial_refuses_command() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await vision.add(Observation(GatePosition.PARTIALLY_OPEN, 0.40))
    assert await controller(relay, vision).request_state(GateTarget.OPEN) == OperationResult.STATE_UNKNOWN
    assert not relay.pulses


@pytest.mark.asyncio
async def test_stale_confirmed_endpoint_with_unknown_latest_refuses_command() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await add(vision, GatePosition.CLOSED, 3)
    await add(vision, GatePosition.UNKNOWN)
    assert await controller(relay, vision).request_state(GateTarget.OPEN) == OperationResult.STATE_UNKNOWN
    assert not relay.pulses


@pytest.mark.asyncio
async def test_wrong_endpoint_for_five_seconds_allows_one_retry() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await add(vision, GatePosition.CLOSED, 3)
    gate = controller(relay, vision)
    task = asyncio.create_task(gate.request_state(GateTarget.OPEN))
    await wait_for_pulses(relay, 1)
    await add(vision, GatePosition.CLOSED, 3, start=relay.pulses[0].sent_at + timedelta(milliseconds=1), step=3)
    await wait_for_pulses(relay, 2)
    await add(vision, GatePosition.OPEN, 3, start=relay.pulses[1].sent_at + timedelta(milliseconds=1))
    assert await task == OperationResult.SUCCESS
    assert len(relay.pulses) == 2


@pytest.mark.asyncio
async def test_stationary_partial_for_ten_seconds_allows_retry() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await add(vision, GatePosition.CLOSED, 3)
    gate = controller(relay, vision)
    task = asyncio.create_task(gate.request_state(GateTarget.OPEN))
    await wait_for_pulses(relay, 1)
    await add(vision, GatePosition.PARTIALLY_OPEN, 3, start=relay.pulses[0].sent_at + timedelta(milliseconds=1), step=5.5)
    await wait_for_pulses(relay, 2)
    await add(vision, GatePosition.OPEN, 3, start=relay.pulses[1].sent_at + timedelta(milliseconds=1))
    assert await task == OperationResult.SUCCESS
    assert len(relay.pulses) == 2


@pytest.mark.asyncio
async def test_no_retry_while_moving() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await add(vision, GatePosition.CLOSED, 3)
    gate = controller(relay, vision, timeout=0.03)
    task = asyncio.create_task(gate.request_state(GateTarget.OPEN))
    await wait_for_pulses(relay, 1)
    await add(vision, GatePosition.PARTIALLY_OPEN, moving=True, start=relay.pulses[0].sent_at)
    assert await task == OperationResult.ABORTED_UNCERTAIN
    assert len(relay.pulses) == 1


@pytest.mark.asyncio
async def test_unknown_after_pulse_never_retries() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await add(vision, GatePosition.CLOSED, 3)
    gate = controller(relay, vision, timeout=0.03)
    task = asyncio.create_task(gate.request_state(GateTarget.OPEN))
    await wait_for_pulses(relay, 1)
    await add(vision, GatePosition.UNKNOWN, 3, start=relay.pulses[0].sent_at)
    assert await task == OperationResult.ABORTED_UNCERTAIN
    assert len(relay.pulses) == 1


@pytest.mark.asyncio
async def test_partial_stall_then_wrong_endpoint_uses_final_recovery_pulse() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await add(vision, GatePosition.CLOSED, 3)
    gate = controller(relay, vision)
    task = asyncio.create_task(gate.request_state(GateTarget.OPEN))

    await wait_for_pulses(relay, 1)
    await add(
        vision,
        GatePosition.PARTIALLY_OPEN,
        3,
        start=relay.pulses[0].sent_at + timedelta(milliseconds=1),
        step=5.5,
    )
    await wait_for_pulses(relay, 2)
    await add(
        vision,
        GatePosition.CLOSED,
        3,
        start=relay.pulses[1].sent_at + timedelta(milliseconds=1),
        step=3,
    )
    await wait_for_pulses(relay, 3)
    await add(
        vision,
        GatePosition.OPEN,
        3,
        start=relay.pulses[2].sent_at + timedelta(milliseconds=1),
    )

    assert await task == OperationResult.SUCCESS
    assert len(relay.pulses) == 3


@pytest.mark.asyncio
async def test_exactly_three_failed_attempts() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await add(vision, GatePosition.CLOSED, 3)
    gate = controller(relay, vision)
    task = asyncio.create_task(gate.request_state(GateTarget.OPEN))
    for count in (1, 2, 3):
        await wait_for_pulses(relay, count)
        pulse = relay.pulses[count - 1]
        await add(vision, GatePosition.CLOSED, 3, start=pulse.sent_at + timedelta(milliseconds=1), step=3)
    assert await task == OperationResult.FAILED_AFTER_3_ATTEMPTS
    assert len(relay.pulses) == 3


@pytest.mark.asyncio
async def test_simultaneous_request_is_rejected() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await add(vision, GatePosition.CLOSED, 3)
    gate = controller(relay, vision)
    first = asyncio.create_task(gate.request_state(GateTarget.OPEN))
    await wait_for_pulses(relay, 1)
    with pytest.raises(OperationInProgressError):
        await gate.request_state(GateTarget.CLOSED)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)


def test_restart_constructs_idle_controller() -> None:
    gate = controller(FakeRelay(), RollingVision())
    assert gate.active is False
    assert gate.operation.state.value == "IDLE"
    assert gate.operation.attempt == 0


@pytest.mark.asyncio
async def test_manual_pulse_works_when_the_gate_state_is_unknown() -> None:
    """Refusing every command on unknown state leaves no way to work the gate."""
    vision, relay = RollingVision(), FakeRelay()
    gate = controller(relay, vision)
    assert await gate.request_state(GateTarget.OPEN) == OperationResult.STATE_UNKNOWN
    assert not relay.pulses

    assert await gate.manual_pulse(source="web_ui") == OperationResult.MANUAL_PULSE_SENT
    assert len(relay.pulses) == 1
    assert gate.operation.target is None      # a bare toggle claims no target
    assert gate.active is False


@pytest.mark.asyncio
async def test_manual_pulse_still_refuses_while_the_gate_is_moving() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await vision.add(Observation(GatePosition.PARTIALLY_OPEN, 0.9, is_moving=True))
    gate = controller(relay, vision)
    assert await gate.manual_pulse() == OperationResult.GATE_MOVING
    assert not relay.pulses


@pytest.mark.asyncio
async def test_manual_pulse_is_rejected_while_another_operation_runs() -> None:
    vision, relay = RollingVision(), FakeRelay()
    await add(vision, GatePosition.CLOSED, 3)
    gate = controller(relay, vision)
    first = asyncio.create_task(gate.request_state(GateTarget.OPEN))
    await wait_for_pulses(relay, 1)
    with pytest.raises(OperationInProgressError):
        await gate.manual_pulse()
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
