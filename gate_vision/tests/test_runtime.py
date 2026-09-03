from __future__ import annotations

import json

import numpy as np
import pytest

from app.config import Settings
from app.models import CoverState
from app.mqtt_cover import (
    ATTRIBUTES_TOPIC,
    AVAILABILITY_TOPIC,
    DISCOVERY_TOPIC,
    MqttCover,
    PAYLOAD_UNKNOWN,
    STATE_TOPIC,
)
from app.web import _with_polygon


class Client:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.subscriptions: list[tuple[str, int]] = []

    def publish(self, topic: str, payload: str, **kwargs: object) -> None:
        self.messages.append((topic, payload))

    def subscribe(self, topic: str, qos: int) -> None:
        self.subscriptions.append((topic, qos))


def messages(client: Client, topic: str) -> list[str]:
    return [
        payload
        for published_topic, payload in client.messages
        if published_topic == topic
    ]


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


def test_debug_page_separates_home_assistant_state_from_diagnostics() -> None:
    html = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "app"
        / "templates"
        / "index.html"
    ).read_text(encoding="utf-8")
    assert "Camera" in html
    assert "Home Assistant state" in html
    assert "Vision state" in html
    assert "Controller target" in html
    assert "EVENT LOG" not in html
    assert "REFERENCES" not in html
    assert "safety device" not in html


def test_mqtt_discovery_creates_main_gate_cover() -> None:
    payload = MqttCover.discovery_payload()
    assert payload["object_id"] == "main_gate"
    assert payload["default_entity_id"] == "cover.main_gate"
    assert payload["device_class"] == "gate"
    assert payload["optimistic"] is False


def test_discovery_declares_no_icon() -> None:
    """Home Assistant owns presentation; the app never chooses an icon."""
    assert "icon" not in MqttCover.discovery_payload()
    assert "mdi:" not in json.dumps(MqttCover.discovery_payload())


def test_discovery_advertises_only_open_and_close() -> None:
    payload = MqttCover.discovery_payload()
    assert payload["payload_open"] == "OPEN"
    assert payload["payload_close"] == "CLOSE"
    # A toggle relay cannot halt travel and there is no position or tilt sensor.
    assert payload["payload_stop"] is None
    for unsupported in (
        "set_position_topic",
        "position_topic",
        "tilt_command_topic",
        "tilt_status_topic",
    ):
        assert unsupported not in payload


def test_discovery_maps_only_valid_home_assistant_cover_states() -> None:
    payload = MqttCover.discovery_payload()
    assert payload["state_open"] == CoverState.OPEN.value == "open"
    assert payload["state_closed"] == CoverState.CLOSED.value == "closed"
    assert payload["state_opening"] == CoverState.OPENING.value == "opening"
    assert payload["state_closing"] == CoverState.CLOSING.value == "closing"


@pytest.mark.parametrize(
    ("cover_state", "expected"),
    [
        ("closed", "closed"),
        ("open", "open"),
        ("opening", "opening"),
        ("closing", "closing"),
        ("unknown", PAYLOAD_UNKNOWN),
    ],
)
def test_state_payload_publishes_home_assistant_cover_states(
    cover_state: str,
    expected: str,
) -> None:
    assert MqttCover.state_payload({"cover_state": cover_state}) == expected


@pytest.mark.asyncio
async def test_state_is_published_without_buffering() -> None:
    """Opening must reach Home Assistant at once, not after a settling delay."""
    status = {"available": True, "cover_state": "closed"}
    mqtt = MqttCover(lambda: status)
    client = Client()
    mqtt._client = client

    await mqtt.publish()
    status["cover_state"] = "opening"
    await mqtt.publish()
    status["cover_state"] = "open"
    await mqtt.publish()

    assert messages(client, STATE_TOPIC) == ["closed", "opening", "open"]


@pytest.mark.asyncio
async def test_availability_follows_the_status_payload() -> None:
    status = {"available": True, "cover_state": "open"}
    mqtt = MqttCover(lambda: status)
    client = Client()
    mqtt._client = client

    await mqtt.publish()
    status["available"] = False
    await mqtt.publish()
    status["available"] = True
    await mqtt.publish()

    assert messages(client, AVAILABILITY_TOPIC) == ["online", "offline", "online"]


@pytest.mark.asyncio
async def test_diagnostics_travel_as_attributes_not_as_the_cover_state() -> None:
    status = {
        "available": True,
        "cover_state": "unknown",
        "vision_state": "PARTIAL",
        "fault": True,
        "fault_reason": "gate_stopped_mid_travel",
    }
    mqtt = MqttCover(lambda: status)
    client = Client()
    mqtt._client = client
    await mqtt.publish()

    assert messages(client, STATE_TOPIC) == [PAYLOAD_UNKNOWN]
    attributes = json.loads(messages(client, ATTRIBUTES_TOPIC)[-1])
    assert attributes["vision_state"] == "PARTIAL"
    assert attributes["fault"] is True
    assert attributes["fault_reason"] == "gate_stopped_mid_travel"


@pytest.mark.asyncio
async def test_connect_publishes_discovery_once() -> None:
    status = {"available": True, "cover_state": "closed"}
    mqtt = MqttCover(lambda: status)
    client = Client()
    mqtt._client = client
    mqtt._on_connect(client, None, None, 0, None)

    await mqtt.publish()
    status["cover_state"] = "opening"
    await mqtt.publish()

    assert len(messages(client, DISCOVERY_TOPIC)) == 1


def test_web_frame_draws_fixed_polygon() -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    annotated = _with_polygon(frame)
    assert annotated is not frame
    assert np.count_nonzero(annotated) > 0
