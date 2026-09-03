from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class GatePosition(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PARTIALLY_OPEN = "PARTIALLY_OPEN"
    UNKNOWN = "UNKNOWN"


class GateTarget(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class CoverState(StrEnum):
    """The only cover states Home Assistant accepts for `cover.main_gate`.

    Home Assistant additionally renders the entity as `unavailable` on its own
    when availability is withdrawn, so there is no member for it here.
    """

    OPEN = "open"
    CLOSED = "closed"
    OPENING = "opening"
    CLOSING = "closing"
    UNKNOWN = "unknown"


class VisionState(StrEnum):
    """Internal diagnostic classification. Never a Home Assistant cover state."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PARTIAL = "PARTIAL"
    MOVING = "MOVING"
    UNKNOWN = "UNKNOWN"


class ControllerState(StrEnum):
    IDLE = "IDLE"
    VALIDATING_STATE = "VALIDATING_STATE"
    SENDING_COMMAND = "SENDING_COMMAND"
    WAITING_FOR_TARGET = "WAITING_FOR_TARGET"
    RETRY_PENDING = "RETRY_PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class OperationResult(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    ALREADY_AT_TARGET = "ALREADY_AT_TARGET"
    STATE_UNKNOWN = "STATE_UNKNOWN"
    GATE_MOVING = "GATE_MOVING"
    SUCCESS = "SUCCESS"
    RELAY_FAILED = "RELAY_FAILED"
    ABORTED_UNCERTAIN = "ABORTED_UNCERTAIN"
    FAILED_AFTER_3_ATTEMPTS = "FAILED_AFTER_3_ATTEMPTS"


@dataclass(frozen=True, slots=True)
class Observation:
    position: GatePosition
    confidence: float
    open_score: float = 0.0
    closed_score: float = 0.0
    partial_score: float = 0.0
    frame_change: float = 0.0
    is_moving: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    travel: float | None = None

    def payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["position"] = self.position.value
        payload["timestamp"] = self.timestamp.isoformat()
        return payload


@dataclass(slots=True)
class Operation:
    target: GateTarget | None = None
    source: str | None = None
    state: ControllerState = ControllerState.IDLE
    result: OperationResult | None = None
    attempt: int = 0
    max_attempts: int = 3
    status: str = "Idle"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_relay_pulse: datetime | None = None

    def payload(self) -> dict[str, object]:
        return {
            "target": self.target.value if self.target else None,
            "source": self.source,
            "controller_state": self.state.value,
            "result": self.result.value if self.result else None,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "last_relay_pulse": (
                self.last_relay_pulse.isoformat() if self.last_relay_pulse else None
            ),
        }

