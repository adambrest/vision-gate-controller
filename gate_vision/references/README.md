# Reference frames

`open/` and `closed/` are loaded by the classifier. Every supported image file
in them is used; there is no filename allowlist. At least one OPEN and one
CLOSED file are required.

`partial/` is **not** loaded by the classifier. It holds labelled
partially-open frames used as held-out test data for the gate travel measure.

Filenames record the lighting condition and the gate state, for example
`Night-partial2.jpg`. Keep that convention: the tests and the travel
calibration both rely on day, IR and night being represented at each endpoint.

Known gap: leave-one-out testing fails on all three closed references, so
lighting outside these conditions is not yet covered. See the Gate travel
section of the app README.
