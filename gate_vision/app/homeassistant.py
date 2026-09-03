from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import cv2
import httpx
import numpy as np

from app.vision import DECODE_FLAG


CORE_API = "http://supervisor/core/api"


@dataclass(frozen=True, slots=True)
class PulseResult:
    success: bool
    sent_at: datetime
    detail: str = ""


class HomeAssistantClient:
    """Internal-only Home Assistant camera and script client."""

    def __init__(
        self,
        camera_entity: str,
        gate_script: str,
        *,
        token: str | None = None,
        base_url: str = CORE_API,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        supervisor_token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        if not supervisor_token:
            raise RuntimeError("SUPERVISOR_TOKEN is unavailable")
        self.camera_entity = camera_entity
        self.gate_script = gate_script
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {supervisor_token}"}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def camera_frame(self) -> np.ndarray:
        entity = quote(self.camera_entity.strip(), safe=".")
        response = await self._client.get(
            f"{self._base_url}/camera_proxy/{entity}",
            headers=self._headers,
        )
        response.raise_for_status()
        encoded = np.frombuffer(response.content, dtype=np.uint8)
        # Half-size decode: a quarter of the pixels, and the gate polygon is
        # scaled from the frame dimensions so geometry is unaffected.
        frame = cv2.imdecode(encoded, DECODE_FLAG)
        if frame is None or frame.size == 0:
            raise ValueError("Home Assistant camera response is not a valid image")
        return frame

    async def pulse_gate(self) -> PulseResult:
        sent_at = datetime.now(UTC)
        try:
            response = await self._client.post(
                f"{self._base_url}/services/script/turn_on",
                headers={**self._headers, "Content-Type": "application/json"},
                json={"entity_id": self.gate_script},
            )
            response.raise_for_status()
            return PulseResult(True, sent_at)
        except httpx.HTTPError as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            detail = f"Home Assistant script request failed{f' ({status})' if status else ''}"
            return PulseResult(False, sent_at, detail)

    async def check_connection(self) -> None:
        response = await self._client.get(f"{self._base_url}/config", headers=self._headers)
        response.raise_for_status()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

