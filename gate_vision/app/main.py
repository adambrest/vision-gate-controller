from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.camera import Camera
from app.config import Settings
from app.cover_state import (
    Availability,
    get_effective_cover_state,
    get_fault,
    get_vision_state,
)
from app.gate_controller import GateController, OperationInProgressError
from app.homeassistant import HomeAssistantClient
from app.models import GatePosition, GateTarget, Observation, OperationResult
from app.mqtt_cover import MqttCover
from app.vision import ReferenceClassifier, RollingVision, set_gate_polygon


LOGGER = logging.getLogger(__name__)


class Runtime:
    def __init__(
        self,
        settings: Settings,
        camera: Camera,
        classifier: ReferenceClassifier,
        vision: RollingVision,
    ) -> None:
        self.settings = settings
        self.camera = camera
        self.classifier = classifier
        self.vision = vision
        self.controller: GateController | None = None
        self.mqtt: MqttCover | None = None
        self.latest_observation = Observation(GatePosition.UNKNOWN, 0.0)
        self.confirmed_state = GatePosition.UNKNOWN
        self.confirmation_current = False
        self.availability = Availability(settings.camera_unavailable_after_seconds)
        self.latest_frame: np.ndarray | None = None
        self.latest_region: np.ndarray | None = None
        self.active_poll_interval = settings.snapshot_interval_seconds
        self.measured_poll_hz = 0.0
        self._completed_frame_times: deque[float] = deque(maxlen=9)
        self._request_tasks: set[asyncio.Task[Any]] = set()
        self._last_logged_position: GatePosition | None = None
        self._last_logged_motion: bool | None = None

    def attach(self, controller: GateController, mqtt: MqttCover) -> None:
        self.controller = controller
        self.mqtt = mqtt

    def start_request(self, target: GateTarget, *, source: str) -> None:
        assert self.controller is not None
        self._track(self.controller.request_state(target, source=source))

    def start_manual_pulse(self, *, source: str) -> None:
        assert self.controller is not None
        self._track(self.controller.manual_pulse(source=source))

    def _track(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._request_tasks.add(task)
        task.add_done_callback(self._request_done)

    def _request_done(self, task: asyncio.Task[Any]) -> None:
        self._request_tasks.discard(task)
        try:
            task.result()
        except OperationInProgressError:
            LOGGER.warning("Gate command rejected because another operation is active")
        except Exception:
            LOGGER.exception("Gate command task failed")

    async def publish_status(self) -> None:
        if self.mqtt is not None:
            await self.mqtt.publish()

    def effective_cover_state(self) -> str:
        """The one place the Home Assistant cover state is decided."""
        controller = self.controller
        return get_effective_cover_state(
            active_target=controller.requested_target if controller is not None else None,
            confirmed=self.confirmed_state,
            confirmation_current=self.confirmation_current,
        ).value

    def update_availability(self) -> bool:
        """Availability is tracked apart from gate position and only here."""
        return self.availability.update(
            camera_online=self.camera.status.online,
            monitoring_ready=self.classifier.ready,
        )

    def status_payload(self) -> dict[str, Any]:
        controller = self.controller
        camera_time = self.camera.status.last_frame_at
        operation = controller.operation.payload() if controller is not None else {}
        active = controller.active if controller is not None else False
        last_result = controller.operation.result if controller is not None else None
        vision_state = get_vision_state(
            latest=self.latest_observation,
            confirmed=self.confirmed_state,
            confirmation_current=self.confirmation_current,
        )
        fault_reason = get_fault(
            available=self.availability.available,
            availability_reason=self.availability.reason,
            controller_active=active,
            vision_state=vision_state,
            last_result=last_result if last_result is not OperationResult.IN_PROGRESS else None,
        )
        snapshot_observations: list[dict[str, object]] = []
        # The camera loop stores this compact copy for synchronous MQTT/web access.
        if hasattr(self, "recent_observations"):
            snapshot_observations = [item.payload() for item in self.recent_observations[-6:]]
        return {
            # The Home Assistant cover state. Only open/closed/opening/closing/unknown.
            "cover_state": self.effective_cover_state(),
            "available": self.availability.available,
            "availability_reason": self.availability.reason,
            # Diagnostics below. These are never Home Assistant cover states.
            "vision_state": vision_state.value,
            "confirmed_state": self.confirmed_state.value,
            "confirmation_current": self.confirmation_current,
            "raw_state": self.latest_observation.position.value,
            "confidence": round(self.latest_observation.confidence, 4),
            "travel": (
                round(self.latest_observation.travel, 4)
                if self.latest_observation.travel is not None
                else None
            ),
            "fault": fault_reason is not None,
            "fault_reason": fault_reason,
            "open_score": round(self.latest_observation.open_score, 4),
            "closed_score": round(self.latest_observation.closed_score, 4),
            "partial_score": round(self.latest_observation.partial_score, 4),
            "is_moving": self.latest_observation.is_moving,
            "frame_change": round(self.latest_observation.frame_change, 4),
            "last_camera_frame_time": camera_time.isoformat() if camera_time else None,
            "camera_online": self.camera.status.online,
            "reference_count": self.classifier.count,
            "reference_files": list(self.classifier.reference_files),
            "target_state": operation.get("target") if active else None,
            "current_requested_target": operation.get("target") if active else None,
            "controller_status": operation.get("status", "Idle"),
            "controller_state": operation.get("controller_state", "IDLE"),
            "controller_active": active,
            "current_attempt": operation.get("attempt", 0),
            "last_result": operation.get("result"),
            "last_relay_pulse": operation.get("last_relay_pulse"),
            "poll_interval_seconds": self.active_poll_interval,
            "target_poll_hz": round(1.0 / self.active_poll_interval, 2),
            "measured_poll_hz": round(self.measured_poll_hz, 2),
            "recent_observations": snapshot_observations,
            "events": list(controller.events)[-20:] if controller is not None else [],
        }

    async def camera_loop(self) -> None:
        loop = asyncio.get_running_loop()
        last_sample_at: float | None = None
        fast_until = 0.0
        was_online: bool | None = None
        while True:
            cycle_started = loop.time()
            sample_interval = (
                cycle_started - last_sample_at
                if last_sample_at is not None
                else self.settings.snapshot_interval_seconds
            )
            try:
                frame = await self.camera.frame()
                timestamp = self.camera.status.last_frame_at or datetime.now(UTC)
                observation, region = await asyncio.to_thread(
                    self.classifier.observe,
                    frame,
                    sample_interval_seconds=sample_interval,
                    timestamp=timestamp,
                )
                snapshot = await self.vision.add(observation)
                self.latest_frame = frame
                self.latest_region = region
                self.latest_observation = observation
                self.confirmed_state = snapshot.confirmed
                self.confirmation_current = snapshot.confirmation_current
                self.recent_observations = snapshot.observations
                frame_completed_at = loop.time()
                last_sample_at = cycle_started
                self._completed_frame_times.append(frame_completed_at)
                if len(self._completed_frame_times) >= 2:
                    elapsed = self._completed_frame_times[-1] - self._completed_frame_times[0]
                    if elapsed > 0:
                        self.measured_poll_hz = (len(self._completed_frame_times) - 1) / elapsed
                if was_online is not True:
                    LOGGER.info("Camera online")
                was_online = True
                self._log_vision_change(observation, snapshot.confirmed)
            except Exception as exc:
                if was_online is not False:
                    LOGGER.warning("Camera offline: %s", exc)
                was_online = False

            self.update_availability()
            now = loop.time()
            if self.latest_observation.is_moving:
                fast_until = now + self.settings.moving_poll_hold_seconds
            if now < fast_until:
                interval = self.settings.moving_interval_seconds
            else:
                interval = self.settings.snapshot_interval_seconds
            self.active_poll_interval = interval
            await self.publish_status()
            await asyncio.sleep(max(0.0, interval - (loop.time() - cycle_started)))

    def _log_vision_change(self, observation: Observation, confirmed: GatePosition) -> None:
        if observation.position != self._last_logged_position:
            LOGGER.info(
                "Camera sees %s (%.0f%%)",
                observation.position.value,
                observation.confidence * 100,
            )
            self._last_logged_position = observation.position
        if observation.is_moving != self._last_logged_motion:
            LOGGER.info("Gate motion %s", "detected" if observation.is_moving else "stopped")
            self._last_logged_motion = observation.is_moving

    async def shutdown(self) -> None:
        for task in tuple(self._request_tasks):
            task.cancel()
        if self._request_tasks:
            await asyncio.gather(*self._request_tasks, return_exceptions=True)


def web_server_task(runtime: Runtime) -> asyncio.Task[Any]:
    import uvicorn

    from app.web import create_web_app

    server = uvicorn.Server(
        uvicorn.Config(
            create_web_app(runtime),
            host="0.0.0.0",
            port=8080,
            log_level="warning",
            access_log=False,
        )
    )
    return asyncio.create_task(server.serve(), name="gate-vision-web")


async def run() -> None:
    settings = Settings.from_options()
    set_gate_polygon(settings.polygon_points())
    home_assistant = HomeAssistantClient(settings.camera_entity, settings.gate_script)
    camera = Camera(home_assistant)
    classifier = ReferenceClassifier(
        Path("/app/references"),
        endpoint_travel_tolerance=settings.endpoint_travel_tolerance,
        endpoint_travel_veto=settings.endpoint_travel_veto,
        endpoint_travel_corroboration=settings.endpoint_travel_corroboration,
    )
    vision = RollingVision(settings.confirmation_window, settings.confirmation_required)
    runtime = Runtime(settings, camera, classifier, vision)
    mqtt = MqttCover(runtime.status_payload)
    controller = GateController(
        home_assistant,
        vision,
        travel_timeout_seconds=settings.gate_travel_timeout_seconds,
        partial_stall_seconds=settings.partial_stall_seconds,
        wrong_endpoint_seconds=settings.wrong_endpoint_seconds,
        max_attempts=settings.max_gate_attempts,
        on_change=runtime.publish_status,
    )
    runtime.attach(controller, mqtt)
    mqtt.set_controller(controller)
    LOGGER.info("Vision Gate Controller started")
    await home_assistant.check_connection()
    await mqtt.start()
    await mqtt.publish()
    camera_task = asyncio.create_task(runtime.camera_loop(), name="gate-camera-worker")
    web_task = web_server_task(runtime)
    tasks = [camera_task, web_task]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.shutdown()
        await mqtt.stop()
        await home_assistant.close()


def main() -> None:
    # OpenCV defaults to one thread per core and thrashes on a four-core box for
    # frames this small. Two threads measurably lowers CPU at the same frame rate.
    cv2.setNumThreads(2)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception:
        LOGGER.exception("Vision Gate Controller stopped due to a fatal error")
        raise


if __name__ == "__main__":
    main()
