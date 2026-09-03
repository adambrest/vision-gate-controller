from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.models import GatePosition, Observation
from app.config import Settings
from app.vision import GATE_POINTS as V_GATE_POINTS
from app.vision import (
    GateTravel,
    ReferenceClassifier,
    decode_frame,
    RollingVision,
    extract_gate_region,
    read_frame,
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
    assert points["top_left"] == pytest.approx((163.15, 148.35))
    assert points["bottom_right"] == pytest.approx((305.6, 251.2))


def test_perspective_transform_returns_image() -> None:
    region = extract_gate_region(frame("closed"))
    assert region.size > 0
    assert region.shape[1] > region.shape[0]


def test_exactly_six_references_use_live_transform(tmp_path: Path) -> None:
    root = tmp_path / "references"
    six_references(root)
    classifier = ReferenceClassifier(root)
    source = read_frame(root / "open" / "open_0.jpg")
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


REFERENCES = Path(__file__).resolve().parents[1] / "references"


def real_classifier(**kwargs) -> ReferenceClassifier:
    return ReferenceClassifier(REFERENCES, **kwargs)


def real_region(kind: str, name: str):
    return extract_gate_region(read_frame(REFERENCES / kind / f"{name}.jpg"))


def labelled(kind: str) -> list[str]:
    return sorted(p.stem for p in (REFERENCES / kind).glob("*.jpg"))


def test_reference_set_covers_day_ir_and_night() -> None:
    for kind in ("open", "closed"):
        names = labelled(kind)
        for lighting in ("Day", "IR", "Night"):
            assert any(n.startswith(lighting) for n in names), f"{kind} lacks {lighting}"
    assert len(labelled("open")) == 3
    assert len(labelled("closed")) == 3
    assert len(labelled("partial")) == 11


def test_partial_references_are_not_loaded_as_endpoints() -> None:
    """references/partial holds held-out test data, never classifier input."""
    classifier = real_classifier()
    assert classifier.count == 6
    assert all("partial" not in name for name in classifier.reference_files)


def test_travel_calibrates_from_the_reference_set() -> None:
    assert real_classifier().travel.ready is True


@pytest.mark.parametrize("name", ["Day-closed", "IR-closed", "Night-closed"])
def test_travel_is_zero_at_the_closed_endpoint(name: str) -> None:
    assert real_classifier().travel.measure(real_region("closed", name)) == pytest.approx(0.0, abs=0.08)


@pytest.mark.parametrize("name", ["Day-open", "IR-open", "Night-open"])
def test_travel_is_one_at_the_open_endpoint(name: str) -> None:
    assert real_classifier().travel.measure(real_region("open", name)) == pytest.approx(1.0, abs=0.08)


def test_no_endpoint_reference_is_mistaken_for_a_partial_gate() -> None:
    """The safety-critical property: a false partial lets a command pulse the gate."""
    classifier = real_classifier()
    for kind in ("open", "closed"):
        for name in labelled(kind):
            travel = classifier.travel.measure(real_region(kind, name))
            assert travel <= 0.08 or travel >= 0.92, f"{name} measured {travel}"


def test_travel_catches_most_but_not_all_held_out_partials() -> None:
    """Documents real coverage on the eight labelled partial frames.

    Four of eleven are detected. The rest measure fully open, so they behave
    exactly as they did before travel existed - no new failure mode. Partial
    position is not solved; see the Gate travel section of the README.
    """
    classifier = real_classifier()
    caught = [
        name
        for name in labelled("partial")
        if 0.08 < classifier.travel.measure(real_region("partial", name)) < 0.92
    ]
    assert len(caught) == 4


def test_endpoint_travel_veto_is_disabled_by_default() -> None:
    """Leave-one-out shows travel does not generalise to unseen lighting.

    A held-out daytime CLOSED frame measures fully open, so an enabled veto
    would downgrade a closed gate and let CLOSE pulse it back open.
    """
    assert Settings().endpoint_travel_veto is False
    classifier = real_classifier()
    assert classifier._apply_travel(GatePosition.OPEN, 0.95, 0.0) == (GatePosition.OPEN, 0.95)


def test_travel_veto_downgrades_only_when_explicitly_enabled() -> None:
    classifier = real_classifier(endpoint_travel_veto=True)
    position, confidence = classifier._apply_travel(GatePosition.OPEN, 0.98, 0.50)
    assert position is GatePosition.PARTIALLY_OPEN
    assert confidence >= 0.70
    # Still never invents an endpoint.
    assert classifier._apply_travel(GatePosition.UNKNOWN, 0.3, 1.0) == (GatePosition.UNKNOWN, 0.3)


def test_travel_generalisation_gap_is_tracked() -> None:
    """Held-out closed frames fail; this is why the veto ships disabled."""
    opens = sorted((REFERENCES / "open").glob("*.jpg"))
    closeds = sorted((REFERENCES / "closed").glob("*.jpg"))
    read = lambda p: extract_gate_region(read_frame(p))
    open_failures = 0
    for held in opens:
        rest = [p for p in opens if p != held]
        travel = GateTravel()
        travel.calibrate([read(p) for p in rest], [read(p) for p in closeds])
        if travel.measure(read(held)) < 0.92:
            open_failures += 1
    closed_failures = 0
    for held in closeds:
        rest = [p for p in closeds if p != held]
        travel = GateTravel()
        travel.calibrate([read(p) for p in opens], [read(p) for p in rest])
        if travel.measure(read(held)) > 0.08:
            closed_failures += 1
    # Tightening the gate polygon took closed failures from 3 to 1. Open
    # generalises cleanly. Reconsider the veto when closed reaches 0.
    assert open_failures == 0
    assert closed_failures == 1, "re-evaluate enabling endpoint_travel_veto"


def test_missing_travel_calibration_leaves_the_verdict_untouched() -> None:
    classifier = real_classifier(endpoint_travel_veto=True)
    assert classifier._apply_travel(GatePosition.OPEN, 0.91, None) == (GatePosition.OPEN, 0.91)


def test_small_frames_are_not_shrunk_below_the_working_width() -> None:
    """Reference captures are often smaller than live frames.

    Halving those too would calibrate on far less detail than the camera gives,
    so only frames that stay usable after halving are reduced.
    """
    big = np.zeros((1080, 1920, 3), dtype=np.uint8)
    small = np.zeros((360, 640, 3), dtype=np.uint8)
    for image, expected in ((big, (540, 960)), (small, (360, 640))):
        ok, encoded = cv2.imencode(".jpg", image)
        assert ok
        assert decode_frame(np.frombuffer(encoded.tobytes(), dtype=np.uint8)).shape[:2] == expected


def test_gate_polygon_is_a_parallelogram() -> None:
    """A rigid leaf sweeping a straight opening projects to parallel edges."""
    pts = V_GATE_POINTS
    top = (pts["top_right"][0] - pts["top_left"][0], pts["top_right"][1] - pts["top_left"][1])
    bottom = (pts["bottom_right"][0] - pts["bottom_left"][0], pts["bottom_right"][1] - pts["bottom_left"][1])
    assert top == pytest.approx(bottom, abs=0.5)


def test_gate_polygon_is_configurable() -> None:
    """The corridor can be retuned from add-on options without a rebuild."""
    points = Settings(gate_polygon="1,2 3,4 5,6 7,8").polygon_points()
    assert points["top_left"] == (1.0, 2.0)
    assert points["bottom_left"] == (7.0, 8.0)
    for bad in ("1,2 3,4", "1,2 3,4 5,6 x,y"):
        with pytest.raises(ValueError):
            Settings(gate_polygon=bad).polygon_points()


def test_travel_corroboration_confirms_a_closed_gate_the_margin_misses() -> None:
    """IR-closed scored 0.54 on margin alone, below the 0.80 confirmation bar."""
    classifier = real_classifier()
    position, confidence = classifier._corroborate(
        GatePosition.PARTIALLY_OPEN, 0.54, 0.0, leans_closed=True
    )
    assert position is GatePosition.CLOSED
    assert confidence >= 0.80


def test_travel_corroboration_never_promotes_to_open() -> None:
    """Travel near one only means 'unlike closed', which partials also trigger."""
    classifier = real_classifier()
    assert classifier._corroborate(
        GatePosition.PARTIALLY_OPEN, 0.5, 1.0, leans_closed=False
    ) == (GatePosition.PARTIALLY_OPEN, 0.5)
    # An existing open reading is strengthened, not created.
    _, confidence = classifier._corroborate(GatePosition.OPEN, 0.5, 1.0, leans_closed=False)
    assert confidence >= 0.80


def test_travel_corroboration_needs_similarity_to_agree() -> None:
    classifier = real_classifier()
    assert classifier._corroborate(
        GatePosition.PARTIALLY_OPEN, 0.4, 0.0, leans_closed=False
    ) == (GatePosition.PARTIALLY_OPEN, 0.4)


def test_every_endpoint_reference_reaches_the_confirmation_threshold() -> None:
    """A plainly shut or open gate must not report unknown."""
    classifier = real_classifier()
    for kind, expected in (("open", GatePosition.OPEN), ("closed", GatePosition.CLOSED)):
        for name in labelled(kind):
            classifier.previous = None
            observation, _ = classifier.observe(
                read_frame(REFERENCES / kind / f"{name}.jpg"),
                sample_interval_seconds=1.0,
                timestamp=datetime.now(UTC),
            )
            assert observation.position is expected, name
            assert observation.confidence >= 0.80, f"{name} scored {observation.confidence}"
