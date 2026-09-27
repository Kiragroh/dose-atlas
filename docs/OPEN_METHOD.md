# Offene Methode: Geometrie, Dosis und Ringvergleich

## Lokale Planindizes (Erweiterung 21.09.2026)

Die 35-mm-Targetdomäne wird mittels euklidischer Distanz zum nächsten
Target-Maskenvoxel disjunkt zugeordnet. Gleichstände werden deterministisch
durch scipy.ndimage.distance_transform_edt aufgelöst. Die Zuordnung benutzt
keine Dosisinformation und bleibt zwischen Plänen identisch. Sie ist eine
explizite lokale Auswertungskonvention, keine Trennung physikalischer
Dosisbeiträge und keine Rekonstruktion vollständiger Single-Lesion-Pläne.

Je Target werden in seiner zugeordneten Region, mit seiner eigenen Rx, berechnet:

- Paddick-CI = Überlappungsvolumen² / (Targetvolumen × V100%-Isodosenvolumen).
- Anzeige 1/CI (ideal 1), nicht der RTOG-Index V100%/Targetvolumen.
- GI = V50%-Isodosenvolumen / V100%-Isodosenvolumen.
- Lokales V12 = Volumen mit Dosis >=12 Gy außerhalb **aller** ausgewählten
  Targets in dieser Region. Keine Gehirnmaskierung, kein Doppelzählen.

Fehlende Dosisunterstützung irgendwo in einer lokalen Region macht deren lokale
Kennwerte für beide Pläne ungültig. Ein nullwertiger Nenner beziehungsweise
CI=0 ergibt keinen Kehrwert; es wird keine Unendlichkeit oder Ersatznull
gespeichert. Mittelwerte verwenden je Kennwert die gleichen Targets mit
beidseitig definiertem Wert. Angezeigt werden mean(1/CI) und mean(GI),
ungewichtet, samt Anzahl gültiger Targets; mean(1/CI) ist nicht 1/mean(CI).
Ohne Referenz wird über die definierten Vorhersagewerte gemittelt.

Definitionsquellen: [Paddick/Lippitz, GI](https://pubmed.ncbi.nlm.nih.gov/18503356/)
und [Conformity indices for 170 targets](https://pmc.ncbi.nlm.nih.gov/articles/PMC5718686/).
Die örtliche Zuordnung und Beschränkung auf 35 mm sind unsere Erweiterung.

Der zuschaltbare synthetische Kopf in 2D/3D ist ein Ellipsoid mit schematischer
Nase und Ohren. Zentrum und Mindestabmessungen werden aus Targetgrenzen
abgeleitet. Er enthält keine patientenspezifische äußere Anatomie, HU oder
Gehirnsegmentierung und beeinflusst keinerlei Dosis- oder Volumenberechnung.

Stand: 21.09.2026. Vollständig lokale, nachvollziehbare Forschungsimplementierung.

## 1. Dosisvorhersage

Für eine Position x und gemeinsame Verschreibung R lautet das Modell

    D_hat(x) = R * clip(F_theta(phi(x, Targets)), 0, 2).

F ist ein Histogram Gradient Boosting Regressor mit festgelegten Hyperparametern
in `model.new_regressor`. Die acht Eingaben phi sind in `model.FEATURE_NAMES`
aufgelistet. Strukturkonturen allein bestimmen diese Eingaben; beobachtete Dosis
wird ausschließlich als Trainingsziel bzw. unabhängige Vergleichsdosis verwendet.
Das Modellgewicht ist keine einfache geschlossene Formel. Eine transparente,
untrainierte radiale Vergleichsheuristik ist zusätzlich in `model.baseline`:

    außen: D/R = exp(-d / (2.5 + 0.35*r))
    innen: D/R = 1 + 0.18 * min(t/r, 1)

d ist der Abstand zur rasterisierten Targetoberfläche in mm, t die Eindringtiefe
und r der volumenäquivalente Radius des nächsten Targets. Dies ist eine Heuristik,
kein physikalisches Bestrahlungsmodell. Der trainierte reine Abstandsvergleich in
`train.py` ist davon getrennt. Seeds, Sampling, Training, Foldbildung und
Validierung sind im Quellcode und README festgelegt.

## 2. Exploratives Ringverhältnis

Anregung ist die vom Nutzer bereitgestellte Vortragsfolie „Brain Sparing Index
(BSI)“: Quotient AUC_AI / AUC_plan im relativen Dosisintervall 0.5–0.8 Rx.
Autor, Originalpublikation, Ringbreite und genaue Maskierungsregeln konnten
aus der Folie nicht festgestellt werden. Die vom Nutzer berichtete Gamma-Knife-
Trainingsbasis ist nicht unabhängig verifiziert. Wir behaupten weder eine
identische Reimplementierung noch eine Validierung dieses fremden Modells.
Das Foto ist nicht Teil des Veröffentlichungspakets.

Unsere eigene, explizite Definition je Target T_i ist:

    S_i = {x außerhalb aller ausgewählten Targets: Abstand(x,T_i) <= 10 mm}
    A_i(D) = Integral von u=0.5 bis 0.8 über Volumen{x in S_i: D(x)/R >= u} du
    Q_i = A_i(D_hat) / A_i(D_plan).

Die Ringgeometrie basiert auf der ursprünglichen Konturmaske, nicht auf der
äquivalenten Kugel. Der Abstand verwendet das in `model.py` dokumentierte
Halbvoxel-Oberflächenmodell. Die 10 mm sind eine vorläufige Designentscheidung,
kein klinisch validierter Optimalwert. Es gibt keine Beschneidung am Gehirn;
deshalb nennen wir dies **geometrisches Ringverhältnis**, nicht automatisch
einen Brain-Sparing-Index. Ringe können überlappen und werden nicht summiert.

Für voxelweise konstante Dosis ist die Integralformel exakt gleich

    A_i(D) = voxel_volume_cc * sum(clip(D(x)/R - 0.5, 0, 0.3), x in S_i).

So ist keine grobe DVH-Abtastung erforderlich. A hat die Einheit cm³, da über
relative Dosis integriert wird. Integration über Gy ergäbe R*A und bei derselben
Verschreibung denselben Quotienten. Die algebraische Normierung bedeutet keine
klinische Unabhängigkeit von Verschreibung, Fraktionierung oder Technologie.

- Q=1: gleiche Ring-AUC; die räumlichen Dosisfelder können trotzdem verschieden sein.
- Q>1: kleinere Ring-AUC im hochgeladenen Plan als im Modellbenchmark.
- Q<1: größere Ring-AUC im hochgeladenen Plan.
- Fehlende Dosisunterstützung, angeschnittener Ring oder Nenner null: kein Quotient.
- D98 und V100 stehen daneben. Ein unterdosiertes Target darf nicht allein wegen
  geringerer Ringdosis als bessere Planung gelten. Keine automatische Rangliste.

Die Formel sättigt oberhalb 80% und ignoriert Dosis unterhalb 50% Rx. Sie ersetzt
weder Ganzhirn-V12 noch OAR-Metriken, Konformität, Hotspots oder Applizierbarkeit.
Die zehn lokalen Testfälle verwenden jeweils die Vorhersage aus einem Modell,
das ohne den betreffenden Patienten trainiert wurde. Es ist ein beobachteter
Workflow-Benchmark und kein Nachweis der bestmöglich erreichbaren Dosis.

## 3. Nachbauen und unabhängig prüfen

1. Abhängigkeiten aus `requirements.txt` installieren; `python -m pytest -q`.
2. Eigene autorisierte PLAN-RTDOSE/RTPLAN/RTSTRUCT-Paare zusammenstellen. Das
   Inventarschema und die Auswahl sind in `train.py`/`local_cases.py` nachvollziehbar.
3. `python train.py --fresh --inventory PFAD_ZUM_INVENTAR` ausführen. Patienten
   statt Voxel trennen; keine Auswahl an zurückgehaltenen Testfällen vornehmen.
4. `python check_resolution.py` und die lokalen API-Prüfskripte ausführen.
5. Für andere Verfahren, Verschreibungen oder Gehirnmasken neue unabhängige
   Validierung durchführen. Die jetzige Kohorte umfasst Elements-Einzeit-SRS,
   18–20 Gy. Gamma-Knife-Erfahrung ist nicht automatisch übertragbar.

Der eigene Programmcode und die Dokumentation stehen unter MIT (`LICENSE`).
Abhängigkeiten behalten ihre eigenen Lizenzen. DICOMs, private Caches, das Foto
und lokal trainierte Modellgewichte erhalten dadurch keine Datenfreigabe.
`build_package.py` erstellt das lokale vollständige Hostingpaket sowie zusätzlich
ein reines Quellpaket ohne Fallinhalte oder trainiertes Modellgewicht. Mit eigenen
berechtigten Daten kann dieses Quellpaket trainiert werden. Ein öffentliches
Repository wurde in diesem Arbeitsschritt nicht angelegt.

## HDSS source planes and upload responsiveness

RTSTRUCT SourcePixelPlanesCharacteristicsSequence (3006,004A) is retained by
both allowlist passes. Direction cosines, origin, slice spacing and frame count
define each ROI's native planes. Contours are validated against those planes;
the rasterizer transforms LPS voxel centres into the native basis and fills
half-open slabs with even-odd polygon inclusion. Missing contour planes remain
empty. Explicit spacing also permits a single contour plane. Without explicit
source geometry, only the existing axial contour interpretation is supported.

This implements the [DICOM source-plane geometry](https://dicom.nema.org/medical/dicom/2021d/output/chtml/part03/sect_C.8.8.6.4.html),
not a native-resolution HDSS DVH engine. Prediction and quantitative comparison
remain on the 1-mm model grid. Submillimetre contours may therefore differ from
TPS values; this input extension adds no model training or clinical validation.

Browser parsing, de-identification and original-case export rebinding execute
in a self-hosted Web Worker. During inspection the structure/dose selectors
remain usable. A serialized latest-selection queue discards stale results and
releases stale original-metadata contexts. Prediction locks the inputs.


## Separate target prescriptions

A common value is the fallback; an explicit ROI-number to Gy map overrides it.
The UI offers an explicit apply-to-all action and individual editable target
values. PTVs have selection priority; GTVs are suggested only when no PTV exists.
The dose-derived hypothesis remains ceil(D98/Gy), provided Dmean > 10 Gy.
Neither hypothesis nor editable range constitutes a confirmed prescription.

For unequal target prescriptions R_i, the normalized model prediction stays
unchanged. The relative field first receives the fixed spatial regularization described below. Inside T_i its scale is exactly R_i. Outside all targets:

    w_i(x) = 1 / max(EDT_i(x), grid_spacing/2)^2
    R(x) = sum_i(w_i(x) * R_i) / sum_i(w_i(x))
    D_hat(x) = normalized_prediction(x) * R(x)

EDT uses the selected target voxel mask in physical millimetres. Equal target
values use the original scalar multiplication path exactly. The mixed-Rx
extension is an explicit heuristic, not training on heterogeneous prescriptions
or a physical optimization. Values outside the observed 18–20 Gy range are
extrapolations. Positive values up to 100 Gy are an input sanity limit, not a
clinical recommendation or supported clinical range.

Target V100 and ring AUC use each target's own prescription. Global CI/GI are
undefined for mixed prescriptions and are returned as null; absolute V12
remains a descriptive domain-limited value. Organ V100 is also undefined with
mixed target prescriptions. DICOM export retains the case's coordinate frame
and describes heterogeneous prescriptions in research derivation metadata.

## Empirical display DVHs

Each retained dose value is emitted twice: cumulative volume for D >= d and
its right-hand limit for D > d. Duplicate x coordinates retain real vertical
steps, including tied predictions from the tree model. The curves are not
smoothed and are not native TPS DVHs.

At most 201 distinct dose knots are retained. Larger arrays use evenly spaced
voxel ranks, including both endpoints; omitted volume between retained knots
is bounded by ceil((N-1)/200)/N (about 0.5 percentage points). Small arrays and
small numbers of distinct model values are represented exactly. All numerical
metrics use the full underlying voxel arrays, independently of this bounded
display representation. The default view compares one selected target; an
explicit all-curves option remains available.


## Spatial regularization and detailed structure display

Users can select the base model (sigma=0) or the regularized prediction
(default sigma=1 mm, FWHM 2.3548 mm, reflect boundary, support truncated at
4 sigma). The Gaussian acts on the normalized predicted 3-D volume before
prescription scaling. Reference dose and original contour geometry remain
unchanged. DVHs, statistics, slice views and exported RTDOSE use the same
resulting field. There is no separate cosmetic DVH smoothing. Discrete voxel
sampling remains; complete absence of small steps is not claimed.

This development choice followed visual inspection and comparison of 0.5-mm
and 1-mm regularization on ten cached patient-held-out model predictions.
Postprocessing was therefore selected on the existing development cohort.
It is not an independent validation of this output variant. The raw model and
caches are retained; users can still select the unregularized base output.

The maximum target-volume fraction within any 0.05-Gy window fell on average
from 32.76% to 2.91% (reference 2.70%). Target voxel MAE worsened from 0.842 to
1.297 Gy in aggregate, whereas case-macro target D98 absolute error improved
from 1.877 to 0.422 Gy. Different metrics trade off; no overall superiority is
claimed. Details and adverse individual changes:
[continuity_validation.md](continuity_validation.md).

Structure visualization uses native axial contour polygons with a 0.01-mm
polyline error bound instead of a fixed 48-vertex cap. All target contour planes
are retained in 3D. Oblique source slabs are sampled at 0.2 mm in displayed
axial planes; a work budget may increase that display spacing for exceptionally
large inputs. Isodoses are extracted from the full 1-mm dose grid even when
the heatmap preview is decimated. Open isolines remain open at unsupported
boundaries. None of these display paths change the quantitative grid, and
they are not archived in Supabase.


## Mehrere Vergleichsdosen

Zur Basisanalyse können bis zu drei weitere PLAN-RTDOSE-Dateien hinzugefügt
werden. Jede wird im Browser mit der Originalstruktur gemeinsam bereinigt und
auf übereinstimmenden Referenzrahmen geprüft. Targets, bestätigte Rx und Modell-
variante bleiben konstant. Die API analysiert die Paare nacheinander; jede
Analyse ist ein separater Archivlauf. Die gemeinsame Auswahl und Darstellung
mehrerer Ergebnisse besteht nur in der Sitzung. Downloads gehören zur Basis-
analyse. Fehlende Dosisunterstützung wird nicht durch Nullen ersetzt.

Die aktive Referenz steuert Isodosen, DVHs, Kennwert- und Ringtabellen. Lokale
Vorhersagekennwerte können bei anderem Referenzfeld wegen der gemeinsamen
Dosisunterstützung fehlen oder wieder verfügbar sein; die Vorhersagedosis
selbst ändert sich dabei nicht. Plotpunkte der Basisvorhersage und der jeweiligen
Originalpläne sind entsprechend beschriftet. Sie belegen keine klinische
Überlegenheit einer Version.


## Observed conformity optimism (2026-09-22)

An exploratory re-evaluation of cached patient-held-out predictions for all
10 development cases (82 targets) found mean inverse Paddick CI 1.0663 for
the current 1-mm-smoothed prediction versus 1.2801 for the reference.
69/82 predictions, but only 1/82 references, were below 1.1. These are
equal-target means, not an independent external validation. Unsmoothed
predictions were also optimistic (mean 1.0389; 70/82 below 1.1).

The separately inspected 24-target example had 18 jointly evaluable targets:
mean 1.0503 predicted versus 1.2491 reference (14/18 versus 0/18 below 1.1).
Six targets lacked complete shared dose support. For these 18 evaluable
targets, prescription-isodose components did not extend beyond their assigned
territories within the evaluated domain. Territory clipping did not explain
the discrepancy. Coverage averaged 99.359% versus 95.356%; prescription-dose
spill outside target averaged 4.357% versus 18.446% of target volume.

The reciprocal Paddick formula is unchanged and has no artificial 1.1 floor.
There is no target-coverage renormalization. Geometric dose regression can
average directional plan irregularity into an overly contour-following field;
this is a plausible explanation, not an isolated causal proof. Smoothing has
case-dependent effects and disabling it does not remove the cohort bias.
Predicted conformity must therefore not be interpreted as achievable optimum
or evidence that the uploaded plan or planning system is inferior. Correcting
this requires independently validated dose-field modelling, not relabelling
or clamping the displayed CI.


## Empirical dose calibration v1 (2026-09-22)

The new default for uploads is `Empirisch kalibriert (1 mm)`. The previous
1-mm prediction and unregularized base model remain selectable. This is a
small population-level adjustment, not a change of prescription or a minimum
allowed CI. The complete predicted field is computed as

`D_new(x) = Rx_scale(x) * 1.04 * Gaussian_sigma_1mm(HGB(geometry(x)))`.

The uploaded reference never enters this transformation. Isodoses, DVHs,
metrics and RTDOSE use the same corrected field. RTDOSE DerivationDescription,
result metadata, metadata archive and Excel record the factor. Existing
archived results keep their original values. Mixed-Rx interpolation is
unchanged. The synthetic demo is uncalibrated.

### Selection and evaluation

A bounded scalar grid 1.00–1.08 in steps of 0.01 was evaluated using a balanced
objective for inverse CI, GI, local V12, D98, coverage and Dmean. On calibration
cases, D98 case-macro MAE was allowed to increase by at most 0.10 Gy and
GI/V12/coverage MAE could not worsen. These are development rules, not clinical
tolerances. No parameter was fitted to the displayed 24-target case.

For each outer held-out patient, HGB was fitted on the other nine patients.
Three inner case folds (six training / three calibration patients each)
provided independent calibration predictions on those nine patients. The
scalar was chosen using only these inner predictions and references, then
applied to the outer prediction. The outer test reference was used only for
assessment. Selected factors were 1.03 (two folds) and 1.04 (eight folds).
For deployment, all ten existing LOPO predictions selected the fixed 1.04
factor for the all-case-trained base model. Local real-test-plan loading uses
the corresponding outer-fold factor, not this deployment factor.

This is an exploratory re-analysis of the same ten development cases after
observing their bias; it is not untouched or external validation. Direction
features and wider isotropic blur were explored but not included in deployment.
The model weights, geometry features and 1-mm Gaussian are unchanged.

Equal-target summary across 82 targets, before → nested held-out calibration:

| Metric | Before | Calibrated | Reference |
|---|---:|---:|---:|
| Mean inverse Paddick CI | 1.0663 | 1.0977 | 1.2801 |
| Inverse CI below 1.1 | 69/82 | 48/82 | 1/82 |
| Mean GI | 4.7882 | 4.5849 | 4.2392 |
| D98 MAE / Gy | 0.4281 | 0.4989 | — |
| Dmean MAE / Gy | 1.3531 | 0.7013 | — |
| Local V12 MAE / cm³ | 0.2652 | 0.2135 | — |
| Coverage MAE / percentage points | 3.2894 | 1.4019 | — |

The conformity bias is reduced modestly, not eliminated: most calibrated
predictions still have inverse CI below 1.1. Individual D98 errors can grow.
Do not interpret the calibrated dose as an achievable optimum or evidence of
software superiority. Reproduce with `python validate_calibration.py` against
the authorized private case caches; aggregate results and the model hash are
in `artifacts/dose_calibration.json`. No clinical input files are distributed.


For the separately inspected 24-target case (18 jointly evaluable targets),
the fixed 1.04 deployment factor changes mean inverse CI from 1.0503 to
1.1839 (reference 1.2491); counts below 1.1 change from 14 to 2. Its mean GI
changes from 5.7978 to 5.4975 (reference 8.1774), worsening GI agreement in
this example. It was not used for calibration and is outside the training
case target-count range; it is an illustrative extrapolation, not validation.
