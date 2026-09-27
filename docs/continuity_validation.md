# Spatial regularization audit

The raw histogram gradient boosting regressor produces repeated dose values,
including large plateaus within targets. A fixed Gaussian filter now regularizes
the actual three-dimensional normalized prediction before prescription scaling.
The resulting field is used consistently for metrics, empirical DVHs, display
planes and RTDOSE export. The observed reference dose is not filtered.

The default physical standard deviation is **1.0 mm**, corresponding to a Gaussian
FWHM of 2.355 mm. The raw model output is available as an explicit alternative
(sigma 0), which preserves all original voxel values. Implementation uses a positive normalized kernel, reflect
boundary extension and four-standard-deviation truncation. This preserves
constant fields and input bounds; prescription scaling remains linear. The
finite sampled kernel reduces plateaus but does not guarantee unique doses at
every voxel. No DVH interpolation, jitter, target-dose renormalization or
reference-dependent correction is applied.

This width is a development choice after visual inspection and comparison of
0.5 and 1.0 mm on the existing development cohort. The 0.5-mm version reduced
exact dose ties but retained visible clusters, so exact ties were an inadequate
acceptance metric. The existing ten patient-held-out prediction caches were evaluated
without retraining or changing the reference, geometry or support domain.

| Quantity | Raw HGB | Spatially regularized |
|---|---:|---:|
| Target voxel MAE, case mean (Gy) | 0.842 | 1.297 |
| 0–5 mm outside target MAE, case mean (Gy) | 1.614 | 1.594 |
| 5–15 mm MAE, case mean (Gy) | 1.139 | 1.136 |
| 15–35 mm MAE, case mean (Gy) | 0.712 | 0.711 |
| Target D98 absolute error, target mean then case mean (Gy) | 1.877 | 0.422 |
| Outside-target V12 domain absolute error, case mean (cc) | 2.121 | 2.004 |
| Largest equal-dose plateau per target, mean over 82 targets (%) | 25.300 | 0.302 |
| Largest equal-dose plateau in any target (%) | 61.702 | 0.917 |
| Largest concentration in any 0.05-Gy interval, target mean (%) | 32.755 | 2.908 |
| Largest concentration in any 0.05-Gy interval, worst target (%) | 62.838 | 4.819 |

**Target voxel MAE worsened in all ten cases.** The improved smoothness and D98
error therefore come with an accuracy tradeoff, not a global predictive
improvement. Case-mean D98 error improved in all ten cases. One of 82 individual
targets had an increased D98 error versus raw output (0.055 Gy). V12 absolute
error increased in five cases, with a largest increase of 0.207 cc.

The 0.05-Gy concentration uses a sliding closed interval, avoiding arbitrary
histogram-bin origins. Reference dose has a mean of 2.695% and worst value of
4.444%; its field remains untouched. For 22 small targets below 0.25 cc,
target-mean D98 absolute error was 2.312 Gy raw, 1.311 Gy with sigma 0.5 mm and
0.558 Gy with sigma 1.0 mm. This is a descriptive subgroup, not a tuned cutoff
or validation of clinical adequacy.

These are exploratory results from the same development cohort. They are not
independent validation, a guarantee of a physically deliverable dose, evidence
of an optimal plan, or native TPS acceptance. Mixed-prescription scaling remains
exploratory and was not validated by these uniform-prescription caches. The
original raw estimator and held-out caches remain available for reproducibility.

Run `python evaluate_continuity.py` in the project directory to regenerate the
private per-case audit and sanitized `artifacts/continuity_validation.json`.
The public artifact contains no case geometry or source identifiers. Targeted
tests verify bounds, constant-field preservation, prescription linearity and
one-field consistency through compute, DVH, displayed slices and DICOM export.

Implementation reference: [SciPy gaussian_filter documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.gaussian_filter.html).
