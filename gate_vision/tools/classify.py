"""Classify camera frames with the production pipeline.

Point it at any image, or a directory of them, and it reports exactly what the
app would decide: the vision position, measured gate travel, and the resulting
Home Assistant cover state. Use it to probe edge cases before trusting them.

    python -m tools.classify frame.jpg
    python -m tools.classify ~/Downloads --expect partial
    python -m tools.classify frame.jpg --annotate out/     # show why
    python -m tools.classify frame.jpg --veto              # with the veto on

Run it from the gate_vision directory.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from app.config import Settings
from app.models import GatePosition
from app.vision import (
    GATE_POLYGON,
    REFERENCE_RESOLUTION,
    TRAVEL_COLUMNS,
    ReferenceClassifier,
    extract_gate_region,
    read_frame,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
LABELS = ("open", "closed", "partial")


def collect(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            found.extend(
                p for p in sorted(path.rglob("*")) if p.suffix.lower() in IMAGE_SUFFIXES
            )
        elif path.suffix.lower() in IMAGE_SUFFIXES:
            found.append(path)
        else:
            print(f"skipping {path} (not an image)", file=sys.stderr)
    return found


def travel_verdict(travel: float | None, tolerance: float) -> str:
    if travel is None:
        return "UNMEASURED"
    if travel >= 1.0 - tolerance:
        return "open"
    if travel <= tolerance:
        return "closed"
    return "partial"


def cover_state_if_sustained(observation) -> str:
    """The cover state this frame would produce if it kept repeating.

    A single frame cannot express rolling confirmation, so this assumes the
    frame persists long enough to confirm. It is an approximation of the live
    entity, not a substitute for it.
    """
    if observation.is_moving:
        return "unknown"
    if observation.position is GatePosition.OPEN and observation.confidence >= 0.80:
        return "open"
    if observation.position is GatePosition.CLOSED and observation.confidence >= 0.80:
        return "closed"
    return "unknown"


def annotate(path: Path, frame: np.ndarray, region: np.ndarray, classifier, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    height, width = frame.shape[:2]
    marked = frame.copy()
    points = np.asarray(
        [
            [round(x * width / REFERENCE_RESOLUTION[0]), round(y * height / REFERENCE_RESOLUTION[1])]
            for x, y in GATE_POLYGON
        ],
        dtype=np.int32,
    )
    cv2.polylines(marked, [points], True, (120, 255, 160), 2, cv2.LINE_AA)

    panel = cv2.resize(region, (TRAVEL_COLUMNS * 3, 240), interpolation=cv2.INTER_CUBIC)
    travel = classifier.travel.measure(region)
    if travel is not None:
        edge = int(round((1.0 - travel) * TRAVEL_COLUMNS)) * 3
        cv2.line(panel, (edge, 0), (edge, panel.shape[0]), (60, 60, 255), 3)
        cv2.putText(
            panel,
            f"leading edge  travel {travel * 100:.1f}%",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (60, 60, 255),
            2,
        )
    marked = cv2.resize(marked, (panel.shape[1], round(height * panel.shape[1] / width)))
    target = out_dir / f"{path.stem}_annotated.png"
    cv2.imwrite(str(target), np.vstack([marked, panel]))
    return target


def validate(root: Path, settings) -> int:
    """Score the reference set and say whether the travel veto is safe to enable.

    Every reference-matching method tried so far scores near perfectly when a
    frame is in its own calibration and collapses when it is held out, because
    three lighting conditions per class is enough to memorise lighting but not
    to generalise. Leave-one-out is the honest measure, and the acceptance
    criterion for turning the veto on.
    """
    from app.vision import GateTravel, extract_gate_region

    read = lambda p: extract_gate_region(read_frame(p))
    opens = sorted((root / "open").glob("*.jpg"))
    closeds = sorted((root / "closed").glob("*.jpg"))
    partials = sorted((root / "partial").glob("*.jpg"))
    if len(opens) < 2 or len(closeds) < 2:
        print("need at least two open and two closed references to validate")
        return 2
    tol = settings.endpoint_travel_tolerance
    classifier = ReferenceClassifier(
        root, endpoint_travel_tolerance=tol,
        endpoint_travel_corroboration=settings.endpoint_travel_corroboration,
    )
    print(f"reference set: {len(opens)} open, {len(closeds)} closed, {len(partials)} partial\n")

    confirmed = 0
    for kind, expected in (("open", GatePosition.OPEN), ("closed", GatePosition.CLOSED)):
        for path in sorted((root / kind).glob("*.jpg")):
            classifier.previous = None
            obs, _ = classifier.observe(
                read_frame(path), sample_interval_seconds=1.0, timestamp=datetime.now(UTC)
            )
            ok = obs.position is expected and obs.confidence >= 0.80
            confirmed += ok
            if not ok:
                print(f"  endpoint not confirmed: {path.stem} -> {obs.position.value} {obs.confidence:.2f}")
    total_ends = len(opens) + len(closeds)
    print(f"endpoints confirmed in-set          {confirmed}/{total_ends}")

    print("\nleave-one-out (each frame held out of its own calibration):")
    fails = {"open": [], "closed": []}
    for kind, group, other, ok in (
        ("open", opens, closeds, lambda v: v >= 1.0 - tol),
        ("closed", closeds, opens, lambda v: v <= tol),
    ):
        for held in group:
            rest = [p for p in group if p != held]
            travel = GateTravel()
            if kind == "open":
                travel.calibrate([read(p) for p in rest], [read(p) for p in other])
            else:
                travel.calibrate([read(p) for p in other], [read(p) for p in rest])
            value = travel.measure(read(held))
            if not ok(value):
                fails[kind].append((held.stem, value))
    for kind, group in (("open", opens), ("closed", closeds)):
        bad = fails[kind]
        print(f"  {kind:<7} {len(group) - len(bad)}/{len(group)}"
              + ("" if not bad else "   failing: " + ", ".join(f"{n} ({v*100:.0f}%)" for n, v in bad)))

    wrong = [p.stem for p in partials
             if (lambda o: o.position in (GatePosition.OPEN, GatePosition.CLOSED) and o.confidence >= 0.80)(
                 classifier.observe(read_frame(p), sample_interval_seconds=1.0,
                                    timestamp=datetime.now(UTC))[0])]
    print(f"\npartials wrongly read as an endpoint {len(wrong)}/{len(partials)}")
    for name in wrong:
        print(f"  {name}")

    clean = not fails["open"] and not fails["closed"]
    print("\n" + ("-" * 62))
    if clean and confirmed == total_ends:
        print("PASS  leave-one-out is clean. endpoint_travel_veto can be enabled.")
        return 0
    print("HOLD  leave-one-out still fails, so endpoint_travel_veto must stay off.")
    print("      Add endpoint references for conditions that fail above, then re-run.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="*", help="image files, or directories of them")
    parser.add_argument("--validate", action="store_true",
                        help="score the reference set and report whether the travel veto can be enabled")
    parser.add_argument("--references", default="references", help="reference root")
    parser.add_argument("--expect", choices=LABELS, help="assert every image is this state")
    parser.add_argument("--annotate", metavar="DIR", help="write annotated images showing the leading edge")
    parser.add_argument("--veto", action="store_true", help="enable the endpoint travel veto")
    args = parser.parse_args()

    settings = Settings()
    classifier = ReferenceClassifier(
        Path(args.references),
        endpoint_travel_tolerance=settings.endpoint_travel_tolerance,
        endpoint_travel_veto=args.veto,
    )
    if not classifier.ready:
        print("reference set is not usable: need at least one open and one closed frame")
        return 2
    print(
        f"references: {classifier.count} frames, travel calibrated={classifier.travel.ready}, "
        f"veto={'on' if args.veto else 'off'}, tolerance={settings.endpoint_travel_tolerance}"
    )

    if args.validate:
        return validate(Path(args.references), settings)

    images = collect(args.images)
    if not images:
        print("no images found")
        return 2

    mismatches = 0
    for path in images:
        frame = read_frame(path)
        if frame is None or frame.size == 0:
            print(f"\n{path.name}\n  unreadable image")
            mismatches += 1
            continue
        region = extract_gate_region(frame)
        classifier.previous = None
        observation, _ = classifier.observe(
            frame, sample_interval_seconds=1.0, timestamp=datetime.now(UTC)
        )
        verdict = travel_verdict(observation.travel, settings.endpoint_travel_tolerance)
        travel_text = "unmeasured" if observation.travel is None else f"{observation.travel * 100:.1f}%"

        print(f"\n{path.name}   [{frame.shape[1]}x{frame.shape[0]} decoded]")
        print(f"  vision position   {observation.position.value}  (confidence {observation.confidence:.2f})")
        print(f"  gate travel       {travel_text}  -> {verdict}")
        print(
            f"  scores            open {observation.open_score:.2f}  "
            f"closed {observation.closed_score:.2f}  partial {observation.partial_score:.2f}"
        )
        print(f"  HA cover state    {cover_state_if_sustained(observation)}   (if sustained)")
        if args.annotate:
            print(f"  annotated         {annotate(path, frame, region, classifier, Path(args.annotate))}")
        if args.expect:
            actual = observation.position.value.lower()
            hit = (
                args.expect == "partial"
                and (verdict == "partial" or actual == "partially_open")
            ) or (args.expect in ("open", "closed") and verdict == args.expect)
            if not hit:
                mismatches += 1
                print(f"  MISMATCH          expected {args.expect}")

    if args.expect:
        print(f"\n{len(images) - mismatches}/{len(images)} matched '{args.expect}'")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
