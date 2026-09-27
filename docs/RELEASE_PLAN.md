# Public source release plan

Approved scope: standalone research source/GUI, method documentation, generated synthetic illustrations and GitHub publication. This release does not publish patient-derived model weights, DICOMs or RN-register contents. Live systems remain untouched.

Implementation stays in this separate checkout. Preserve the existing model, geometry, dose and metrics code. Add only portability safeguards: a clearly labelled analytic synthetic demo when trained weights are absent, explicit missing-model status, configurable queue administrator with no default grant, complete source download and a container that starts without private files. Document model installation/training and the remaining RN integration boundary.

Verification order:

1. Add `tests/test_public_release.py`; run it and observe missing-model/source-export failures before fixes.
2. Patch `app.py` and `job_queue.py` only for these release safeguards; existing clinical inference never falls back to a heuristic.
3. Run all synthetic tests from this checkout without weights. Syntax-check JavaScript and validate citation metadata.
4. Start an isolated loopback instance; inspect demo, model status, image assets and source archive.
5. Inspect the exact staged file list and scan for private paths, real identifiers and credentials. Generated images contain no patient references.
6. Commit, push to `Kiragroh/dose-atlas`, verify remote files and CI, then create a source-only versioned prerelease. No DOI is invented; archive registration remains separate.
