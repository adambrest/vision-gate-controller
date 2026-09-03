# Reference frames

`open/` and `closed/` are loaded by the classifier. Every supported image file
in them is used; there is no filename allowlist. At least one OPEN and one
CLOSED file are required.

`partial/` is **not** loaded by the classifier. It holds labelled
partially-open frames used as held-out test data for the gate travel measure.

Filenames record the lighting condition and the gate state, for example
`Night-partial2.jpg`. Keep that convention: the tests and the travel
calibration both rely on day, IR and night being represented at each endpoint.

Label carefully. Three frames added in 1.1.11 were labelled as endpoints when
they were partially open, which corrupted calibration until it was caught.
When in doubt, put the frame in `partial/`, which is never used for training.

Known gap: leave-one-out testing still fails on one closed reference, so
lighting outside these conditions is not fully covered. Open references
generalise cleanly. Adding frames measurably improves this - tightening the gate
polygon took leave-one-out from 3/6 to 5/6 - so keep adding endpoint frames
for conditions that misclassify. Check with:

    python -m tools.classify path/to/frames --expect closed

See the Gate travel section of the app README.
