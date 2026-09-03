from __future__ import annotations

import cv2
import httpx
import numpy as np
import pytest

from app.homeassistant import HomeAssistantClient


@pytest.mark.asyncio
async def test_internal_camera_and_script_endpoints() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer supervisor-test"
        if request.method == "GET":
            image = np.zeros((1080, 1920, 3), dtype=np.uint8)
            ok, encoded = cv2.imencode(".jpg", image)
            assert ok
            return httpx.Response(200, content=encoded.tobytes(), headers={"content-type": "image/jpeg"})
        assert request.content == b'{"entity_id":"script.main_gate_relay_pulse"}'
        return httpx.Response(200, json=[])

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = HomeAssistantClient(
        "camera.garage_high_resolution_channel",
        "script.main_gate_relay_pulse",
        token="supervisor-test",
        client=async_client,
    )

    # A full-size camera frame is halved to cut CPU and memory on the gateway.
    assert (await client.camera_frame()).shape == (540, 960, 3)
    assert (await client.pulse_gate()).success is True
    assert calls == [
        ("GET", "/core/api/camera_proxy/camera.garage_high_resolution_channel"),
        ("POST", "/core/api/services/script/turn_on"),
    ]
    await async_client.aclose()

