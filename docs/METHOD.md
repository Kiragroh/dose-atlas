# Method: geometry, not beam optimization

![Illustrative principle](assets/method-principle.png)

This image is an AI-generated schematic; anatomy, isodose surfaces and curves are not measured data. CT intensities are not inputs. The implementation uses empirical voxel DVHs, not the smooth illustrative sketch above.

## Base model

For prescription R, the unregularized estimate is `D_hat(x) = R * clip(F(phi(x)), 0, 2)`.
F is a histogram gradient boosting regressor with 90 iterations, at most 15 leaves, learning rate 0.075, L2 regularization 10, minimum leaf size 80, early stopping disabled and random seed 20260920.

Eight features in `model.py`:

1. Signed distance to the rasterized target surface (mm).
2. Equivalent-volume radius of the nearest target.
3. Distance divided by nearest radius.
4. Approximate distance to the second-nearest equivalent target sphere.
5. Sum of radial proximity contributions from all targets.
6. Number of targets.
7. Total target volume (cm³).
8. Relative depth inside the target.

Contours, not spheres, define target masks. Sphere approximations are used only for selected features. A half-voxel distance-boundary approximation is explicit in the code. Overlapping selected target masks are rejected; do not count a union and its constituents as separate targets.

Training uses prescription-normalized reference dose, with 4,000 samples per case in each distance band: inside, 0–5, 5–15 and 15–35 mm outside. Patient-held-out folds keep all voxels from a case together. DICOM dose-plan-structure references, not filenames, associate the training inputs.

## Variant ledger

| Variant | Processing | Evidence boundary |
|---|---|---|
| Raw near model | HGB and Rx scaling | Original ten-case patient-wise LOPO |
| Regularized | Gaussian sigma 1 mm on relative 3D field before Rx scaling | Postprocessing selected on the development cohort |
| Calibrated | Regularization plus model-bound scalar; original deployment factor 1.04 | Nested case folds within an exploratory reanalysis; not external validation |
| Distal overview | Separate distal HGB; smooth blend from 30 to 45 mm | Separate sampled distal-case evaluation, not full-organ validation |
| Weight-free GUI demo | Analytic radial heuristic on synthetic targets | UI demonstration only; no learned prediction or validation |

Regularization changes the actual dose field, not only the DVH appearance. Reference dose is untouched. DICOM export, display and metrics use the same result. Calibration is tied to the exact model hash and must never be copied onto a different model without reevaluation.

For mixed prescriptions, target voxels use their own Rx; outside targets the scaling field uses inverse-square distance weights. This is an exploratory heuristic. It does not extend the training evidence to heterogeneous prescriptions or multiple fractions. Global CI/GI are not reported for heterogeneous Rx.

## Geometry, support and metrics

- DICOM patient LPS coordinates; 1-mm base grid extending 35 mm around targets.
- Even-odd contour rasterization with explicit source-plane support where available; contour slab assumptions and resolution limitations remain visible.
- Physical-coordinate linear sampling of reference RTDOSE; unsupported samples remain missing, never silently zero-filled.
- Empirical DVH and numerical metrics from voxel values. Display curves may retain a bounded set of dose knots; metrics use the full arrays.
- V12/CI/GI refer to the supported target neighbourhood, not automatically whole brain. Organ values are descriptive only and omitted when support is incomplete.
- Local target territory assignment is a geometric evaluation convention, not a decomposition of physical dose contributions.

[Detailed formulas and historical implementation notes](OPEN_METHOD.md) cover local Paddick CI/GI, ring AUC, sampling, regularization and calibration. Those notes describe the original deployment; model availability in this public source release is governed by [MODELS.md](MODELS.md).

## Longitudinal RN integration

The companion private RN application integrates per-plan doses, organ-inclusive grids, structure-based rigid registration and voxelwise physical-dose summation. Its recent grid fix expands native model fields to cover inverse-transformed reference-anatomy bounds before summation. It does not change original dose values or replace unknown original support with zeros.

That application adapter, its clinical records and its case-specific outputs are **not included in v0.1.0**. `overview_model.py` exposes the distal surrogate, but the standalone GUI still uses the near-target path. The RN path uses near/distal blending and regularization, without the additional 1.04 web calibration factor. Do not call these interchangeable model versions.

Whole-organ grid coverage is not whole-organ dose accuracy. Structure-only registered sums are exploratory physical Gy sums, not clinically validated cumulative dose or BED/EQD2. The dose surrogate does not predict radionecrosis probability.

## Methods paragraph for manuscripts

> We developed Dose Atlas, a geometry-based dose surrogate for exploratory cranial single-fraction stereotactic radiosurgery analysis. Target contours were rasterized in a common DICOM patient coordinate system. Eight voxel-level geometric features describing target distance, size, multiplicity and spatial proximity were used by a histogram gradient boosting regressor to estimate prescription-normalized dose. Neither image intensity nor beam geometry was used at inference. Initial model development used ten treatment cases comprising 82 targets, with patient-wise leave-one-out evaluation and distance-stratified training samples. Predicted dose, dose-volume histograms and research dose exports were derived from the same voxel field. Spatial regularization and empirical calibration were evaluated as separate exploratory variants on the development cohort. These predictions approximate an observed reference workflow rather than a physical dose calculation or a deliverable treatment plan.

Before submission, identify the actual software commit, model hash, postprocessing variant, cohort and support domain used. Do not quote initial-development metrics as validation of another model, external population, distal extension or synthetic demo. The software citation does not replace citations for the underlying gradient-boosting method, DICOM standard and dosimetric metric definitions.
