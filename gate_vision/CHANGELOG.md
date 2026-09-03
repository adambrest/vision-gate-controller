# Changelog

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
