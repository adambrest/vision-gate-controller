"""The single source of truth for what Home Assistant is told about the gate.

Gate Vision owns physical state, vision classification, controller state, and
command verification. Presentation - dashboard icons, icon templates, HomeKit -
belongs to Home Assistant, so nothing here decides how the entity looks.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from app.models import (
    CoverState,
    GatePosition,
    GateTarget,
    Observation,
    OperationResult,
    VisionState,
)


FAULT_REASONS: dict[OperationResult, str] = {
    OperationResult.RELAY_FAILED: "relay_command_failed",
    OperationResult.FAILED_AFTER_3_ATTEMPTS: "target_not_reached_after_three_attempts",
    OperationResult.ABORTED_UNCERTAIN: "camera_evidence_uncertain",
    OperationResult.GATE_MOVING: "gate_moving_when_commanded",
    OperationResult.STATE_UNKNOWN: "gate_state_unknown_when_commanded",
}
# A manual pulse is a deliberate override, not a fault.
NON_FAULT_RESULTS = {OperationResult.MANUAL_PULSE_SENT, OperationResult.ALREADY_AT_TARGET}


def get_effective_cover_state(
    *,
    active_target: GateTarget | None,
    confirmed: GatePosition,
    confirmation_current: bool,
) -> CoverState:
    """Resolve the Home Assistant cover state.

    `active_target` is the target of an operation that is still running, so an
    accepted OPEN or CLOSE reports `opening`/`closing` before the next camera
    frame arrives. Everything else follows rolling camera confirmation, and a
    confirmation the rolling window no longer supports is never reused.
    """
    if active_target is GateTarget.OPEN:
        return CoverState.OPENING
    if active_target is GateTarget.CLOSED:
        return CoverState.CLOSING
    if confirmation_current:
        if confirmed is GatePosition.OPEN:
            return CoverState.OPEN
        if confirmed is GatePosition.CLOSED:
            return CoverState.CLOSED
    return CoverState.UNKNOWN


def get_vision_state(
    *,
    latest: Observation,
    confirmed: GatePosition,
    confirmation_current: bool,
) -> VisionState:
    """Richer internal classification for diagnostics and the debug page."""
    if latest.is_moving:
        return VisionState.MOVING
    if confirmation_current:
        if confirmed is GatePosition.OPEN:
            return VisionState.OPEN
        if confirmed is GatePosition.CLOSED:
            return VisionState.CLOSED
    if latest.position is GatePosition.PARTIALLY_OPEN:
        return VisionState.PARTIAL
    return VisionState.UNKNOWN


def get_fault(
    *,
    available: bool,
    availability_reason: str | None,
    controller_active: bool,
    vision_state: VisionState,
    last_result: OperationResult | None,
) -> str | None:
    """Diagnostic fault reason, or None. Never a Home Assistant cover state."""
    if not available:
        return availability_reason or "unavailable"
    if controller_active:
        return None
    if vision_state is VisionState.PARTIAL:
        return "gate_stopped_mid_travel"
    if vision_state in (VisionState.OPEN, VisionState.CLOSED):
        # The gate reached a confident endpoint, so an older failure is spent.
        return None
    if last_result is None or last_result in NON_FAULT_RESULTS:
        return None
    return FAULT_REASONS.get(last_result)


@dataclass(slots=True)
class Availability:
    """Availability of `cover.main_gate`, tracked apart from gate position.

    A single dropped camera frame must not withdraw the entity, so camera
    failure has to persist for `grace_seconds` before availability is lost.
    A dependency that stops gate-state monitoring outright withdraws it at once.
    """

    grace_seconds: float
    clock: Callable[[], float] = time.monotonic
    available: bool = True
    reason: str | None = None
    _failing_since: float | None = None

    def update(self, *, camera_online: bool, monitoring_ready: bool) -> bool:
        if not monitoring_ready:
            self._failing_since = None
            return self._set(False, "gate_state_monitoring_unavailable")
        if camera_online:
            self._failing_since = None
            return self._set(True, None)
        now = self.clock()
        if self._failing_since is None:
            self._failing_since = now
        if now - self._failing_since >= self.grace_seconds:
            return self._set(False, "camera_offline")
        return self.available

    def _set(self, available: bool, reason: str | None) -> bool:
        self.available = available
        self.reason = reason
        return available
