from __future__ import annotations

import numpy as np
import pytest

from app.config import Settings
from app.mqtt_cover import (
    AVAILABILITY_TOPIC,
    DISCOVERY_TOPIC,
    MqttCover,
    STATE_SWITCH_DELAY_SECONDS,
    STATE_TOPIC,
)
from app.web import _with_polygon


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


class Client:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.subscriptions: list[tuple[str, int]] = []

    def publish(self, topic: str, payload: str, **kwargs: object) -> None:
        self.messages.append((topic, payload))

    def subscribe(self, topic: str, qos: int) -> None:
        self.subscriptions.append((topic, qos))


def messages(client: Client, topic: str) -> list[str]:
    return [payload for published_topic, payload in client.messages if published_topic == topic]


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
    assert "icon" not in payload
    assert payload["optimistic"] is False


@pytest.mark.asyncio
async def test_mqtt_discovery_is_static_while_state_changes() -> None:
    status = {"available": True, "cover_state": "CLOSED"}
    mqtt = MqttCover(lambda: status)
    client = Client()
    mqtt._client = client
    mqtt._on_connect(client, None, None, 0, None)

    await mqtt.publish()
    status["cover_state"] = "OPENING"
    await mqtt.publish()

    assert len(messages(client, DISCOVERY_TOPIC)) == 1
    assert messages(client, STATE_TOPIC)[-1] == "OPENING"


@pytest.mark.asyncio
async def test_direction_state_is_held_through_brief_vision_hiccups() -> None:
    clock = Clock()
    status = {"available": True, "cover_state": "OPEN"}
    mqtt = MqttCover(lambda: status, clock=clock)
    client = Client()
    mqtt._client = client

    await mqtt.publish()
    status["cover_state"] = "CLOSING"
    await mqtt.publish()

    for elapsed, outlier in ((0.5, "CLOSED"), (1.0, "OPEN"), (1.5, "UNKNOWN")):
        clock.now = elapsed
        status["cover_state"] = outlier
        await mqtt.publish()

    assert messages(client, STATE_TOPIC) == [
        "OPEN",
        "CLOSING",
        "CLOSING",
        "CLOSING",
        "CLOSING",
    ]

    clock.now += STATE_SWITCH_DELAY_SECONDS - 0.1
    await mqtt.publish()
    assert messages(client, STATE_TOPIC)[-1] == "CLOSING"

    clock.now += 0.11
    await mqtt.publish()
    assert messages(client, STATE_TOPIC)[-1] == "None"


@pytest.mark.asyncio
async def test_terminal_state_requires_a_stable_buffer_after_closing() -> None:
    clock = Clock()
    status = {"available": True, "cover_state": "CLOSING"}
    mqtt = MqttCover(lambda: status, clock=clock)
    client = Client()
    mqtt._client = client

    await mqtt.publish()
    status["cover_state"] = "CLOSED"
    await mqtt.publish()
    clock.now = STATE_SWITCH_DELAY_SECONDS
    await mqtt.publish()

    assert messages(client, STATE_TOPIC) == ["CLOSING", "CLOSING", "CLOSED"]


@pytest.mark.asyncio
async def test_brief_camera_dropout_does_not_mark_cover_unavailable() -> None:
    clock = Clock()
    status = {"available": True, "cover_state": "OPEN"}
    mqtt = MqttCover(lambda: status, clock=clock)
    client = Client()
    mqtt._client = client

    await mqtt.publish()
    status["available"] = False
    await mqtt.publish()
    clock.now = STATE_SWITCH_DELAY_SECONDS - 0.1
    await mqtt.publish()
    assert messages(client, AVAILABILITY_TOPIC) == ["online", "online", "online"]

    status["available"] = True
    await mqtt.publish()
    status["available"] = False
    clock.now = 4.0
    await mqtt.publish()
    clock.now = 4.0 + STATE_SWITCH_DELAY_SECONDS
    await mqtt.publish()
    assert messages(client, AVAILABILITY_TOPIC)[-1] == "offline"


def test_web_frame_draws_fixed_polygon() -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    annotated = _with_polygon(frame)
    assert annotated is not frame
    assert np.count_nonzero(annotated) > 0
