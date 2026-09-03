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

STATE_SWITCH_DELAY_SECONDS = 3.0
DIRECTION_STATES = {"CLOSING", "OPENING"}
VALID_COVER_STATES = {"CLOSED", "CLOSING", "OPEN", "OPENING"}


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
        self._display_state: str | None = None
        self._pending_state: str | None = None
        self._pending_state_since: float | None = None
        self._display_availability: str | None = None
        self._offline_pending_since: float | None = None

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
        self._publish_discovery(client)
        availability = self._buffered_availability(status)
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
        availability = self._buffered_availability(status)
        buffered_state = self._buffered_state(status)
        state = buffered_state if buffered_state != "UNKNOWN" else "None"
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
        self._display_state = None
        self._pending_state = None
        self._pending_state_since = None
        self._display_availability = None
        self._offline_pending_since = None

    def _publish_discovery(self, client: Any) -> None:
        client.publish(
            DISCOVERY_TOPIC,
            json.dumps(self.discovery_payload()),
            qos=1,
            retain=True,
        )

    def _buffered_state(self, status: dict[str, Any]) -> str:
        """Hold cover state so Home Assistant's native gate icon cannot jitter."""
        candidate = str(status.get("cover_state", "UNKNOWN"))
        if candidate not in VALID_COVER_STATES:
            candidate = "UNKNOWN"
        now = self._clock()

        if self._display_state is None or candidate in DIRECTION_STATES:
            self._display_state = candidate
            self._pending_state = None
            self._pending_state_since = None
            return candidate

        if candidate == self._display_state:
            self._pending_state = None
            self._pending_state_since = None
            return self._display_state

        if candidate != self._pending_state:
            self._pending_state = candidate
            self._pending_state_since = now
            return self._display_state

        assert self._pending_state_since is not None
        if now - self._pending_state_since >= STATE_SWITCH_DELAY_SECONDS:
            self._display_state = candidate
            self._pending_state = None
            self._pending_state_since = None

        return self._display_state

    def _buffered_availability(self, status: dict[str, Any]) -> str:
        """Require a sustained outage before marking the cover unavailable."""
        candidate = "online" if status.get("available") else "offline"
        now = self._clock()

        if self._display_availability is None:
            self._display_availability = candidate
            return candidate
        if candidate == "online":
            self._display_availability = "online"
            self._offline_pending_since = None
            return "online"
        if self._display_availability == "offline":
            return "offline"
        if self._offline_pending_since is None:
            self._offline_pending_since = now
            return "online"
        if now - self._offline_pending_since >= STATE_SWITCH_DELAY_SECONDS:
            self._display_availability = "offline"
            self._offline_pending_since = None
        return self._display_availability

    @staticmethod
    def discovery_payload() -> dict[str, Any]:
        return {
            "name": "Main Gate",
            "unique_id": "gate_vision_main_gate",
            "object_id": "main_gate",
            "default_entity_id": "cover.main_gate",
            "device_class": "gate",
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
