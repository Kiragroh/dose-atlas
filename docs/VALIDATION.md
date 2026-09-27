# Evidence and limitations

## Development evidence, not an external test set

Original cohort: ten single-fraction cranial SRS reference cases, 82 targets, 5–11 targets/case, prescriptions 18–20 Gy and voxelized target volumes 0.109–10.888 cm³. All voxels of each held-out patient remain outside training. The deployed base model was subsequently fitted on all ten cases.

| Equal-weight case mean | Raw HGB | Sigma 1 mm |
|---|---:|---:|
| Target voxel MAE (Gy) | 0.842 | 1.297 |
| 0–5 mm outside MAE (Gy) | 1.614 | 1.594 |
| 5–15 mm outside MAE (Gy) | 1.139 | 1.136 |
| 15–35 mm outside MAE (Gy) | 0.712 | 0.711 |
| Target D98 MAE: target mean within each case (Gy) | 1.877 | 0.422 |
| Outside-target, domain-limited V12 MAE (cm³) | 2.121 | 2.004 |

Regularization worsened target voxel MAE in all ten cases, despite improved D98 error and appearance. Its width was selected after examining the development cohort. It is not independently validated postprocessing. See [regularization audit](continuity_validation.md).

The largest raw-model case V12 error was approximately 11.75 cm³. The trained distance-only baseline had target MAE about 0.97 Gy; the fixed analytic radial heuristic about 3.45 Gy. They are different baselines, not interchangeable comparisons.

The calibration analysis used internal patient-level folds and found a deployment scale of 1.04. It was performed after observing development-cohort bias. Its equal-target results are not interchangeable with the case-macro numbers above. Predicted conformity remained optimistic. Details are in [OPEN_METHOD.md](OPEN_METHOD.md).

The distal extension has separate held-out sampled-voxel results, not independent full-organ DVH validation. Combining near and distal models and registering multiple plans does not inherit a demonstrated whole-brain accuracy from either component.

## What remains open

- Independent external validation across planning workflows and larger cohorts.
- Whole-organ DVH accuracy, especially near steep gradients and outside original target neighbourhoods.
- Native TPS agreement for small targets and contour/source-plane edge cases.
- Mixed Rx, larger numbers of targets and geometries outside the observed training support.
- Uncertainty calibration, longitudinal registration and cumulative-dose validation.
- Reproducibility of the original trained model without public weights/training data.

Do not turn these outputs into clinical constraints, vendor rankings, optimal-plan claims or patient RN probabilities. Software tests validate implementation contracts using synthetic inputs; they do not establish medical-device approval or clinical adequacy.
