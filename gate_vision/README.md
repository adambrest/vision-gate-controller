# Vision Gate Controller — Home Assistant Yellow

Vision Gate Controller is my personal Home Assistant app, tailored to my gate and camera on Home Assistant Yellow. GitHub supports updates, change tracking, and maintenance of my installation. It publishes `cover.main_gate` through MQTT Discovery. Camera vision supplies physical state; every command calls `script.main_gate_relay_pulse` through Home Assistant's internal Supervisor/Core proxy and verifies the requested endpoint.

## Runtime

- Stationary and idle: one camera snapshot per second.
- Detected movement: four snapshots per second, held for two seconds after the last movement frame.
- The Home Assistant Ingress control dashboard is always available.
- Frames are processed in memory and are never accumulated on disk.

Position and motion are independent. For example, the gate can be `PARTIALLY_OPEN` and `MOVING` simultaneously. No command or retry is sent while motion is detected.

## Ownership

Vision Gate Controller owns the physical gate state, vision classification,
controller state, and command verification. Home Assistant owns everything
about presentation: the dashboard icon, any icon templating or customisation,
and the HomeKit presentation. The app publishes no icon and contains no
icon-selection logic, so icons are changed entirely in Home Assistant.

## Home Assistant cover states

`cover.main_gate` publishes only valid Home Assistant Cover states:

| State | Meaning |
| --- | --- |
| `open` | Rolling camera confirmation currently says OPEN. |
| `closed` | Rolling camera confirmation currently says CLOSED. |
| `opening` | An OPEN command was accepted and is still in progress. |
| `closing` | A CLOSE command was accepted and is still in progress. |
| `unknown` | No operation is active and vision is not confidently OPEN or CLOSED. |
| `unavailable` | Home Assistant's own state when availability is withdrawn. |

`get_effective_cover_state()` in `app/cover_state.py` is the single source of
truth and applies exactly these rules, in order:

1. An active operation targeting OPEN reports `opening`.
2. An active operation targeting CLOSED reports `closing`.
3. A current rolling confirmation of OPEN reports `open`.
4. A current rolling confirmation of CLOSED reports `closed`.
5. Anything else reports `unknown`.

`opening` and `closing` are published the moment GateController accepts the
command, without waiting for the next camera frame. A confirmation the rolling
window no longer supports is treated as stale, so a previously confirmed
position is never reported indefinitely.

Custom states such as `partial`, `moving`, `fault`, `stopped`, or `error` are
never emitted. They exist internally and are exposed as attributes only:
`vision_state`, `raw_state`, `confirmed_state`, `controller_state`,
`target_state`, `confidence`, `fault`, `fault_reason`, and `last_result`. A
gate stopped mid-travel therefore reports cover state `unknown` with
`vision_state: PARTIAL`, `fault: true`, and
`fault_reason: gate_stopped_mid_travel`.

## Availability

Availability is tracked separately from gate position. The entity is available
while the app is running, Home Assistant communication works, and the camera is
usable for gate-state monitoring. A single failed camera frame never withdraws
it; the camera must stay offline for `camera_unavailable_after_seconds`
(default 15). A dependency that makes gate-state monitoring impossible, such as
a missing reference set, withdraws availability immediately. Availability
returns as soon as camera frames resume.

Home Assistant is pushed a fresh entity update whenever opening or closing
begins, OPEN or CLOSED is confirmed, an operation fails or aborts, the state
becomes unknown, availability changes, or a diagnostic fault changes. No manual
refresh or unrelated polling delay is involved.

## Supported actions

`cover.main_gate` keeps device class `gate` and advertises OPEN and CLOSE only.
Set position, tilt, and stop are deliberately not advertised: the gate is a
toggle relay verified by camera, and travel cannot be halted on command.

## Stability

Rolling confirmation uses a lower bar to hold a confirmation than to earn one.
Three frames at 0.80 confidence acquire it; two frames at 0.70 keep it. With a
single threshold, a stationary gate whose confidence sits near the line flaps
between its position and `unknown`, which reads as the entity misbehaving.
Motion is the one exception and drops the confirmation immediately, since a
moving gate really is changing position.

The entity reports `unknown` rather than guessing. That is deliberate: a wrong
confident state is worse than an honest absent one.

## Working the gate by hand

Camera verification can leave the gate state unknown, and every verified command
is refused in that case. The Ingress page therefore carries a plain relay pulse
that works regardless of gate state. It is a bare toggle: it claims no target,
verifies no result, and is not treated as a fault. It still refuses to fire
while the gate is moving, and while another operation is running.

## Bounded recovery

An operation has at most three pulses total: the initial pulse and no more than two retries.

- A continuously stationary, confident `PARTIALLY_OPEN` gate permits a retry after 10 seconds.
- A confidently confirmed wrong endpoint permits a retry after five seconds.
- Either retry condition requires at least three qualifying post-pulse frames.
- Motion resets both timers.
- Unknown or inconsistent camera evidence never authorizes a retry.
- A third unsuccessful attempt ends as `FAILED_AFTER_3_ATTEMPTS`; attempt four cannot occur.

## Gate travel

Region similarity cannot separate a nearly-open gate from a fully open one.
Measured on this camera, a gate at 82% travel scored OPEN at 0.94 confidence,
and the gap between 90% and 100% travel was about 0.01 - far inside day/night
variation, so no threshold on that score can separate them.

`GateTravel` measures the gate leaf's leading edge column by column against the
reference set and reports travel from 0.0 (closed) to 1.0 (fully open).
References are compared individually and maximised over, never averaged: day,
IR and night frames of the same scene look nothing alike. The edge is the end
of the leaf's contiguous run, so a fence post or a parked vehicle further along
the region cannot pin it.

### The gate travel corridor

`GATE_POINTS` covers only the corridor the leaf sweeps between parked and
closed. It is derived from what changes between the closed and open references
in all three lighting conditions, so it excludes the road, the driveway beyond
the gate and the paving below. The previous polygon reached into the road,
where passing vehicles, a crane and moving shadows were measured as though they
were the gate.

### Measured accuracy

Against the labelled reference set, with the eleven partial frames held out of
calibration:

| | result |
| --- | --- |
| Endpoints, in-set | 6/6 exact (open 100%, closed 0%) |
| Endpoints, leave-one-out | 5/6 (open 3/3, closed 2/3) |
| Held-out partial frames detected | 4/11 |
| Held-out partial frames read as an endpoint (with the clearance check) | 1/11 |
| Endpoint frames misread as partial | 0 |

Travel alone reads a leaf stopped just short of open as fully open, because an
angled, nearly-folded leaf matches no closed reference. OPEN therefore also
requires the columns beside the parked leaf to match an open gate
(`open_clearance_minimum`, default 0.50). Held-out open frames score at least
0.64 there; the near-open partials that used to confirm OPEN score about 0.37.

Endpoints are reliable. **Partial position is not solved.** Four approaches
were measured and rejected:

| approach | result | why it fails |
| --- | --- | --- |
| Whole-region similarity | partial indistinguishable from open | encodes lighting, not gate position |
| Three-class nearest neighbour | 4/14 | nearest neighbour of a closed frame is a partial frame of the same lighting |
| Per-column slat texture | 3/14 | at night the far half of the gate is unlit and has no texture |
| Leading-edge template match | 4/17 | locks onto the fixed end post, present in every frame |

The per-column reference comparison in use is the best of them, and its limit
is structural: the leaf has periodic slats, so a translated gate never aligns
with a closed reference, and correlation collapses. Frames whose slats happen
to align score as closed; the rest do not.

Solving partial position properly needs either substantially more labelled data
with a learned model, or a limit switch on the gate motor. The camera is
reliable for the endpoints and for detecting motion, which is what the
controller depends on.

## Runtime cost

Camera frames are decoded at half size, which cuts a 1920x1080 frame from
6.2 MB to 1.6 MB and about 28% of the per-frame CPU. The gate polygon is scaled
from the frame dimensions, so decoding smaller changes no geometry. OpenCV is
capped at two threads because it otherwise starts one per core and thrashes on
frames this small.

## References

Every supported image file beneath `references/open` and `references/closed` is enumerated, logged by filename, transformed through the same pipeline, and used. There is no filename allowlist or maximum count. The classifier becomes operational when at least one valid OPEN and one valid CLOSED image exist.

The reference set contains OPEN and CLOSED frames from my camera in day, IR,
and night conditions. These frames are part of the app image and tie the
classifier to this particular gate and viewpoint.

## Entity transport

MQTT Discovery is used because an App cannot directly register a functional Home Assistant cover over the Core REST API. MQTT supplies a native commandable entity without a companion custom integration or a second control server. It requires the official Mosquitto Broker App and Home Assistant MQTT integration.

All MQTT traffic remains inside Home Assistant. Camera and relay access use `http://supervisor/core/api` with the injected `SUPERVISOR_TOKEN`; no HA URL or long-lived token is configured.

## Web control

The Ingress page is always on and deliberately minimal. It shows camera availability,
the latest camera frame, OPEN/CLOSE controls, and the richer internal states. It
distinguishes the formal Home Assistant cover state from the diagnostics beside
it, so `Home Assistant state: opening`, `Vision state: MOVING`, and
`Controller target: OPEN` are shown as separate rows. The controls call the same
GateController as `cover.main_gate`.

Camera sampling is fixed at 1 Hz while stationary and 4 Hz after movement is detected.
Old polling values retained in an existing App's `options.json` are ignored. The browser
checks the lightweight status endpoint at 4 Hz so it can display movement promptly.

See [Runtime behaviour](DOCS.md) for more detail on this installation.
