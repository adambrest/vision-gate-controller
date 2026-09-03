from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from typing import Any

from app.gate_controller import GateController, OperationInProgressError
from app.models import CoverState, GateTarget


LOGGER = logging.getLogger(__name__)
DISCOVERY_TOPIC = "homeassistant/cover/gate_vision_main_gate/config"
COMMAND_TOPIC = "gate_vision/main_gate/set"
STATE_TOPIC = "gate_vision/main_gate/state"
ATTRIBUTES_TOPIC = "gate_vision/main_gate/attributes"
AVAILABILITY_TOPIC = "gate_vision/status"

# Home Assistant's MQTT cover reads this payload as "state is unknown".
PAYLOAD_UNKNOWN = "None"


class MqttCover:
    """MQTT Discovery bridge for the single production cover entity.

    It transports state; it does not decide state, and it holds no opinion on
    how Home Assistant presents the entity.
    """

    def __init__(self, status_provider: Callable[[], dict[str, Any]]) -> None:
        self.status_provider = status_provider
        self.controller: GateController | None = None
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected: asyncio.Event | None = None

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
        client.publish(DISCOVERY_TOPIC, json.dumps(self.discovery_payload()), qos=1, retain=True)
        client.publish(
            AVAILABILITY_TOPIC,
            self._availability(self.status_provider()),
            qos=1,
            retain=True,
        )
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
        self._client.publish(
            AVAILABILITY_TOPIC,
            self._availability(status),
            qos=1,
            retain=True,
        )
        self._client.publish(STATE_TOPIC, self.state_payload(status), qos=1, retain=True)
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

    @staticmethod
    def state_payload(status: dict[str, Any]) -> str:
        state = str(status.get("cover_state", CoverState.UNKNOWN.value))
        if state == CoverState.UNKNOWN.value:
            return PAYLOAD_UNKNOWN
        return state

    @staticmethod
    def _availability(status: dict[str, Any]) -> str:
        return "online" if status.get("available") else "offline"

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
            # A toggle relay cannot halt travel, so STOP is not advertised.
            "payload_stop": None,
            "state_open": CoverState.OPEN.value,
            "state_closed": CoverState.CLOSED.value,
            "state_opening": CoverState.OPENING.value,
            "state_closing": CoverState.CLOSING.value,
            "optimistic": False,
            "qos": 1,
            "device": {
                "identifiers": ["gate_vision_main_gate"],
                "name": "Main Gate",
                "manufacturer": "Vision Gate Controller",
                "model": "Camera-verified toggle gate",
            },
        }
