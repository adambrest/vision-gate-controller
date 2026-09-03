from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.models import GatePosition, Observation
from app.vision import (
    DECODE_FLAG,
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
    source = cv2.imread(str(root / "open" / "open_0.jpg"), DECODE_FLAG)
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


def real_classifier() -> ReferenceClassifier:
    return ReferenceClassifier(Path(__file__).resolve().parents[1] / "references")


def real_region(kind: str, index: int = 0):
    root = Path(__file__).resolve().parents[1] / "references" / kind
    path = sorted(root.glob("*.jpg"))[index]
    return extract_gate_region(cv2.imread(str(path), DECODE_FLAG))


def test_travel_calibrates_from_the_reference_set() -> None:
    assert real_classifier().travel.ready is True


@pytest.mark.parametrize(
    ("kind", "index", "expected"),
    [("closed", 0, 0.0), ("closed", 1, 0.0), ("open", 0, 1.0), ("open", 1, 1.0)],
)
def test_travel_measures_the_real_endpoints(kind: str, index: int, expected: float) -> None:
    """Both endpoints must stay exact, including the night closed reference."""
    travel = real_classifier().travel.measure(real_region(kind, index))
    assert travel == pytest.approx(expected, abs=0.02)


def test_endpoints_are_still_classified_after_the_travel_check() -> None:
    classifier = real_classifier()
    for kind, expected in (("closed", GatePosition.CLOSED), ("open", GatePosition.OPEN)):
        for index in (0, 1):
            root = Path(__file__).resolve().parents[1] / "references" / kind
            frame = cv2.imread(str(sorted(root.glob("*.jpg"))[index]), DECODE_FLAG)
            classifier.previous = None
            observation, _ = classifier.observe(
                frame, sample_interval_seconds=1.0, timestamp=datetime.now(UTC)
            )
            assert observation.position is expected
            assert observation.confidence >= 0.80


def test_gate_short_of_its_endpoint_is_not_reported_as_open() -> None:
    """The regression: region similarity called a nearly-open gate OPEN."""
    classifier = real_classifier()
    closed, opened = real_region("closed"), real_region("open")
    width = closed.shape[1]
    # Slide the panel left, leaving it short of the fully open position.
    partial = opened.copy()
    shift = int(round(0.55 * width))
    partial[:, : width - shift] = closed[:, shift:]

    travel = classifier.travel.measure(partial)
    assert travel is not None and travel < 0.92
    position, confidence = classifier._apply_travel(GatePosition.OPEN, 0.98, travel)
    assert position is GatePosition.PARTIALLY_OPEN
    # Stays commandable so the controller can drive the gate to its target.
    assert confidence >= 0.70


def test_travel_never_invents_an_endpoint() -> None:
    classifier = real_classifier()
    for claimed in (GatePosition.PARTIALLY_OPEN, GatePosition.UNKNOWN):
        position, confidence = classifier._apply_travel(claimed, 0.5, 1.0)
        assert position is claimed and confidence == 0.5


def test_missing_travel_calibration_leaves_the_verdict_untouched() -> None:
    classifier = real_classifier()
    assert classifier._apply_travel(GatePosition.OPEN, 0.91, None) == (GatePosition.OPEN, 0.91)
