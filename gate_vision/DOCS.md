# Runtime behaviour

This app serves my own gate installation on Home Assistant Yellow. The source,
reference images, and automated image build are kept on GitHub to make personal
updates and maintenance easier.

## Home Assistant connection

The camera is `camera.garage_high_resolution_channel`, and
`script.main_gate_relay_pulse` produces the momentary relay pulse. Camera and
relay access use Home Assistant’s internal Supervisor/Core proxy. MQTT Discovery
provides `cover.main_gate` through the local broker and MQTT integration.

The cover supports open and close. It reports `open`, `closed`, `opening`,
`closing`, or `unknown`; Home Assistant shows `unavailable` when monitoring is
unavailable. Richer vision, controller, and fault information appears as
attributes. Home Assistant owns icons and HomeKit presentation.

## Camera observations

Sampling runs at one frame per second while stationary and four while movement
is detected, with a two-second hold after movement. Frames are processed in
memory. Rolling confirmation reduces state changes caused by isolated uncertain
frames, while motion immediately clears the previous position confirmation.

The gate polygon describes the corridor swept by my gate leaf. OPEN and CLOSED
reference frames cover day, IR, and night conditions. Partial frames are held
out for evaluation and are never loaded as classifier references.

Travel is measured from 0.0 (closed) to 1.0 (open) and appears in diagnostics.
It does not reliably distinguish every partial position across lighting
conditions. The endpoint travel veto is disabled by default because held-out
reference testing still exposes errors. The offline classification tool
supports evaluation of these limitations against the production pipeline.

## Controls and diagnostics

The Ingress dashboard shows the live frame, gate polygon, rectified gate region,
classifier observations, references, controller state, attempts, and results.
The Home Assistant cover state appears separately from internal vision and
controller diagnostics. Dashboard open and close actions use the same controller
as the cover entity.

The manual relay control is a plain toggle with no target or result verification.
It can operate when vision reports an unknown position, but remains blocked
while motion or another operation is active.

## Command verification

Startup never pulses the relay. A restart discards any prior in-progress command
and re-establishes state from camera observations. Verified commands require a
known state, and commands are rejected while the gate is moving.

An operation allows at most three pulses. Retries require stable post-pulse
camera evidence: a stationary partial position for ten seconds or a confirmed
wrong endpoint for five seconds, with at least three qualifying observations.
Motion resets these timers; uncertain evidence never authorizes a retry.

A single failed camera frame does not withdraw availability. The default camera
offline threshold is fifteen seconds; missing references prevent monitoring
immediately. Camera vision is not an obstruction sensor, and the gate’s physical
safety systems remain authoritative.
