# Changelog

## 1.1.12

- Correct three mislabelled frames added in 1.1.11. All three are partially
  open, not endpoints, and are moved to `references/partial`. The endpoint set
  is back to three open and three closed frames.
- Tighten `GATE_POINTS` to the gate travel corridor, derived from what changes
  between the closed and open references across all three lighting conditions.
  The old polygon reached into the road, so passing vehicles, the crane and
  driveway shadows were measured as though they were the gate.
- Travel leave-one-out improves from 3/6 to 5/6. Open references now generalise
  with no failures; one closed reference still fails. The veto stays disabled
  until that reaches zero.

## 1.1.11

- Add three endpoint references captured at times the set did not cover: an
  afternoon closed gate under dappled tree shadow, a late-night closed gate,
  and an afternoon open gate. A vehicle crossing the open gateway is added to
  the held-out partial folder.
- These were found by running real frames through `tools/classify.py`. Both
  closed frames were previously reported as `PARTIALLY_OPEN`, so the cover
  state read `unknown` for a fully closed gate in the afternoon and at night.
  The scene changes more with sun angle than with gate position, and the
  reference set did not cover those angles.
- Coverage measured on those six frames improves from 4/6 to 5/6, travel
  leave-one-out from 3/6 to 6/9, and detected partials from 4/8 to 5/9.
  Travel leave-one-out now fails only on closed references; open generalises
  cleanly.

## 1.1.10

- Add `tools/classify.py`, which runs any image through the production pipeline
  and reports the vision position, measured travel and resulting cover state.
  `--annotate` draws the detected leading edge over the frame, and `--expect`
  turns a folder of labelled frames into a pass/fail check.
- Decode frames adaptively. Halving is skipped when it would take a frame below
  a usable working width, so reference captures smaller than the live camera
  feed are no longer calibrated at a third of the detail the camera provides.
  Full-size camera frames are still halved for CPU and memory.

## 1.1.9

- Replace the reference set with six labelled frames covering day, IR and night
  at both endpoints, plus eight labelled partial frames held out for testing.
- Fix two bugs in the 1.1.8 travel measure that only appeared once references
  spanned several lighting conditions. Open references were averaged while
  closed references were maximised over; averaging a day frame with an IR frame
  produces a profile matching neither, and a genuinely open gate at night
  measured 0% travel. The leading edge also took the rightmost matching run
  anywhere in the region, so a fence post at the far side pinned it to fully
  closed. References are now maximised over individually and the edge is the end
  of the leaf's contiguous run.
- Disable the endpoint travel veto by default. Leave-one-out over the reference
  set shows travel does not generalise to unseen lighting: a held-out daytime
  CLOSED frame measures fully open, because the comparison is a lighting-matched
  nearest neighbour rather than a geometric one. Left enabled it would downgrade
  a genuinely closed gate to PARTIALLY_OPEN and let a CLOSE command pulse an
  already-closed gate back open. Enable with `endpoint_travel_veto` once the
  reference set covers more conditions.
- Travel is still measured every frame and published as the `travel` attribute
  so coverage can be assessed from real data.

## 1.1.8

- Fix a state error where a gate stopped short of its endpoint was reported as
  fully OPEN. Region similarity saturates long before travel completes: a gate
  at 82% travel scored OPEN at 0.94 confidence, and the gap between 90% and
  100% travel was about 0.01 - far inside day/night variation, so no threshold
  could separate them.
- Add `GateTravel`, which measures the panel's leading edge per column and
  reports travel from 0.0 (closed) to 1.0 (fully open). Both endpoints of the
  existing reference set, including the night frame, measure exactly 0% and 100%.
- Use travel to veto a claimed endpoint that travel does not support. It can
  only downgrade OPEN or CLOSED to PARTIALLY_OPEN and never invents an endpoint.
  Confidence stays at or above 0.70 so a stranded gate remains commandable and
  the existing partial-stall recovery pulse can now actually engage.
- Add `endpoint_travel_tolerance` (default 0.08) to tune endpoint strictness.
- Decode camera frames at half size: 6.2 MB per frame down to 1.6 MB, and about
  28% less CPU per frame. The gate polygon scales from the frame dimensions, so
  geometry is unaffected.
- Cap OpenCV at two threads; it defaulted to one per core and thrashed.
- Show measured gate travel on the debug page.

## 1.1.7

- Remove all Home Assistant icon-selection logic. The app no longer publishes an
  icon or maps states to `mdi:boom-gate*`; Home Assistant owns presentation.
- Add `get_effective_cover_state()` as the single source of truth, emitting only
  the valid cover states `open`, `closed`, `opening`, `closing`, and `unknown`.
- Publish `opening`/`closing` the moment a command is accepted, without waiting
  for the next camera frame.
- Stop reusing a rolling confirmation the vision window no longer supports.
- Track availability separately, withdrawing the entity only after the camera has
  been offline for `camera_unavailable_after_seconds` (default 15).
- Expose `vision_state`, `fault`, `fault_reason`, and `target_state` as
  diagnostic attributes instead of as cover states.
- Stop advertising STOP, which the toggle relay cannot perform.
- Separate the Home Assistant state from vision and controller state on the
  debug page.

## 1.1.6

- Map the standard `cover.main_gate` entity to explicit boom-gate icons for
  closed, open, closing, and opening states.
- Use the boom-gate alert icon whenever camera availability or gate state is
  untrusted.

## 1.1.5

- Use Home Assistant's native state-aware gate icons instead of repeatedly
  changing the static MQTT discovery icon.
- Buffer terminal and unknown cover states for three seconds.
- Ignore camera availability dropouts shorter than three seconds.

## 1.1.4

- Rename the app to Vision Gate Controller.
- Hold directional gate icons through brief vision outliers and require a
  three-second stable state before switching to a terminal or alert icon.

## 1.1.3

- Publish the app through a Home Assistant app repository.
- Add an automatically built GHCR container image.

## 1.1.2

- Provide MQTT cover discovery, camera verification, bounded retries, and an
  Ingress control dashboard.
