from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from app.models import GatePosition, Observation


LOGGER = logging.getLogger(__name__)
REFERENCE_RESOLUTION = (1280, 720)
GATE_POINTS = {
    "top_left": (317.0, 268.0),
    "bottom_left": (310.0, 407.0),
    "top_right": (639.0, 384.0),
    "bottom_right": (600.0, 550.0),
}
GATE_POLYGON = (
    GATE_POINTS["top_left"],
    GATE_POINTS["top_right"],
    GATE_POINTS["bottom_right"],
    GATE_POINTS["bottom_left"],
)
CLASSIFIER_SIZE = (160, 96)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def scaled_gate_points(width: int, height: int) -> dict[str, tuple[float, float]]:
    if width <= 0 or height <= 0:
        raise ValueError("frame dimensions must be positive")
    scale_x = width / REFERENCE_RESOLUTION[0]
    scale_y = height / REFERENCE_RESOLUTION[1]
    return {
        name: (point[0] * scale_x, point[1] * scale_y)
        for name, point in GATE_POINTS.items()
    }


def extract_gate_region(frame: np.ndarray) -> np.ndarray:
    if frame.ndim not in (2, 3) or frame.size == 0:
        raise ValueError("expected a non-empty camera frame")
    height, width = frame.shape[:2]
    points = scaled_gate_points(width, height)
    top_left = np.asarray(points["top_left"], dtype=np.float32)
    bottom_left = np.asarray(points["bottom_left"], dtype=np.float32)
    top_right = np.asarray(points["top_right"], dtype=np.float32)
    bottom_right = np.asarray(points["bottom_right"], dtype=np.float32)
    output_width = max(
        1,
        round(max(np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left))),
    )
    output_height = max(
        1,
        round(max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right))),
    )
    source = np.asarray(
        [top_left, top_right, bottom_right, bottom_left],
        dtype=np.float32,
    )
    destination = np.asarray(
        [
            (0, 0),
            (output_width - 1, 0),
            (output_width - 1, output_height - 1),
            (0, output_height - 1),
        ],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(source, destination)
    return cv2.warpPerspective(frame, transform, (output_width, output_height))


def preprocess(region: np.ndarray) -> np.ndarray:
    canvas_width, canvas_height = CLASSIFIER_SIZE
    source_height, source_width = region.shape[:2]
    scale = min(canvas_width / source_width, canvas_height / source_height)
    width = max(1, round(source_width * scale))
    height = max(1, round(source_height * scale))
    resized = cv2.resize(region, (width, height), interpolation=cv2.INTER_AREA)
    gray = resized if resized.ndim == 2 else cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    canvas = np.zeros((canvas_height, canvas_width), dtype=np.uint8)
    x = (canvas_width - width) // 2
    y = (canvas_height - height) // 2
    canvas[y : y + height, x : x + width] = gray
    normalized = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(canvas)
    return cv2.GaussianBlur(normalized, (3, 3), 0)


class ReferenceClassifier:
    """Small HOG/layout matcher that uses every labeled reference file."""

    _hog = cv2.HOGDescriptor(CLASSIFIER_SIZE, (16, 16), (8, 8), (8, 8), 9)

    def __init__(self, reference_root: Path, expected_count: int | None = None) -> None:
        self.reference_root = reference_root
        self.expected_count = expected_count
        self.features: dict[GatePosition, list[np.ndarray]] = {
            GatePosition.OPEN: [],
            GatePosition.CLOSED: [],
        }
        self.reference_files: tuple[str, ...] = ()
        self.previous: np.ndarray | None = None
        self.reload()

    @property
    def count(self) -> int:
        return sum(len(items) for items in self.features.values())

    @property
    def ready(self) -> bool:
        return (
            bool(self.features[GatePosition.OPEN])
            and bool(self.features[GatePosition.CLOSED])
        )

    def reload(self) -> None:
        loaded = {GatePosition.OPEN: [], GatePosition.CLOSED: []}
        filenames: list[str] = []
        for position in (GatePosition.OPEN, GatePosition.CLOSED):
            directory = self.reference_root / position.value.lower()
            if not directory.exists():
                continue
            for path in sorted(directory.iterdir()):
                if path.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if frame is None or frame.size == 0:
                    LOGGER.error("Unreadable reference: %s", path.name)
                    continue
                loaded[position].append(self._feature(extract_gate_region(frame)))
                filenames.append(f"{position.value.lower()}/{path.name}")
        self.features = loaded
        self.reference_files = tuple(filenames)
        self.previous = None
        for filename in self.reference_files:
            LOGGER.info("Reference loaded: %s", filename)
        if self.expected_count is not None and self.count != self.expected_count:
            LOGGER.warning(
                "Reference count differs from expected inventory: expected %d, loaded %d",
                self.expected_count,
                self.count,
            )
        if not self.ready:
            LOGGER.error(
                "Reference set invalid: at least one OPEN and one CLOSED image are required"
            )

    def observe(
        self,
        frame: np.ndarray,
        *,
        sample_interval_seconds: float,
        timestamp: datetime,
    ) -> tuple[Observation, np.ndarray]:
        region = extract_gate_region(frame)
        processed = preprocess(region)
        feature = self._feature_processed(processed)
        previous, self.previous = self.previous, processed
        difference = (
            float(cv2.absdiff(processed, previous).mean()) if previous is not None else 0.0
        )
        movement_threshold = max(
            1.5,
            8.0 * min(max(sample_interval_seconds, 0.1), 1.5),
        )
        is_moving = previous is not None and difference >= movement_threshold
        frame_change = min(1.0, difference / 20.0)
        if not self.ready:
            return Observation(
                GatePosition.UNKNOWN,
                0.0,
                frame_change=frame_change,
                is_moving=is_moving,
                timestamp=timestamp,
            ), region

        open_similarity = self._similarity(feature, self.features[GatePosition.OPEN])
        closed_similarity = self._similarity(feature, self.features[GatePosition.CLOSED])
        logits = np.asarray([open_similarity, closed_similarity], dtype=np.float64) * 30.0
        logits -= logits.max()
        probabilities = np.exp(logits)
        probabilities /= probabilities.sum()
        open_score, closed_score = (float(value) for value in probabilities)
        nearest = max(open_similarity, closed_similarity)
        margin = abs(open_similarity - closed_similarity)
        endpoint_confidence = float(np.clip((margin - 0.10) / 0.18, 0.0, 1.0))
        partial_score = 1.0 - endpoint_confidence

        if open_score >= 0.82 and open_similarity >= 0.58:
            position = GatePosition.OPEN
            confidence = max(open_score, endpoint_confidence)
        elif nearest < 0.60:
            position = GatePosition.UNKNOWN
            confidence = 0.0
        elif endpoint_confidence >= 0.75:
            position = GatePosition.OPEN if open_score > closed_score else GatePosition.CLOSED
            confidence = endpoint_confidence
        else:
            position = GatePosition.PARTIALLY_OPEN
            confidence = partial_score

        return Observation(
            position,
            confidence,
            open_score,
            closed_score,
            partial_score,
            frame_change,
            is_moving,
            timestamp,
        ), region

    @classmethod
    def _feature(cls, region: np.ndarray) -> np.ndarray:
        return cls._feature_processed(preprocess(region))

    @classmethod
    def _feature_processed(cls, processed: np.ndarray) -> np.ndarray:
        hog = cls._hog.compute(processed).reshape(-1).astype(np.float32)
        coarse = cv2.resize(processed, (40, 24), interpolation=cv2.INTER_AREA).astype(np.float32)
        coarse = (coarse - coarse.mean()) / (coarse.std() + 1e-6)
        feature = np.concatenate((hog, coarse.reshape(-1) * 0.35))
        return feature / max(float(np.linalg.norm(feature)), 1e-6)

    @staticmethod
    def _similarity(feature: np.ndarray, references: list[np.ndarray]) -> float:
        return max(float(np.dot(feature, reference)) for reference in references)


@dataclass(frozen=True, slots=True)
class VisionSnapshot:
    latest: Observation
    confirmed: GatePosition
    observations: tuple[Observation, ...]
    version: int
    confirmation_current: bool = False

    def stationary_duration(
        self,
        position: GatePosition,
        *,
        since: datetime,
        minimum_confidence: float = 0.0,
        minimum_observations: int = 3,
    ) -> float:
        matching: list[Observation] = []
        for item in reversed(self.observations):
            if item.timestamp <= since:
                break
            if (
                item.position != position
                or item.is_moving
                or item.confidence < minimum_confidence
            ):
                break
            matching.append(item)
        if len(matching) < minimum_observations:
            return 0.0
        return max(0.0, (matching[0].timestamp - matching[-1].timestamp).total_seconds())


class RollingVision:
    def __init__(self, window: int = 4, required: int = 3, history: int = 256) -> None:
        self.window = window
        self.required = required
        self._observations: deque[Observation] = deque(maxlen=max(6, history))
        self._confirmed = GatePosition.UNKNOWN
        self._current = False
        self._version = 0
        self._condition = asyncio.Condition()

    async def add(self, observation: Observation) -> VisionSnapshot:
        async with self._condition:
            self._observations.append(observation)
            recent = tuple(self._observations)[-self.window :]
            for position in (GatePosition.OPEN, GatePosition.CLOSED):
                if self._support(position, recent) >= self.required:
                    self._confirmed = position
                    break
            # A confirmation the rolling window no longer supports is stale, so
            # the gate state it implies must not be reported indefinitely.
            self._current = self._confirmed in (
                GatePosition.OPEN,
                GatePosition.CLOSED,
            ) and self._support(self._confirmed, recent) >= self.required
            self._version += 1
            snapshot = self._snapshot()
            self._condition.notify_all()
            return snapshot

    async def snapshot(self) -> VisionSnapshot:
        async with self._condition:
            return self._snapshot()

    async def wait_for_update(self, after: int, timeout: float) -> VisionSnapshot:
        async with self._condition:
            await asyncio.wait_for(
                self._condition.wait_for(lambda: self._version > after),
                timeout,
            )
            return self._snapshot()

    @staticmethod
    def _support(position: GatePosition, recent: tuple[Observation, ...]) -> int:
        return sum(
            item.position == position and item.confidence >= 0.80 and not item.is_moving
            for item in recent
        )

    def _snapshot(self) -> VisionSnapshot:
        latest = self._observations[-1] if self._observations else Observation(
            GatePosition.UNKNOWN,
            0.0,
        )
        return VisionSnapshot(
            latest,
            self._confirmed,
            tuple(self._observations),
            self._version,
            self._current,
        )
