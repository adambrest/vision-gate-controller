# Vision Gate Controller — Home Assistant Yellow

Vision Gate Controller is a Home Assistant app that publishes `cover.main_gate` through MQTT Discovery. Camera vision supplies physical state; every command calls `script.main_gate_relay_pulse` through Home Assistant's internal Supervisor/Core proxy and verifies the requested endpoint.

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

## Bounded recovery

An operation has at most three pulses total: the initial pulse and no more than two retries.

- A continuously stationary, confident `PARTIALLY_OPEN` gate permits a retry after 10 seconds.
- A confidently confirmed wrong endpoint permits a retry after five seconds.
- Either retry condition requires at least three qualifying post-pulse frames.
- Motion resets both timers.
- Unknown or inconsistent camera evidence never authorizes a retry.
- A third unsuccessful attempt ends as `FAILED_AFTER_3_ATTEMPTS`; attempt four cannot occur.

## References

Every supported image file beneath `references/open` and `references/closed` is enumerated, logged by filename, transformed through the same pipeline, and used. There is no filename allowlist or maximum count. The classifier becomes operational when at least one valid OPEN and one valid CLOSED image exist.

The published image contains four original reference files. To add new reference
frames, place them in the appropriate source directory, increment the app version,
and publish the repository change. The next GitHub build includes them automatically.

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

See `DOCS.md` for installation and update instructions.
