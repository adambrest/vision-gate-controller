from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    camera_entity: str = "camera.garage_high_resolution_channel"
    gate_script: str = "script.main_gate_relay_pulse"
    snapshot_interval_seconds: float = 1.0
    moving_interval_seconds: float = 0.25
    moving_poll_hold_seconds: float = 2.0
    confirmation_window: int = 4
    confirmation_required: int = 3
    gate_travel_timeout_seconds: float = 35.0
    partial_stall_seconds: float = 10.0
    wrong_endpoint_seconds: float = 5.0
    max_gate_attempts: int = 3

    @classmethod
    def from_options(cls, path: Path = Path("/data/options.json")) -> "Settings":
        values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        # These rates are fixed. Discard stale values retained by older installs.
        values.pop("snapshot_interval_seconds", None)
        values.pop("moving_interval_seconds", None)
        settings = cls(**{key: value for key, value in values.items() if key in cls.__annotations__})
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.confirmation_required > self.confirmation_window:
            raise ValueError("confirmation_required cannot exceed confirmation_window")
        if self.max_gate_attempts != 3:
            raise ValueError("max_gate_attempts must be 3 (initial pulse plus two retries)")
        if self.moving_interval_seconds > self.snapshot_interval_seconds:
            raise ValueError("moving polling interval cannot exceed the normal interval")
