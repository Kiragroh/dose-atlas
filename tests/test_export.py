from io import BytesIO
import numpy as np
import pydicom
import pytest
from pydicom.tag import Tag
from pydicom.uid import RTDoseStorage, ExplicitVRLittleEndian
from geometry import Grid, InputError
from dose_export import export_prediction, export_structure, RESEARCH_LABEL
from test_api import structure_bytes


def inputs(spacing=1.):
    _, source = structure_bytes()
    source.StudyInstanceUID = '1.2.3.44'
    source.SeriesInstanceUID = '1.2.3.45'
    source.PatientName = 'SYNTHETIC^Müller'
    source.PatientID = 'SYNTHETIC-EXPORT'
    source.InstitutionName = 'NOT_TO_COPY'
    grid = Grid(np.array([-12.5, 7.25, -31.]), (4, 5, 7), spacing)
    z, y, x = np.indices(grid.shape)
    pred = (0.127891 * x + 0.793711 * y + 1.757919 * z).astype(np.float32)
    return pred, grid, source


@pytest.mark.parametrize('spacing', [1., .5, 2.5])
def test_roundtrip_dose_and_physical_coordinates(spacing):
    pred, grid, source = inputs(spacing)
    result = pydicom.dcmread(BytesIO(export_prediction(pred, grid, source, [1], 20)))
    assert result.SOPClassUID == RTDoseStorage
    assert result.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian
    assert result.pixel_array.dtype == np.dtype('uint32')
    assert result.pixel_array.shape == grid.shape
    decoded = result.pixel_array.astype(float) * float(result.DoseGridScaling)
    np.testing.assert_allclose(decoded, pred, atol=float(result.DoseGridScaling) / 2 + 1e-10, rtol=0)
    assert result.FrameIncrementPointer == Tag(0x3004, 0x000C)
    row, col = np.array(result.ImageOrientationPatient, float).reshape(2, 3)
    normal = np.cross(row, col)
    origin = np.array(result.ImagePositionPatient, float)
    for z, y, x in [(0, 0, 0), (3, 4, 6), (2, 1, 4)]:
        physical = (origin + x * float(result.PixelSpacing[1]) * row
                    + y * float(result.PixelSpacing[0]) * col
                    + float(result.GridFrameOffsetVector[z]) * normal)
        np.testing.assert_allclose(physical, grid.origin + np.array([x, y, z]) * spacing)


def test_original_linkage_new_instances_and_research_label():
    pred, grid, source = inputs()
    before = source.to_json()
    first = pydicom.dcmread(BytesIO(export_prediction(pred, grid, source, [1], 20)))
    second = pydicom.dcmread(BytesIO(export_prediction(pred, grid, source, [1], 20)))
    assert source.to_json() == before
    assert first.StudyInstanceUID == source.StudyInstanceUID
    assert first.FrameOfReferenceUID == source.StructureSetROISequence[0].ReferencedFrameOfReferenceUID
    assert first.PatientID == source.PatientID and first.PatientName == source.PatientName
    assert first.SOPInstanceUID != second.SOPInstanceUID != source.SOPInstanceUID
    assert first.SeriesInstanceUID != second.SeriesInstanceUID != source.SeriesInstanceUID
    assert first.file_meta.MediaStorageSOPInstanceUID == first.SOPInstanceUID
    assert first.DoseSummationType == 'OTHER'
    assert first.DoseUnits == 'GY' and first.DoseType == 'PHYSICAL'
    assert 'ReferencedRTPlanSequence' not in first
    assert first.SeriesDescription == RESEARCH_LABEL
    assert len(first.DoseComment) <= 64 and '>35mm unvalidated' in first.DoseComment
    assert 'beyond 35 mm' in first.DerivationDescription
    assert 'unvalidated extrapolation' in first.DerivationDescription
    assert 'InstitutionName' not in first and 'ROIName' not in first
    for keyword in ('PatientBirthDate','PatientSex','StudyDate','StudyTime',
                    'ReferringPhysicianName','StudyID','AccessionNumber',
                    'OperatorsName','PositionReferenceIndicator','SliceThickness'):
        assert keyword in first


def test_optional_identity_omission_does_not_break_original_geometry_linkage():
    pred, grid, source = inputs()
    result = pydicom.dcmread(BytesIO(export_prediction(pred, grid, source, [1], 20,
                                                     preserve_patient_identity=False)))
    assert result.PatientName == '' and result.PatientID == ''
    assert result.StudyInstanceUID == source.StudyInstanceUID
    assert result.FrameOfReferenceUID == source.StructureSetROISequence[0].ReferencedFrameOfReferenceUID


@pytest.mark.parametrize('bad', [np.nan, np.inf, -1.])
def test_nonfinite_or_negative_dose_is_not_silently_zeroed(bad):
    pred, grid, source = inputs()
    pred[0, 0, 0] = bad
    with pytest.raises(InputError): export_prediction(pred, grid, source, [1], 20)


def test_invalid_shape_geometry_uid_and_target_selection_rejected():
    pred, grid, source = inputs()
    with pytest.raises(InputError): export_prediction(pred[::2], grid, source, [1], 20)
    with pytest.raises(InputError): export_prediction(pred, grid, source, [99], 20)
    with pytest.raises(InputError): export_prediction(pred, grid, source, [1, 1], 20)
    with pytest.raises(InputError): export_prediction(pred, grid, source, [1], np.nan)
    bad_grid = Grid(np.array([np.nan, 0, 0]), grid.shape, 1)
    with pytest.raises(InputError): export_prediction(pred, bad_grid, source, [1], 20)
    del source.StudyInstanceUID
    with pytest.raises(InputError): export_prediction(pred, grid, source, [1], 20)


def test_anonymized_structure_completion_and_dose_pair_linkage():
    from upload import anonymize_dataset
    from pydicom.uid import RTStructureSetStorage
    pred, grid, source = inputs()
    cleaned = anonymize_dataset(source, {})
    before = cleaned.to_json()
    result = pydicom.dcmread(BytesIO(export_structure(cleaned)))
    dose = pydicom.dcmread(BytesIO(export_prediction(pred, grid, cleaned, [1], 20)))
    assert cleaned.to_json() == before
    assert result.SOPClassUID == RTStructureSetStorage
    assert result.file_meta.MediaStorageSOPInstanceUID == result.SOPInstanceUID
    assert result.StudyInstanceUID == dose.StudyInstanceUID == cleaned.StudyInstanceUID
    assert result.PatientIdentityRemoved == dose.PatientIdentityRemoved == 'YES'
    assert result.PatientName == dose.PatientName == 'ANON'
    assert 'NOT_TO_COPY' not in result.to_json() and 'SYNTHETIC' not in result.to_json()
    assert result.StructureSetLabel == 'RESEARCH'
    for keyword in ('PatientBirthDate','PatientSex','StudyDate','StudyTime',
                    'ReferringPhysicianName','StudyID','AccessionNumber',
                    'OperatorsName','StructureSetDate','StructureSetTime'):
        assert keyword in result
    roi = result.StructureSetROISequence[0]
    assert roi.ROINumber == 1 and roi.ROIGenerationAlgorithm == 'UNKNOWN'
    assert roi.ReferencedFrameOfReferenceUID == dose.FrameOfReferenceUID
    assert result.ReferencedFrameOfReferenceSequence[0].FrameOfReferenceUID == dose.FrameOfReferenceUID
    assert 'RTReferencedStudySequence' not in result.ReferencedFrameOfReferenceSequence[0]
    obs = result.RTROIObservationsSequence[0]
    assert obs.ObservationNumber == 1 and obs.ReferencedROINumber == roi.ROINumber
    assert obs.RTROIInterpretedType == '' and obs.ROIInterpreter == ''
    for original, exported in zip(cleaned.ROIContourSequence[0].ContourSequence,
                                  result.ROIContourSequence[0].ContourSequence):
        np.testing.assert_array_equal(original.ContourData, exported.ContourData)
        assert original.NumberOfContourPoints == exported.NumberOfContourPoints
        assert 'ContourImageSequence' not in exported


def test_structure_preserves_valid_generation_and_multiple_frames_without_inventing_ct():
    from pydicom.dataset import Dataset
    _, _, source = inputs()
    source.StructureSetROISequence[0].ROIGenerationAlgorithm = 'MANUAL'
    other = Dataset()
    other.ROINumber = 7
    other.ROIName = 'Synthetic other frame'
    other.ReferencedFrameOfReferenceUID = '1.2.3.77'
    source.StructureSetROISequence.append(other)
    result = pydicom.dcmread(BytesIO(export_structure(source)))
    assert [r.ROINumber for r in result.StructureSetROISequence] == [1, 7]
    assert result.StructureSetROISequence[0].ROIGenerationAlgorithm == 'MANUAL'
    assert result.StructureSetROISequence[1].ROIGenerationAlgorithm == 'UNKNOWN'
    assert {r.FrameOfReferenceUID for r in result.ReferencedFrameOfReferenceSequence} == {'1.2.3','1.2.3.77'}
    assert [r.ReferencedROINumber for r in result.RTROIObservationsSequence] == [1, 7]
    assert all('RTReferencedStudySequence' not in r for r in result.ReferencedFrameOfReferenceSequence)
