# Model availability, installation and training

## Source-only release

No patient-derived weights or clinical training data are distributed. The clean-checkout demo is an analytic synthetic illustration. An installed research model is required for predictions on uploaded contours. The public hosted demo uses its separately installed research model; it is not the weight-free local demo.

The runtime expects trusted authorized `artifacts/model.joblib` and a matching `artifacts/model_card.json`. For the calibrated option also supply matching `artifacts/dose_calibration.json`; it is hash-bound to the model. Never use a calibration card from a different model. Without calibration, select the regularized or raw variant rather than the calibrated default in the GUI.

Joblib/pickle can execute code: inspect provenance before loading. A hash verifies identity, not trust. The pinned runtime dependency file describes the original development environment; cross-version sklearn deserialization is not guaranteed. This repository currently provides no public download of the original weights. Request distribution terms from the project maintainer or train an independently documented model on authorized data.

## Training inventory

The current trainer expects a JSON array with three linked rows per case:

```json
[
  {"patient_id":"synthetic-group-01", "stage":"Elements", "modality":"RTDOSE", "summation":"PLAN", "path":"/authorized/case01/dose.dcm", "plan_refs":["2.25.1001"]},
  {"patient_id":"synthetic-group-01", "stage":"Elements", "modality":"RTPLAN", "sop":"2.25.1001", "structure_refs":["2.25.1002"], "path":"/authorized/case01/plan.dcm"},
  {"patient_id":"synthetic-group-01", "stage":"Elements", "modality":"RTSTRUCT", "sop":"2.25.1002", "path":"/authorized/case01/structure.dcm"}
]
```

These are illustrative paths/UIDs, not downloadable patient data. Replace them with authorized local files and actual matching UIDs. `patient_id` is the grouping key; all plans from one person must stay in the same held-out group. The current preparation path expects exactly one PLAN dose, one matching plan and structure per group, one fraction and a uniform prescription explicitly linked to target ROI numbers in RTPLAN. `stage=Elements` is a legacy inventory selector, not proof of vendor or applicability to another workflow.

```shell
python train.py --fresh --inventory /authorized/inventory.json
```

Use an independent working copy for each training dataset. `--fresh` recomputes cached inputs; do not mix datasets or patient-level splits in an existing cache. The trainer creates private intermediate arrays and an aggregate model card; do not commit the private directory. A different dataset produces a different model and requires its own model card and validation. No redistributable dataset is included, so the original development results cannot be fully independently regenerated from this repository alone.

Optional development analyses, after preparing the required private caches:

```shell
python evaluate_continuity.py
python validate_calibration.py
python overview_model.py
```

`overview_model.py` trains a separate distal extension from existing caches and needs at least three case groups. It does not add RN outcomes, clinical uncertainty estimates or OAR-avoidance learning. It is not wired into the standalone GUI in this release.

## Deployment

The documented loopback launch needs no Supabase settings and no cloud account. The optional cloud modules require their own authentication/storage configuration and security review; they are not a turnkey clinical hosting service. The SQL schema is supplied for code/test completeness, not applied automatically.

An optional source-only container starts the synthetic demo without weights:

```shell
docker build -t dose-atlas .
docker run --rm -p 127.0.0.1:18766:8000 dose-atlas
```

For trusted model inference, mount an authorized artifacts directory read-only at `/app/artifacts`. Container execution must be validated in your target environment; local Python tests do not certify Docker or clinical deployment.
