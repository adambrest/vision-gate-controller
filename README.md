# Vision Gate Controller

My personal camera-verified gate controller for Home Assistant Yellow. It connects
my gate camera and relay to Home Assistant so I can see the gate’s observed state
and control it from a single entity and dashboard.

This project is tailored to my own gate, camera view, and Home Assistant setup.
GitHub holds the source and change history and supports easier updates and
maintenance of my installation.

## How it works

The controller compares camera snapshots with reference images of my open and
closed gate, tracks movement, and publishes `cover.main_gate` through MQTT.
Open and close commands pulse the gate relay through Home Assistant, then use
camera observations to verify the result. Recovery is limited to three pulses
per operation, and no pulse is sent while movement is detected.

An Ingress dashboard shows the current camera frame, gate state, and diagnostics,
with open, close, and manual relay controls. When camera evidence is uncertain,
the reported state is `unknown`. Home Assistant handles dashboard and HomeKit
presentation.

The vision is specific to this camera view and its reference frames. Partial
travel measurement remains limited, particularly across different lighting
conditions. The gate’s physical safety systems remain responsible for
obstruction protection.

[Technical overview](gate_vision/README.md) ·
[Runtime behaviour](gate_vision/DOCS.md) ·
[Change history](gate_vision/CHANGELOG.md)
