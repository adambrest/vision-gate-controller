from __future__ import annotations

import numpy as np
import pytest

from app.config import Settings
from app.mqtt_cover import ALERT_ICON, DISCOVERY_TOPIC, MqttCover
from app.web import _with_polygon


def test_web_ui_has_no_debug_mode_switch() -> None:
    assert "debug_mode" not in Settings.__annotations__
    assert Settings().snapshot_interval_seconds == 1.0
    assert Settings().moving_interval_seconds == 0.25


def test_stale_polling_options_are_ignored(tmp_path) -> None:
    options = tmp_path / "options.json"
    options.write_text(
        '{"snapshot_interval_seconds": 30, "moving_interval_seconds": 5}',
        encoding="utf-8",
    )
    settings = Settings.from_options(options)
    assert settings.snapshot_interval_seconds == 1.0
    assert settings.moving_interval_seconds == 0.25


def test_web_ui_is_barebones() -> None:
    html = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "app"
        / "templates"
        / "index.html"
    ).read_text(encoding="utf-8")
    assert "Camera" in html
    assert "Detected state" in html
    assert "Queued action" in html
    assert "EVENT LOG" not in html
    assert "REFERENCES" not in html
    assert "safety device" not in html


def test_mqtt_discovery_creates_main_gate_cover() -> None:
    payload = MqttCover.discovery_payload()
    assert payload["object_id"] == "main_gate"
    assert payload["default_entity_id"] == "cover.main_gate"
    assert payload["device_class"] == "gate"
    assert payload["icon"] == ALERT_ICON
    assert payload["optimistic"] is False


@pytest.mark.parametrize(
    ("available", "cover_state", "icon"),
    [
        (True, "CLOSED", "mdi:boom-gate"),
        (True, "OPEN", "mdi:boom-gate-up"),
        (True, "CLOSING", "mdi:boom-gate-arrow-down"),
        (True, "OPENING", "mdi:boom-gate-arrow-up"),
        (True, "PARTIAL", ALERT_ICON),
        (True, "PARTIALLY_OPEN", ALERT_ICON),
        (True, "UNKNOWN", ALERT_ICON),
        (False, "OPEN", ALERT_ICON),
    ],
)
def test_main_gate_icon_tracks_trusted_cover_state(
    available: bool,
    cover_state: str,
    icon: str,
) -> None:
    assert MqttCover.icon_for_status(
        {"available": available, "cover_state": cover_state}
    ) == icon


@pytest.mark.asyncio
async def test_mqtt_discovery_is_updated_only_when_icon_changes() -> None:
    class Client:
        def __init__(self) -> None:
            self.messages: list[tuple[str, str]] = []

        def publish(self, topic: str, payload: str, **kwargs: object) -> None:
            self.messages.append((topic, payload))

    status = {"available": True, "cover_state": "CLOSED"}
    mqtt = MqttCover(lambda: status)
    client = Client()
    mqtt._client = client

    await mqtt.publish()
    await mqtt.publish()
    status["cover_state"] = "OPENING"
    await mqtt.publish()

    discovery_messages = [
        payload for topic, payload in client.messages if topic == DISCOVERY_TOPIC
    ]
    assert len(discovery_messages) == 2
    assert '"icon": "mdi:boom-gate"' in discovery_messages[0]
    assert '"icon": "mdi:boom-gate-arrow-up"' in discovery_messages[1]


def test_web_frame_draws_fixed_polygon() -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    annotated = _with_polygon(frame)
    assert annotated is not frame
    assert np.count_nonzero(annotated) > 0
