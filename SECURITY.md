# Security and sensitive data

Do not post patient records, DICOMs, identifying anatomy, credentials or internal paths in public issues. Use synthetic reproductions. Report security concerns privately to the maintainer through the GitHub profile before public disclosure.

The default launch command is loopback-only. This repository is not a hardened multi-user clinical deployment. Hosted authentication/storage modules are optional and require a separate security review and configuration; do not expose an unconfigured local server publicly. `DOSE_ATLAS_ADMIN_EMAIL` has no default administrator grant. Do not disable endpoint protection or certificate validation.

Only load trusted, authorized model files: Python pickle/joblib deserialization can execute code. Hashes establish file identity, not trust. Browser/server identifier stripping is a limited allowlist transform, not certified de-identification; anatomy and geometry can remain sensitive. Authorized research weights and neutral dose/target illustrations are distributed, but no source DICOMs, CT, identifiers or clinical records. A blurred CT would not by itself establish anonymization; example backgrounds are mathematical phantoms.
