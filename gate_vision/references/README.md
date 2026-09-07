# Reference frames

`open/` and `closed/` are loaded by the classifier. Every supported image file
in them is used; there is no filename allowlist. At least one OPEN and one
CLOSED file are required.

`partial/` is **not** loaded by the classifier. It holds labelled
partially-open frames used as held-out test data for the gate travel measure.

Filenames record the lighting condition and gate state. The endpoint set covers
my camera’s day, IR, and night views. These references are specific to my gate.

Known limitation: leave-one-out testing still fails on one closed reference, so
lighting outside these conditions is not fully covered. The offline classifier
compares held-out frames against the production pipeline to track this limitation.

See the [technical overview](../README.md) for the gate travel measurements.
