from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.models import GateTarget
from app.vision import GATE_POLYGON, REFERENCE_RESOLUTION


def create_web_app(runtime: Any) -> FastAPI:
    app = FastAPI(title="Vision Gate Controller")
    root = Path(__file__).resolve().parent
    app.mount("/static", StaticFiles(directory=root / "static"), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(root / "templates" / "index.html")

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return runtime.status_payload()

    @app.get("/api/frame")
    async def frame() -> Response:
        return _jpeg(_with_polygon(runtime.latest_frame))

    @app.get("/api/region")
    async def region() -> Response:
        return _jpeg(runtime.latest_region)

    @app.post("/api/request/{target}", status_code=202)
    async def request(target: GateTarget) -> dict[str, bool]:
        state = runtime.status_payload()
        if not state["available"]:
            raise HTTPException(status_code=409, detail="Camera vision is unavailable")
        if state["is_moving"]:
            raise HTTPException(status_code=409, detail="Gate is moving; controls are locked")
        if runtime.controller is not None and runtime.controller.active:
            raise HTTPException(status_code=409, detail="A gate operation is already active")
        runtime.start_request(target, source="web_ui")
        return {"accepted": True}

    @app.post("/api/pulse", status_code=202)
    async def pulse() -> dict[str, bool]:
        """Deliberate override: one relay pulse, no target, no verification."""
        state = runtime.status_payload()
        if state["is_moving"]:
            raise HTTPException(status_code=409, detail="Gate is moving; controls are locked")
        if runtime.controller is not None and runtime.controller.active:
            raise HTTPException(status_code=409, detail="A gate operation is already active")
        runtime.start_manual_pulse(source="web_ui")
        return {"accepted": True}

    return app


def _jpeg(image: Any) -> Response:
    if image is None:
        raise HTTPException(status_code=503, detail="No camera frame is available")
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise HTTPException(status_code=500, detail="Image encoding failed")
    return Response(
        encoded.tobytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


def _with_polygon(image: Any) -> Any:
    if image is None:
        return None
    annotated = image.copy()
    height, width = annotated.shape[:2]
    scale_x = width / REFERENCE_RESOLUTION[0]
    scale_y = height / REFERENCE_RESOLUTION[1]
    points = np.asarray(
        [[round(x * scale_x), round(y * scale_y)] for x, y in GATE_POLYGON],
        dtype=np.int32,
    )
    cv2.fillPoly(annotated, [points], (34, 94, 76))
    cv2.addWeighted(annotated, 0.18, image, 0.82, 0, annotated)
    cv2.polylines(annotated, [points], True, (165, 235, 188), 3, cv2.LINE_AA)
    return annotated
