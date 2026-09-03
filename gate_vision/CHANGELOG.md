# Changelog

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
