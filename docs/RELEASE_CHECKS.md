# v0.1.0 source-release checks

2026-09-27, Python 3.13 on Windows. Separate clean source checkout, no trained weights installed.

- Full synthetic suite: **181 passed, 4 skipped in 11.14 s**. Skips require unavailable model weights/private fixtures; not counted as passes.
- Red/green release checks: absent-model demo, explicit missing-model status, complete source archive, no default admin grant; additionally tested image inclusion in the downloadable source archive.
- Browser at isolated loopback port: synthetic six-target demo completes, analytic/non-trained warning visible, target curves and comparison metrics present. No patient inputs used.
- JavaScript syntax checks: `static/app.js` and `static/i18n.js` passed.
- Citation metadata parsed as YAML; no DOI is claimed.
- Explicit source selection and staged-path inspection; no private directory, DICOM, clinical arrays, model weights, databases or runtime credentials staged. Targeted text scan found no copied clinical identifiers, user workspace paths or credential patterns in selected files. This is not a certified de-identification audit.
- Both generated illustrations were inspected; prompts and limitations are in `assets/PROVENANCE.md`.

Not validated here: Docker execution, clinical/TPS acceptance, native RTDOSE import, external predictive accuracy, or installation of private weights by another institution. GitHub CI status is reported separately by the repository workflow, not assumed from local tests. The existing hosted Dose Atlas and RN application were not redeployed.
