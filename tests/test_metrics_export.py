"""Validate the browser-generated OOXML with an independent Excel reader."""
import sys
import warnings
import zipfile
import openpyxl


def validate(path):
    with warnings.catch_warnings(record=True) as found:
        warnings.simplefilter('always')
        book = openpyxl.load_workbook(path)
        assert not found, [str(item.message) for item in found]
    assert book.sheetnames == ['ROI-Metriken', 'Zusammenfassung', 'Methodik']
    sheet = book['ROI-Metriken']
    headers = [cell.value for cell in sheet[1]]
    rows = [dict(zip(headers, row)) for row in sheet.iter_rows(min_row=2, values_only=True)]
    assert len(rows) == 8
    assert rows[0]['D98_Gy'] == 19 and rows[4]['D98_Gy'] == 20
    assert rows[0]['local_inverse_CI'] == 0 and rows[0]['local_GI'] is None
    assert rows[0]['ROI_number'] == 7 and rows[0]['local_coverage_pct'] == 99
    assert rows[0]['ROI_name'] == '=ÄÖ & <Target>'
    assert sheet.cell(2, headers.index('ROI_name')+1).data_type == 's'
    assert sheet.cell(2, headers.index('local_inverse_CI')+1).data_type == 'n'
    assert rows[1]['D98_Gy'] == 0 and rows[3]['D98_Gy'] is None
    assert book['Zusammenfassung'].max_row == 5
    metadata = '\n'.join(str(cell.value) for row in book['Methodik'] for cell in row)
    for text in ['35-mm', 'keine Hirnmaske', 'Kehrwert', 'beidseitig', 'Referenzabdeckung', 'synthetische', 'Sigma 1 mm', 'fractions']:
        assert text in metadata
    for tab in book:
        assert tab.freeze_panes == 'A2' and tab.auto_filter.ref
        assert not any(cell.data_type == 'f' for row in tab for cell in row)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        payload = b''.join(archive.read(name) for name in archive.namelist())
        for secret in [b'PRIVATE-ID', b'PRIVATE-FILE', b'PatientID', b'123456789']:
            assert secret not in payload
    print('XLSX independent readback: 2 comparisons, 8 metric rows; values, blanks, zeros, text safety, privacy, metadata, CRC and styles passed')


if __name__ == '__main__':
    validate(sys.argv[1])
