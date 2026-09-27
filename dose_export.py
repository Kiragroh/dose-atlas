"""In-memory RTDOSE export for explicitly requested, linked research comparison.

Exports the full quantitative box unchanged, not the decimated browser slices.
The box includes unvalidated extrapolation beyond the 35-mm evaluation domain;
unknown/nonfinite voxels are rejected, never replaced by zero. Original study
and frame UIDs deliberately retain linkage even if patient identity is omitted.

DICOM PS3.3: A.18.3 (IOD), C.8.8.3 (dose), C.7.6.2 (image plane), C.7.6.6
(multi-frame). OTHER is an application-defined extended DoseSummationType;
TPS import support must be verified, and no fictional RTPLAN is constructed.
"""
from datetime import datetime
from io import BytesIO
import copy
import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.tag import Tag
from pydicom.uid import ExplicitVRLittleEndian, RTDoseStorage, RTStructureSetStorage, UID, generate_uid
from pydicom.valuerep import format_number_as_ds
from geometry import InputError, safe_dicom_diagnostics, structure_frame

RESEARCH_LABEL = 'RESEARCH MODEL PREDICTION - NOT FOR TREATMENT'
DOMAIN_NOTICE = ('Full quantitative model box; beyond 35 mm from selected targets '
                 'is unvalidated extrapolation. No whole-brain DVH interpretation. '
                 'No executable plan. Original study/frame linkage retained.')


def _uid(value):
    value = str(value or '')
    if not value or not UID(value).is_valid:
        raise InputError('Export benötigt gültige ursprüngliche DICOM-Referenzkennungen.')
    return value


@safe_dicom_diagnostics
def export_prediction(pred, grid, source_structure, targets, rx, *, preserve_patient_identity=True, target_prescriptions=None, regularization_sigma_mm=None, calibration_scale=1.):
    """Return RTDOSE bytes; never writes a file or alters the source dataset.

    Caller must pass original RTSTRUCT and the finite full-resolution dose in
    Gy. By default PatientName/PatientID are retained for original-patient TPS
    matching. Setting preserve_patient_identity=False leaves these fields empty
    but does NOT anonymize the retained study/frame UIDs or anatomy. Caller must
    disclose this linkage separately from the upload anonymization setting.
    """
    a = np.asarray(pred)
    shape = tuple(grid.shape)
    origin = np.asarray(grid.origin, dtype=float)
    spacing = float(grid.spacing)
    if (a.ndim != 3 or a.shape != shape or any(n < 2 or n > 65535 for n in shape)
            or a.size > 12_000_000):
        raise InputError('Export benötigt das vollständige unterstützte 3D-Dosisraster.')
    if (origin.shape != (3,) or not np.isfinite(origin).all()
            or not np.isfinite(spacing) or spacing <= 0):
        raise InputError('Ungültige räumliche Exportgeometrie.')
    if (not np.isfinite(a).all() or (a < 0).any()
            or not np.isfinite(rx) or rx <= 0):
        raise InputError('Export benötigt endliche, nichtnegative Dosiswerte und gültige Verschreibung.')
    if source_structure.get('Modality') != 'RTSTRUCT':
        raise InputError('Export benötigt die ursprüngliche RTSTRUCT-Zuordnung.')
    available = {int(r.ROINumber) for r in source_structure.get('StructureSetROISequence', [])}
    if (not targets or len(targets) > 40 or len(set(targets)) != len(targets)
            or any(type(n) != int or n not in available for n in targets)):
        raise InputError('Export benötigt eine eindeutige gültige Zielauswahl.')
    frame = _uid(structure_frame(source_structure, targets))
    study = _uid(source_structure.get('StudyInstanceUID'))

    # A conservative uint32 headroom also covers decimal-string scaling roundoff.
    scaling = float(format_number_as_ds(max(float(a.max()) / (2**32 - 1024), 1e-6)))
    encoded = np.rint(a.astype(np.float64) / scaling)
    if not np.isfinite(encoded).all() or encoded.max() > 2**32 - 1:
        raise InputError('Dosiswerte können nicht sicher als RTDOSE kodiert werden.')
    pixels = encoded.astype('<u4').tobytes(order='C')

    sop = generate_uid()
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = RTDoseStorage
    meta.MediaStorageSOPInstanceUID = sop
    ds = FileDataset(None, {}, file_meta=meta, preamble=b'\0' * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.SpecificCharacterSet = 'ISO_IR 192'
    ds.SOPClassUID = RTDoseStorage
    ds.SOPInstanceUID = sop
    ds.StudyInstanceUID = study
    ds.SeriesInstanceUID = generate_uid()
    ds.FrameOfReferenceUID = frame
    ds.Modality = 'RTDOSE'
    ds.SeriesNumber = 9001
    ds.InstanceNumber = 1
    ds.ImageType = ['DERIVED', 'SECONDARY']
    ds.SeriesDescription = RESEARCH_LABEL
    # DoseComment is LO (64 characters); keep the full method in ST below.
    ds.DoseComment = RESEARCH_LABEL + '; >35mm unvalidated'
    values = target_prescriptions if target_prescriptions is not None else [float(rx)]*len(targets)
    if (len(values)!=len(targets) or not values or
            any(type(v) not in (int,float) or not np.isfinite(v) or not 0<v<=100 for v in values)):
        raise InputError('Ungültige Target-Verschreibungen für den Dosisexport.')
    rx_description = ('input Rx '+str(float(values[0]))+' Gy' if len(set(values))==1 else 'heterogeneous target Rx '+', '.join(f'ROI {n}: {v:g} Gy' for n,v in zip(targets,values))+'; exploratory inverse-square distance blend')
    ds.DerivationDescription = (f'Geometry-only research model; {rx_description}. '
                                + DOMAIN_NOTICE)
    if regularization_sigma_mm is not None:
        if regularization_sigma_mm not in (0.,1.):raise InputError('Nicht unterstützte Vorhersageregularisierung.')
        ds.DerivationDescription += (' Normalized model field Gaussian-regularized sigma=1 mm before target Rx scaling; reference unchanged.' if regularization_sigma_mm else ' Unregularized base-model output; reference unchanged.')
    if type(calibration_scale) not in (int,float) or not np.isfinite(calibration_scale) or not 1 <= calibration_scale <= 1.08:
        raise InputError('Ungültiger Kalibrierfaktor für den Export.')
    if calibration_scale != 1.:
        ds.DerivationDescription += f' Empirical cohort dose calibration v1, multiplier {calibration_scale:g}; Rx unchanged; not a deliverable optimum.'
    ds.Manufacturer = 'Dose Atlas Research'
    ds.ManufacturerModelName = 'Geometry-only dose surrogate'
    ds.SoftwareVersions = 'research-02' if regularization_sigma_mm is not None else 'research-01'
    if calibration_scale != 1.:ds.SoftwareVersions = 'research-03'
    for keyword in ('PatientName', 'PatientID'):
        setattr(ds, keyword, str(source_structure.get(keyword, '')) if preserve_patient_identity else '')
    # Mandatory Type 2 values are present; unnecessary clinical text is not copied.
    for keyword in ('PatientBirthDate', 'PatientSex', 'StudyDate', 'StudyTime',
                    'ReferringPhysicianName', 'StudyID', 'AccessionNumber',
                    'OperatorsName', 'PositionReferenceIndicator'):
        setattr(ds, keyword, '')
    if preserve_patient_identity and source_structure.get('IssuerOfPatientID'):
        ds.IssuerOfPatientID = str(source_structure.IssuerOfPatientID)
    if source_structure.get('PatientIdentityRemoved') == 'YES':
        ds.PatientIdentityRemoved = 'YES'
        ds.DeidentificationMethod = 'Source identity removal retained; geometry and UID linkage retained'
    now = datetime.now().astimezone()
    ds.ContentDate = ds.InstanceCreationDate = now.strftime('%Y%m%d')
    ds.ContentTime = ds.InstanceCreationTime = now.strftime('%H%M%S.%f')
    ds.TimezoneOffsetFromUTC = now.strftime('%z')
    ds.DoseUnits = 'GY'
    ds.DoseType = 'PHYSICAL'
    ds.DoseSummationType = 'OTHER'
    ds.Rows, ds.Columns = shape[1:]
    ds.NumberOfFrames = shape[0]
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.ImagePositionPatient = [format_number_as_ds(float(v)) for v in origin]
    ds.PixelSpacing = [format_number_as_ds(spacing)] * 2
    ds.SliceThickness = format_number_as_ds(spacing)
    ds.GridFrameOffsetVector = [format_number_as_ds(float(k * spacing)) for k in range(shape[0])]
    ds.FrameIncrementPointer = Tag(0x3004, 0x000C)
    ds.DoseGridScaling = format_number_as_ds(scaling)
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = 'MONOCHROME2'
    ds.BitsAllocated = ds.BitsStored = 32
    ds.HighBit = 31
    ds.PixelRepresentation = 0
    ds.PixelData = pixels
    ds['PixelData'].VR = 'OW'
    stream = BytesIO()
    pydicom.dcmwrite(stream, ds, write_like_original=False)
    return stream.getvalue()


@safe_dicom_diagnostics
def export_structure(source):
    """Complete an analysis RTSTRUCT for paired research export, without PHI recovery.

    PS3.3 C.8.8.5 and C.8.8.8 define the structure/observation attributes.
    Missing Type 2 interpretations remain empty rather than inferred from names.
    UNKNOWN is an extended defined ROI-generation term, not an assertion of a
    manual/automatic origin. No CT, image or study references are invented.
    Caller supplies the same StudyInstanceUID used by export_prediction.
    """
    if source.get('Modality') != 'RTSTRUCT':
        raise InputError('Strukturexport benötigt RTSTRUCT.')
    study = _uid(source.get('StudyInstanceUID'))
    rois = source.get('StructureSetROISequence', [])
    numbers = [int(r.ROINumber) for r in rois]
    if not numbers or len(set(numbers)) != len(numbers):
        raise InputError('Strukturexport benötigt eindeutige ROI-Nummern.')
    frames = {_uid(r.get('ReferencedFrameOfReferenceUID')) for r in rois}

    # Copy elements, including each sequence child, without FileDataset's
    # pydicom 2.x deepcopy recursion or shared mutable sequence items.
    def clone(source_ds):
        result = Dataset()
        for element in source_ds:
            element_copy = copy.copy(element)
            if element.VR == 'SQ':
                element_copy.value = [clone(child) for child in element.value]
            result.add(element_copy)
        return result

    sop = generate_uid()
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = RTStructureSetStorage
    meta.MediaStorageSOPInstanceUID = sop
    ds = FileDataset(None, clone(source), file_meta=meta, preamble=b'\0' * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.SOPClassUID = RTStructureSetStorage
    ds.SOPInstanceUID = sop
    ds.StudyInstanceUID = study
    ds.SeriesInstanceUID = generate_uid()
    ds.SpecificCharacterSet = 'ISO_IR 192'
    ds.Modality = 'RTSTRUCT'
    ds.StructureSetLabel = 'RESEARCH'
    ds.StructureSetName = 'Research comparison structures'
    ds.SeriesDescription = 'RESEARCH STRUCTURES - NOT FOR TREATMENT'
    ds.SeriesNumber = 9002
    ds.InstanceNumber = 1
    ds.Manufacturer = 'Dose Atlas Research'
    for keyword in ('PatientName', 'PatientID', 'PatientBirthDate', 'PatientSex',
                    'StudyDate', 'StudyTime', 'ReferringPhysicianName', 'StudyID',
                    'AccessionNumber', 'OperatorsName', 'StructureSetDate', 'StructureSetTime'):
        if keyword not in ds:
            setattr(ds, keyword, '')

    known_frames = {}
    for item in ds.get('ReferencedFrameOfReferenceSequence', []):
        uid = _uid(item.get('FrameOfReferenceUID'))
        if uid in known_frames:
            raise InputError('Doppelte Referenzrahmen im Strukturexport.')
        known_frames[uid] = item
    for uid in sorted(frames - known_frames.keys()):
        item = Dataset()
        item.FrameOfReferenceUID = uid
        known_frames[uid] = item
    ds.ReferencedFrameOfReferenceSequence = list(known_frames.values())
    for roi in ds.StructureSetROISequence:
        if 'ROIName' not in roi:
            roi.ROIName = ''
        if roi.get('ROIGenerationAlgorithm') not in ('AUTOMATIC', 'SEMIAUTOMATIC', 'MANUAL', 'UNKNOWN'):
            roi.ROIGenerationAlgorithm = 'UNKNOWN'

    observations = list(ds.get('RTROIObservationsSequence', []))
    seen_numbers, observed_rois = set(), set()
    for obs in observations:
        number = int(obs.ObservationNumber)
        roi_number = int(obs.ReferencedROINumber)
        if number in seen_numbers or roi_number not in numbers:
            raise InputError('Ungültige ROI-Beobachtungsreferenzen im Strukturexport.')
        seen_numbers.add(number)
        observed_rois.add(roi_number)
        for keyword in ('RTROIInterpretedType', 'ROIInterpreter'):
            if keyword not in obs:
                setattr(obs, keyword, '')
    next_number = max(seen_numbers, default=0) + 1
    for number in numbers:
        if number in observed_rois:
            continue
        obs = Dataset()
        obs.ObservationNumber = next_number
        next_number += 1
        obs.ReferencedROINumber = number
        obs.RTROIInterpretedType = ''
        obs.ROIInterpreter = ''
        observations.append(obs)
    ds.RTROIObservationsSequence = observations
    stream = BytesIO()
    pydicom.dcmwrite(stream, ds, write_like_original=False)
    return stream.getvalue()
