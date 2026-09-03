from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from app.gate_controller import GateController, OperationInProgressError
from app.models import GateTarget


LOGGER = logging.getLogger(__name__)
DISCOVERY_TOPIC = "homeassistant/cover/gate_vision_main_gate/config"
COMMAND_TOPIC = "gate_vision/main_gate/set"
STATE_TOPIC = "gate_vision/main_gate/state"
ATTRIBUTES_TOPIC = "gate_vision/main_gate/attributes"
AVAILABILITY_TOPIC = "gate_vision/status"

ALERT_ICON = "mdi:boom-gate-alert"
ICON_SWITCH_DELAY_SECONDS = 3.0
DIRECTION_ICONS = {
    "mdi:boom-gate-arrow-down",
    "mdi:boom-gate-arrow-up",
}
STATE_ICONS = {
    "CLOSED": "mdi:boom-gate",
    "OPEN": "mdi:boom-gate-up",
    "CLOSING": "mdi:boom-gate-arrow-down",
    "OPENING": "mdi:boom-gate-arrow-up",
    "PARTIAL": ALERT_ICON,
    "PARTIALLY_OPEN": ALERT_ICON,
    "UNKNOWN": ALERT_ICON,
}


class MqttCover:
    """MQTT Discovery bridge for the single production cover entity."""

    def __init__(
        self,
        status_provider: Callable[[], dict[str, Any]],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.status_provider = status_provider
        self._clock = clock
        self.controller: GateController | None = None
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected: asyncio.Event | None = None
        self._published_icon: str | None = None
        self._display_icon: str | None = None
        self._pending_icon: str | None = None
        self._pending_icon_since: float | None = None

    def set_controller(self, controller: GateController) -> None:
        self.controller = controller

    async def start(self) -> None:
        from paho.mqtt.client import CallbackAPIVersion, Client

        host = os.environ.get("MQTT_HOST", "")
        if not host:
            raise RuntimeError("MQTT service information is unavailable")
        port = int(os.environ.get("MQTT_PORT", "1883"))
        username = os.environ.get("MQTT_USERNAME") or None
        password = os.environ.get("MQTT_PASSWORD") or None
        self._loop = asyncio.get_running_loop()
        self._connected = asyncio.Event()
        client = Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id="gate-vision-main-gate",
        )
        if username:
            client.username_pw_set(username, password)
        client.will_set(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        self._client = client
        client.connect_async(host, port, keepalive=30)
        client.loop_start()
        await asyncio.wait_for(self._connected.wait(), timeout=15)

    def _on_connect(
        self,
        client: Any,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if reason_code != 0:
            LOGGER.error("MQTT connection failed: %s", reason_code)
            return
        client.subscribe(COMMAND_TOPIC, qos=1)
        status = self.status_provider()
        icon = self._buffered_icon(status)
        self._publish_discovery(client, icon)
        availability = "online" if status.get("available") else "offline"
        client.publish(AVAILABILITY_TOPIC, availability, qos=1, retain=True)
        if self._loop is not None and self._connected is not None:
            self._loop.call_soon_threadsafe(self._connected.set)
        LOGGER.info("MQTT cover discovery published")

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        if self._loop is None or self.controller is None:
            return
        payload = message.payload.decode("utf-8", errors="replace").strip().upper()
        if payload not in ("OPEN", "CLOSE"):
            LOGGER.warning("Ignoring unsupported cover command: %s", payload)
            return
        target = GateTarget.OPEN if payload == "OPEN" else GateTarget.CLOSED
        asyncio.run_coroutine_threadsafe(self._request(target), self._loop)

    async def _request(self, target: GateTarget) -> None:
        assert self.controller is not None
        try:
            await self.controller.request_state(target, source="home_assistant")
        except OperationInProgressError:
            LOGGER.warning("Ignoring command while another gate operation is active")
        except Exception:
            LOGGER.exception("Gate command failed")

    async def publish(self) -> None:
        if self._client is None:
            return
        status = self.status_provider()
        availability = "online" if status.get("available") else "offline"
        state = status["cover_state"] if status["cover_state"] != "UNKNOWN" else "None"
        icon = self._buffered_icon(status)
        if icon != self._published_icon:
            self._publish_discovery(self._client, icon)
        self._client.publish(AVAILABILITY_TOPIC, availability, qos=1, retain=True)
        self._client.publish(STATE_TOPIC, state, qos=1, retain=True)
        self._client.publish(
            ATTRIBUTES_TOPIC,
            json.dumps(status, separators=(",", ":")),
            qos=1,
            retain=True,
        )

    async def stop(self) -> None:
        if self._client is None:
            return
        self._client.publish(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)
        self._client.disconnect()
        self._client.loop_stop()
        self._client = None
        self._published_icon = None
        self._display_icon = None
        self._pending_icon = None
        self._pending_icon_since = None

    def _publish_discovery(self, client: Any, icon: str) -> None:
        client.publish(
            DISCOVERY_TOPIC,
            json.dumps(self.discovery_payload(icon)),
            qos=1,
            retain=True,
        )
        self._published_icon = icon

    @staticmethod
    def icon_for_status(status: dict[str, Any]) -> str:
        if not status.get("available"):
            return ALERT_ICON
        return STATE_ICONS.get(str(status.get("cover_state", "UNKNOWN")), ALERT_ICON)

    def _buffered_icon(self, status: dict[str, Any]) -> str:
        """Hold the current icon until a new non-direction state is stable."""
        candidate = self.icon_for_status(status)
        now = self._clock()

        if self._display_icon is None or candidate in DIRECTION_ICONS:
            self._display_icon = candidate
            self._pending_icon = None
            self._pending_icon_since = None
            return candidate

        if candidate == self._display_icon:
            self._pending_icon = None
            self._pending_icon_since = None
            return self._display_icon

        if candidate != self._pending_icon:
            self._pending_icon = candidate
            self._pending_icon_since = now
            return self._display_icon

        assert self._pending_icon_since is not None
        if now - self._pending_icon_since >= ICON_SWITCH_DELAY_SECONDS:
            self._display_icon = candidate
            self._pending_icon = None
            self._pending_icon_since = None

        return self._display_icon

    @staticmethod
    def discovery_payload(icon: str = ALERT_ICON) -> dict[str, Any]:
        return {
            "name": "Main Gate",
            "unique_id": "gate_vision_main_gate",
            "object_id": "main_gate",
            "default_entity_id": "cover.main_gate",
            "device_class": "gate",
            "icon": icon,
            "command_topic": COMMAND_TOPIC,
            "state_topic": STATE_TOPIC,
            "json_attributes_topic": ATTRIBUTES_TOPIC,
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_open": "OPEN",
            "payload_close": "CLOSE",
            "state_open": "OPEN",
            "state_closed": "CLOSED",
            "state_opening": "OPENING",
            "state_closing": "CLOSING",
            "optimistic": False,
            "qos": 1,
            "device": {
                "identifiers": ["gate_vision_main_gate"],
                "name": "Main Gate",
                "manufacturer": "Vision Gate Controller",
                "model": "Camera-verified toggle gate",
            },
        }
