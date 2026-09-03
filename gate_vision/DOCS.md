# Vision Gate Controller documentation

## Prerequisites

1. Confirm `camera.garage_high_resolution_channel` returns current still images.
2. Confirm manually running `script.main_gate_relay_pulse` produces one momentary gate pulse.
3. Install and start the official Mosquitto Broker App, then configure the Home Assistant MQTT integration.

## Install from GitHub

1. Open **Settings → Apps → App store**.
2. Open the repository menu and add
   `https://github.com/adambrest/vision-gate-controller`.
3. Find and install **Vision Gate Controller** from the app store.
4. Review the Configuration tab, then start the app.
5. Wait for three valid camera confirmations. MQTT Discovery then creates
   `cover.main_gate` with device class `gate`.

The app logs and displays every reference filename at startup. At least one OPEN
and one CLOSED file are required. To add approved originals, place them in their
labeled source directories and publish a new app version; no inventory edit is
necessary.

## Update

New versions appear in Home Assistant after the repository publishes them. Open
the Vision Gate Controller app page and select **Update** when one is available. No files
need to be copied into `/addons` and no local rebuild is required.

## Entity behaviour

`cover.main_gate` has device class `gate` and supports OPEN and CLOSE only. It
publishes only the valid Home Assistant Cover states `open`, `closed`,
`opening`, and `closing`, plus `unknown` when vision cannot confidently place
the gate. Home Assistant renders it as `unavailable` when the app withdraws
availability.

Gate Vision owns the physical gate state, vision classification, controller
state, and command verification. Home Assistant owns presentation: the
dashboard icon, icon templating, and HomeKit presentation. Configure icons with
a Home Assistant template or customisation layer; the app publishes none.

Internal diagnostics such as `PARTIAL`, `MOVING`, and fault reasons are exposed
as entity attributes (`vision_state`, `fault`, `fault_reason`, `target_state`,
`confirmed_state`, `raw_state`, `controller_state`, `confidence`,
`last_result`) and are never used as the cover state.

## Availability

The entity stays available while the app runs, Home Assistant communication
works, and the camera can be used to monitor gate state. A single failed frame
is ignored; the camera must remain offline for
`camera_unavailable_after_seconds` (default 15) before availability is
withdrawn.

## Web interface

The Ingress panel is always available. It displays the current frame, fixed polygon, perspective-corrected gate region, classifier values, rolling observations, reference filenames, controller state, attempts, results, and the same GateController command path used by `cover.main_gate`. The Home Assistant cover state is shown separately from the richer internal vision and controller states.

## Gate travel

The app measures how far the gate has travelled, from 0.0 (closed) to 1.0
(fully open), and publishes it as the `travel` attribute and on the Ingress
debug page.

Travel does not yet gate the reported state. `endpoint_travel_veto` is `false`
by default because leave-one-out testing shows the measure does not generalise
to lighting the reference set does not cover: a held-out daytime closed frame
measures fully open. Enabling it in that state would report a closed gate as
partial, and a CLOSE command would then pulse the gate back open.

To improve it, add reference frames for conditions not yet covered - dusk,
rain, headlights - to `references/open` and `references/closed`, publish a new
version, and re-check. `references/partial` holds labelled partial frames used
only for testing; the classifier never loads them.

## Testing frames offline

`tools/classify.py` runs any image through the production pipeline, so edge
cases can be checked before trusting them. Run it from the `gate_vision`
directory:

```bash
python -m tools.classify frame.jpg                    # one frame
python -m tools.classify ~/captures --expect partial   # a labelled folder
python -m tools.classify frame.jpg --annotate out/     # draw the leading edge
python -m tools.classify frame.jpg --veto              # with the veto enabled
```

It reports the vision position and confidence, measured gate travel, the
open/closed/partial scores, and the cover state the frame would produce if it
persisted. `--annotate` writes an image with the gate polygon drawn on the
frame and the detected leading edge drawn on the rectified region, which is the
quickest way to see why a frame was read the way it was.

Capture reference and test frames at the camera's full resolution where
possible. Frames smaller than the live feed calibrate on less detail.

## Safety behavior## Testing frames offline

`tools/classify.py` runs any image through the production pipeline, so edge
cases can be checked before trusting them. Run it from the `gate_vision`
directory:

```bash
python -m tools.classify frame.jpg                    # one frame
python -m tools.classify ~/captures --expect partial   # a labelled folder
python -m tools.classify frame.jpg --annotate out/     # draw the leading edge
python -m tools.classify frame.jpg --veto              # with the veto enabled
```

It reports the vision position and confidence, measured gate travel, the
open/closed/partial scores, and the cover state the frame would produce if it
persisted. `--annotate` writes an image with the gate polygon drawn on the
frame and the detected leading edge drawn on the rectified region, which is the
quickest way to see why a frame was read the way it was.

Capture reference and test frames at the camera's full resolution where
possible. Frames smaller than the live feed calibrate on less detail.

## Safety behavior

- Startup never pulses the relay.
- Restart discards any prior in-progress command and re-establishes state from camera frames.
- Commands are rejected while the gate is moving.
- Retries require at least three continuous, stable post-pulse camera observations and stop after three total attempts.
- Camera vision is not an obstruction sensor; the gate's physical safety systems remain authoritative.
