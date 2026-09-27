"""Upload preparation and descriptive prescription hypothesis, never a prescription."""
import copy
import math
import re
import numpy as np
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import generate_uid
from geometry import InputError, read_dicom, structure_rois, make_grid, rasterize, dose_geometry,safe_dicom_diagnostics

# Minimal analysis dataset: discard everything not needed for geometry/dose.
KEEP=set('''SOPClassUID SOPInstanceUID StudyInstanceUID SeriesInstanceUID Modality
FrameOfReferenceUID ReferencedFrameOfReferenceUID ReferencedSOPClassUID ReferencedSOPInstanceUID
ReferencedFrameOfReferenceSequence RTReferencedStudySequence RTReferencedSeriesSequence
ContourImageSequence ReferencedRTPlanSequence ReferencedStructureSetSequence
StructureSetROISequence ROINumber ROIName ROIContourSequence ReferencedROINumber
SourcePixelPlanesCharacteristicsSequence SpacingBetweenSlices SliceThickness
ContourSequence ContourGeometricType NumberOfContourPoints ContourData
ImagePositionPatient ImageOrientationPatient GridFrameOffsetVector PixelSpacing
DoseUnits DoseType DoseSummationType DoseGridScaling Rows Columns NumberOfFrames
SamplesPerPixel PhotometricInterpretation BitsAllocated BitsStored HighBit PixelRepresentation
PixelData ExtendedOffsetTable ExtendedOffsetTableLengths'''.split())
CLASS_UIDS={'SOPClassUID','ReferencedSOPClassUID'}


def safe_roi_name(name,number):
    lower=str(name).lower().strip()
    if re.match(r'^ptv',lower):kind='PTV'
    elif re.match(r'^gtv',lower):kind='GTV'
    elif 'brainstem' in lower or 'hirnstamm' in lower:kind='Brainstem'
    elif 'chiasm' in lower:kind='Chiasm'
    elif 'optic' in lower or 'sehnerv' in lower:kind='Optic'
    elif re.fullmatch(r'(brain|hirn|gehirn)(?:\s+\d+)?',lower):kind='Brain'
    else:kind='ROI'
    return f'{kind} {number}'


@safe_dicom_diagnostics
def anonymize_dataset(source,uid_map):
    """Allowlist reconstruction; patient geometry intentionally retained.

    Not a certified DICOM confidentiality profile or proof of anonymous anatomy.
    One fresh UID map must be shared across the structure/dose pair.
    """
    def clean(ds):
        result=Dataset()
        for element in ds:
            if element.keyword not in KEEP:continue
            elem=copy.copy(element)
            if elem.VR=='SQ':elem.value=[clean(child) for child in element.value]
            elif elem.VR=='UI' and elem.keyword not in CLASS_UIDS:
                def remap(value):
                    key=str(value)
                    if not key:return ''
                    if key not in uid_map:uid_map[key]=generate_uid()
                    return uid_map[key]
                elem.value=[remap(v) for v in element.value] if element.VM>1 else remap(element.value)
            result.add(elem)
        if 'ROIName' in result:result.ROIName=safe_roi_name(ds.ROIName,int(ds.ROINumber))
        return result
    result=clean(source)
    result.PatientName='ANON';result.PatientID='ANON';result.PatientIdentityRemoved='YES'
    result.DeidentificationMethod='Dose Atlas minimal analysis allowlist; anatomy retained'
    result.file_meta=FileMetaDataset()
    if hasattr(source,'file_meta') and 'TransferSyntaxUID' in source.file_meta:
        result.file_meta.TransferSyntaxUID=source.file_meta.TransferSyntaxUID
    if 'SOPClassUID' in result:result.file_meta.MediaStorageSOPClassUID=result.SOPClassUID
    if 'SOPInstanceUID' in result:result.file_meta.MediaStorageSOPInstanceUID=result.SOPInstanceUID
    return result


@safe_dicom_diagnostics
def prepare_upload(structure_data,dose_data=None,anonymize=True):
    structure=read_dicom(structure_data,'RTSTRUCT')
    dose=read_dicom(dose_data,'RTDOSE') if dose_data else None
    if anonymize:
        mapping={}
        structure=anonymize_dataset(structure,mapping)
        if dose is not None:dose=anonymize_dataset(dose,mapping)
    info={'applied':bool(anonymize),'location':'server_memory',
          'message':('DICOM-Kennungen und Freitext im Server-Arbeitsspeicher entfernt; Geometrie bleibt erhalten.' if anonymize
                     else 'Keine zusätzliche Anonymisierung; Verarbeitung unverändert im Server-Arbeitsspeicher.'),
          'before_transmission':False}
    return structure,dose,info


def preferred_targets(rois):
    ptvs=[n for n,r in rois.items() if re.match(r'^ptv',r['name'].strip(),re.I)]
    return ptvs or [n for n,r in rois.items() if re.match(r'^gtv',r['name'].strip(),re.I)]


def prescription_hypotheses(rois,dose):
    ptvs=[(n,rois[n]) for n in preferred_targets(rois)]
    if len(ptvs)>100:raise InputError('Mehr als 100 Ziele: Uploadprüfung überschreitet den Prototypumfang.')
    hypotheses=[];samplers={}
    for number,roi in ptvs:
        row=dict(number=number,name=roi['name'],eligible=False,Dmean_Gy=None,D98_Gy=None,suggested_Gy=None)
        try:
            frame=roi['frame']
            if frame not in samplers:samplers[frame]=dose_geometry(dose,frame,high_precision=True)
            interp,origin,basis=samplers[frame]
            grid=make_grid(rois,[number],padding=3,max_voxels=2_000_000)
            mask=rasterize(roi,grid)
            idx=np.flatnonzero(mask)
            if len(idx)<4:raise InputError('Ziel zu klein für eine belastbare 1-mm-Abtastung.')
            z,y,x=np.unravel_index(idx,grid.shape)
            xyz=np.column_stack((x,y,z))*grid.spacing+grid.origin
            values=interp((xyz-origin)@basis)
            if not np.isfinite(values).all():raise InputError('Ziel nicht vollständig durch RTDOSE abgedeckt.')
            mean=float(np.mean(values));d98=float(np.percentile(values,2))
            # Ceil is discontinuous: avoid inventing 1 Gy from numerical noise.
            # Float64 scaling plus a 1e-8 Gy roundoff tolerance, not clinical rounding.
            if abs(d98-round(d98))<1e-8:d98=float(round(d98))
            if abs(mean-10)<1e-8:mean=10.
            row.update(Dmean_Gy=mean,D98_Gy=d98,eligible=mean>10,
                       suggested_Gy=math.ceil(d98) if mean>10 else None,
                       reason='Dmean > 10 Gy' if mean>10 else 'Dmean ≤ 10 Gy: kein Vorschlag')
        except InputError as exc:row['reason']=str(exc)
        hypotheses.append(row)
    return hypotheses


@safe_dicom_diagnostics
def inspect_upload(structure_data,dose_data=None,anonymize=True):
    structure,dose,privacy=prepare_upload(structure_data,dose_data,anonymize)
    rois=structure_rois(structure)
    suggestions=prescription_hypotheses(rois,dose) if dose is not None else []
    hdss_warnings=['HDSS-Quellebenen berücksichtigt; Auswertung auf 1 mm, keine native HDSS-DVH-Validierung.'] if any(r.get('source_planes') is not None for r in rois.values()) else []
    return dict(rois=[dict(number=n,name=r['name'],contours=len(r['contours'])) for n,r in rois.items()],
                suggested_targets=preferred_targets(rois),
                target_kind='PTV' if any(r['name'].lower().startswith('ptv') for r in rois.values()) else 'GTV',
                prescriptions=suggestions,anonymization=privacy,
                warnings=hdss_warnings+([privacy['message'],'Vermutung aus der Dosis: bevorzugte Ziele (PTV, sonst GTV) mit Dmean > 10 Gy; Vorschlag = ceil(D98/Gy). Keine bestätigte Verschreibung.',
                          'Sammel- und Einzel-PTVs können beide vorkommen; Zielauswahl prüfen.'] if dose is not None else
                          [privacy['message'],'Ohne Dosis ist keine D98-basierte Verschreibungsvermutung möglich.']))
