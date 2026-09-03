# Vision Gate Controller — Home Assistant Yellow

Vision Gate Controller is a Home Assistant app that publishes `cover.main_gate` through MQTT Discovery. Camera vision supplies physical state; every command calls `script.main_gate_relay_pulse` through Home Assistant's internal Supervisor/Core proxy and verifies the requested endpoint.

## Runtime

- Stationary and idle: one camera snapshot per second.
- Detected movement: four snapshots per second, held for two seconds after the last movement frame.
- The Home Assistant Ingress control dashboard is always available.
- Frames are processed in memory and are never accumulated on disk.

Position and motion are independent. For example, the gate can be `PARTIALLY_OPEN` and `MOVING` simultaneously. No command or retry is sent while motion is detected.

Home Assistant supplies its native state-aware gate icons. The published
OPENING/CLOSING state is held through brief vision outliers, and a different
terminal or unknown state is published only after it has remained stable for
three seconds. Camera dropouts must also last three seconds before the entity
is marked unavailable.

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
the current detected position, motion, the queued target, OPEN/CLOSE controls, and the
latest camera frame. The controls call the same GateController as `cover.main_gate`.

Camera sampling is fixed at 1 Hz while stationary and 4 Hz after movement is detected.
Old polling values retained in an existing App's `options.json` are ignored. The browser
checks the lightweight status endpoint at 4 Hz so it can display movement promptly.

See `DOCS.md` for installation and update instructions.
