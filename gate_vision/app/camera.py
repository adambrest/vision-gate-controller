from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from app.homeassistant import HomeAssistantClient


@dataclass(slots=True)
class CameraStatus:
    online: bool = False
    last_frame_at: datetime | None = None
    last_error: str | None = None


class Camera:
    def __init__(self, home_assistant: HomeAssistantClient) -> None:
        self.home_assistant = home_assistant
        self.status = CameraStatus()

    async def frame(self) -> np.ndarray:
        try:
            image = await self.home_assistant.camera_frame()
        except Exception as exc:
            self.status.online = False
            self.status.last_error = f"{type(exc).__name__}: {exc}"
            raise
        self.status.online = True
        self.status.last_frame_at = datetime.now(UTC)
        self.status.last_error = None
        return image

