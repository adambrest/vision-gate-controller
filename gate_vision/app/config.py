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
    camera_unavailable_after_seconds: float = 15.0
    endpoint_travel_tolerance: float = 0.08
    endpoint_travel_veto: bool = False
    endpoint_travel_corroboration: bool = True
    # OPEN needs the columns beside the parked leaf to match an open gate this
    # well. 0 disables the check.
    open_clearance_minimum: float = 0.50
    # Corridor corners as "x,y" pairs in 1280x720 camera coordinates: the gate
    # travel corridor, ordered top-left, top-right, bottom-right, bottom-left.
    gate_polygon: str = "326.3,296.7 616.7,391.3 611.2,502.4 320.8,407.8"

    @classmethod
    def from_options(cls, path: Path = Path("/data/options.json")) -> "Settings":
        values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        # These rates are fixed. Discard stale values retained by older installs.
        values.pop("snapshot_interval_seconds", None)
        values.pop("moving_interval_seconds", None)
        settings = cls(**{key: value for key, value in values.items() if key in cls.__annotations__})
        settings.validate()
        return settings

    def polygon_points(self) -> dict[str, tuple[float, float]]:
        names = ("top_left", "top_right", "bottom_right", "bottom_left")
        parts = self.gate_polygon.split()
        if len(parts) != 4:
            raise ValueError("gate_polygon needs four \"x,y\" corners")
        points: dict[str, tuple[float, float]] = {}
        for name, part in zip(names, parts):
            try:
                x, y = (float(value) for value in part.split(","))
            except ValueError as exc:
                raise ValueError(f"gate_polygon corner {name} is not \"x,y\": {part}") from exc
            points[name] = (x, y)
        return points

    def validate(self) -> None:
        self.polygon_points()
        if self.confirmation_required > self.confirmation_window:
            raise ValueError("confirmation_required cannot exceed confirmation_window")
        if self.max_gate_attempts != 3:
            raise ValueError("max_gate_attempts must be 3 (initial pulse plus two retries)")
        if self.moving_interval_seconds > self.snapshot_interval_seconds:
            raise ValueError("moving polling interval cannot exceed the normal interval")
        if not 0.0 < self.endpoint_travel_tolerance < 0.5:
            raise ValueError("endpoint_travel_tolerance must be between 0 and 0.5")
        if not 0.0 <= self.open_clearance_minimum < 1.0:
            raise ValueError("open_clearance_minimum must be between 0 and 1")
        if self.camera_unavailable_after_seconds < self.snapshot_interval_seconds:
            raise ValueError(
                "camera_unavailable_after_seconds must outlast a single camera frame"
            )
