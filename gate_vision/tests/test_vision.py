from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.models import GatePosition, Observation
from app.vision import (
    ReferenceClassifier,
    RollingVision,
    extract_gate_region,
    scaled_gate_points,
)


def frame(position: str) -> np.ndarray:
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    cv2.rectangle(image, (280, 240), (680, 580), (50, 70, 90), -1)
    positions = (
        list(range(310, 371, 20)) + list(range(590, 641, 20))
        if position == "open"
        else list(range(310, 641, 20))
    )
    for x in positions:
        cv2.line(image, (x, 250), (x, 570), (225, 225, 225), 6)
    return image


def six_references(root: Path) -> None:
    for position in ("open", "closed"):
        directory = root / position
        directory.mkdir(parents=True)
        for index in range(3):
            image = frame(position)
            image = cv2.convertScaleAbs(image, alpha=1.0, beta=index * 2)
            assert cv2.imwrite(str(directory / f"{position}_{index}.jpg"), image)


def test_polygon_scales_from_reference_resolution() -> None:
    points = scaled_gate_points(640, 360)
    assert points["top_left"] == (158.5, 134.0)
    assert points["bottom_right"] == (300.0, 275.0)


def test_perspective_transform_returns_image() -> None:
    region = extract_gate_region(frame("closed"))
    assert region.size > 0
    assert region.shape[1] > region.shape[0]


def test_exactly_six_references_use_live_transform(tmp_path: Path) -> None:
    root = tmp_path / "references"
    six_references(root)
    classifier = ReferenceClassifier(root)
    source = cv2.imread(str(root / "open" / "open_0.jpg"))
    expected = classifier._feature(extract_gate_region(source))

    assert classifier.ready is True
    assert classifier.count == 6
    np.testing.assert_allclose(classifier.features[GatePosition.OPEN][0], expected)


def test_every_available_reference_is_loaded_without_exact_count_gate(tmp_path: Path) -> None:
    root = tmp_path / "references"
    six_references(root)
    (root / "open" / "open_0.jpg").unlink()
    classifier = ReferenceClassifier(root)
    assert classifier.ready is True
    assert classifier.count == 5


def test_missing_entire_endpoint_class_disables_classifier(tmp_path: Path) -> None:
    root = tmp_path / "references"
    six_references(root)
    for path in (root / "open").iterdir():
        path.unlink()
    classifier = ReferenceClassifier(root)
    assert classifier.ready is False


@pytest.mark.asyncio
async def test_rolling_confirmation_ignores_unknown() -> None:
    vision = RollingVision(window=4, required=3)
    for position in (
        GatePosition.CLOSED,
        GatePosition.CLOSED,
        GatePosition.UNKNOWN,
        GatePosition.CLOSED,
    ):
        snapshot = await vision.add(Observation(position, 0.95))
    assert snapshot.confirmed == GatePosition.CLOSED


@pytest.mark.asyncio
async def test_moving_position_does_not_confirm_endpoint() -> None:
    vision = RollingVision(window=4, required=3)
    for _ in range(4):
        snapshot = await vision.add(
            Observation(GatePosition.OPEN, 0.99, is_moving=True, timestamp=datetime.now(UTC))
        )
    assert snapshot.confirmed == GatePosition.UNKNOWN
