"""Excel regressions use synthetic data and inspect actual saved XLSX files."""
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import zipfile

from openpyxl import Workbook, load_workbook
import pytest

from backend import workbook_io


def make_plan(rows=None, headers=None):
    book = Workbook()
    sheet = book.active
    sheet.title = "Kế hoạch"
    sheet.append(headers if headers is not None else workbook_io.PLAN_COLUMNS)
    for row in rows or []:
        sheet.append(row)
    buffer = BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def month_row(**changes):
    values = {"terminal": "cua_lo", "period_type": "month", "month": "2026-09", "voyage_id": None,
              "metric": "tonnage", "amount": 120.125, "reference": "PLAN-EXAMPLE", "note": None, **changes}
    return [values.get(key) for key in workbook_io.PLAN_KEYS]


def replace_zip_member(content, name, replacement):
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(content)) as original, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as changed:
        for member in original.infolist():
            changed.writestr(member.filename, replacement(original.read(member.filename)) if member.filename == name else original.read(member.filename))
    return output.getvalue()


def test_template_roundtrips_headers_validation_and_empty_state(tmp_path):
    target = tmp_path / "plan-template.xlsx"
    target.write_bytes(workbook_io.plan_template())
    book = load_workbook(target)
    assert book.sheetnames == ["Kế hoạch", "Hướng dẫn"]
    assert [cell.value for cell in book["Kế hoạch"][1]] == workbook_io.PLAN_COLUMNS
    assert len(book["Kế hoạch"].data_validations.dataValidation) == 3
    assert book["Kế hoạch"]["D2"].number_format == "@"
    book.close()
    preview = workbook_io.parse_plan_workbook(target.read_bytes())
    assert preview["valid"] is False and preview["rows"] == []
    assert preview["errors"][0]["row"] == 2


@pytest.mark.parametrize("headers", [workbook_io.PLAN_COLUMNS, workbook_io.PLAN_KEYS])
def test_import_accepts_typed_numbers_text_identifiers_and_null_optional_fields(headers):
    voyage = month_row(period_type="voyage", month=None, voyage_id="00000101", metric="teu", amount=0, reference="000012")
    preview = workbook_io.parse_plan_workbook(make_plan([month_row(), voyage], headers))
    assert preview["valid"] and not preview["errors"]
    monthly, ship = preview["rows"]
    assert monthly["amount"] == "120.125" and monthly["voyage_id"] is None
    assert monthly["note"] == ""
    assert ship["voyage_id"] == 101 and ship["month"] is None
    assert ship["amount"] == "0" and ship["reference"] == "000012"


@pytest.mark.parametrize("field,value", [("amount", "=SUM(1,2)"), ("reference", '=HYPERLINK("https://example.invalid","test")'), ("note", "=1+1")])
def test_import_rejects_formula_cells_without_evaluating_them(field, value):
    preview = workbook_io.parse_plan_workbook(make_plan([month_row(**{field: value})]))
    assert not preview["valid"] and preview["rows"] == []
    assert preview["errors"][0]["row"] == 2
    assert "công thức" in preview["errors"][0]["message"]


@pytest.mark.parametrize("value", ["120.125", "1,200", "12 tấn", True, None, -1, 0.0001, 1000000000001])
def test_import_requires_bounded_nonnegative_native_number_cells(value):
    preview = workbook_io.parse_plan_workbook(make_plan([month_row(amount=value)]))
    assert not preview["valid"] and preview["rows"] == []
    assert preview["errors"][0]["row"] == 2


@pytest.mark.parametrize("headers", [list(reversed(workbook_io.PLAN_COLUMNS)), ["Wrong", *workbook_io.PLAN_COLUMNS[1:]], workbook_io.PLAN_COLUMNS[:-1]])
def test_wrong_headers_are_rejected(headers):
    with pytest.raises(ValueError, match="cột"):
        workbook_io.parse_plan_workbook(make_plan([month_row()], headers))


def test_duplicate_plan_key_is_reported_without_silently_summing_or_replacing():
    preview = workbook_io.parse_plan_workbook(make_plan([month_row(amount=10), month_row(amount=20)]))
    assert not preview["valid"]
    assert len(preview["rows"]) == 1 and preview["rows"][0]["amount"] == "10"
    assert preview["errors"][0]["row"] == 3
    assert "Trùng" in preview["errors"][0]["message"]
    distinct = workbook_io.parse_plan_workbook(make_plan([month_row(), month_row(terminal="ben_thuy"), month_row(metric="teu")]))
    assert distinct["valid"] and len(distinct["rows"]) == 3


def test_file_size_and_invalid_zip_rejected_before_xml_loader(monkeypatch):
    monkeypatch.setattr(workbook_io, "load_workbook", lambda *args, **kwargs: pytest.fail("Rejected upload reached XML loader"))
    with pytest.raises(ValueError, match="2 MB"):
        workbook_io.parse_plan_workbook(b"0" * (workbook_io.MAX_UPLOAD_BYTES + 1))
    with pytest.raises(ValueError, match="xlsx"):
        workbook_io.parse_plan_workbook(b"not a workbook")


@pytest.mark.parametrize("case", ["inflated_size", "many_members", "macro", "external_link"])
def test_zip_limits_and_active_content_rejected_before_xml_loader(monkeypatch, case):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if case == "inflated_size":
            archive.writestr("xl/large.xml", b"0" * (20 * 1024 * 1024 + 1))
        elif case == "many_members":
            for index in range(1001):
                archive.writestr(f"small-{index}.xml", b"x")
        elif case == "macro":
            archive.writestr("xl/vbaProject.bin", b"not executed")
        else:
            archive.writestr("xl/externalLinks/externalLink1.xml", b"not followed")
    monkeypatch.setattr(workbook_io, "load_workbook", lambda *args, **kwargs: pytest.fail("Rejected ZIP reached XML loader"))
    with pytest.raises(ValueError):
        workbook_io.parse_plan_workbook(buffer.getvalue())


def test_row_count_and_forged_dimensions_are_bounded():
    with pytest.raises(ValueError, match="500"):
        workbook_io.parse_plan_workbook(make_plan([month_row()] * 501))
    original = make_plan([month_row()])
    forged = replace_zip_member(original, "xl/worksheets/sheet1.xml", lambda xml: xml.replace(b'A1:L2', b'A1:L1048576'))
    with pytest.raises(ValueError, match="500"):
        workbook_io.parse_plan_workbook(forged)


def operation(index):
    return {"terminal_name": "Cảng mẫu", "id": f"{index:08d}", "operation_code": f"{index:010d}",
            "operation_date": "2026-09-01", "shift_code": "01", "vessel_name": "Tàu mẫu",
            "voyage_code": "0000000101", "cargo_name": "Hàng mẫu", "customer_name": "0012",
            "direction": "UNLOADING", "quantity": Decimal("1.25"), "quantity_unit_name": "M3",
            "weight": Decimal("2.125"), "weight_unit_name": "M3", "tonnage": None, "teu": 0}


def report(rows):
    return {"meta": {"report_id": "report-example", "filters": {"start_date": "2026-09-01", "end_date": "2026-09-09", "terminal": "all"},
                     "selection": {"customer_id": "000012", "voyage_id": "000101"}, "operations_filter": "all"},
            "overview": {"total_tonnage": None, "total_teu": 0, "record_count": len(rows), "tonnage_status": "unavailable", "teu_status": "complete"}}


def test_export_includes_full_row_count_and_preserves_types_native_units_and_nulls(tmp_path):
    rows = [operation(index) for index in range(1, 64)]
    rows[1]["weight"] = None
    rows[2]["weight"] = 0
    rows[3]["quantity"] = float("nan")
    target = tmp_path / "report-all-rows.xlsx"
    target.write_bytes(workbook_io.report_workbook(report(rows), rows))
    book = load_workbook(target, data_only=False)
    sheet = book["Tác nghiệp"]
    assert sheet.max_row == 64  # More than the dialog's 25-row page.
    assert sheet["B64"].value == "00000063"
    for coordinate, expected in {"B2": "00000001", "C2": "0000000001", "G2": "0000000101", "I2": "0012"}.items():
        assert sheet[coordinate].value == expected and sheet[coordinate].data_type == "s"
    assert isinstance(sheet["D2"].value, datetime) and sheet["D2"].value.date() == date(2026, 9, 1)
    assert sheet["K2"].value == 1.25 and sheet["K2"].data_type == "n"
    assert sheet["M2"].value == 2.125 and sheet["M2"].data_type == "n"
    assert sheet["N2"].value == "M3" and sheet["O2"].value is None
    assert sheet["M3"].value is None and sheet["M4"].value == 0
    assert sheet["K5"].value is None and sheet["P2"].value == 0
    summary = {row[0].value: row[1] for row in book["Tổng hợp"].iter_rows(min_row=2)}
    assert summary["Số dòng trong tệp"].value == 63
    assert summary["Số dòng nguồn"].value == 63
    assert summary["Sản lượng thông qua"].value is None
    assert summary["Container tác nghiệp"].value == 0
    assert summary["ID khách hàng"].value == "000012" and summary["ID khách hàng"].data_type == "s"
    book.close()


@pytest.mark.parametrize("text", ["=1+1", "+SUM(A1:A2)", "-1+2", "@SUM(A1:A2)", '=HYPERLINK("https://example.invalid","test")'])
def test_export_formula_like_names_and_identifiers_remain_literal_text(text):
    rows = [{**operation(1), "cargo_name": text, "customer_name": text, "operation_code": text}]
    content = workbook_io.report_workbook(report(rows), rows, title=text)
    book = load_workbook(BytesIO(content), data_only=False)
    for cell in (book["Tổng hợp"]["B2"], book["Tác nghiệp"]["C2"], book["Tác nghiệp"]["H2"], book["Tác nghiệp"]["I2"]):
        assert cell.value == text and cell.data_type == "s"
    book.close()
    with zipfile.ZipFile(BytesIO(content)) as archive:
        for name in archive.namelist():
            if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                assert b"<f>" not in archive.read(name)


def test_shift_export_contains_all_rows_without_inventing_missing_metrics():
    rows = [operation(1)]
    shifts = [{"date": "2026-09-01", "shift_code": "001", "terminal_name": "Cảng mẫu", "tonnage": None, "teu": 0, "record_count": 1}]
    book = load_workbook(BytesIO(workbook_io.report_workbook(report(rows), rows, shifts=shifts)))
    sheet = book["Theo ca"]
    assert sheet.max_row == 2
    assert sheet["B2"].value == "001" and sheet["B2"].data_type == "s"
    assert sheet["D2"].value is None and sheet["E2"].value == 0 and sheet["F2"].value == 1
    book.close()


def test_numeric_metadata_identifiers_are_exported_as_text():
    snapshot = report([])
    snapshot["meta"]["selection"] = {"customer_id": 123, "voyage_id": 101}
    book = load_workbook(BytesIO(workbook_io.report_workbook(snapshot, [])))
    summary = {row[0].value: row[1] for row in book["Tổng hợp"].iter_rows(min_row=2)}
    assert summary["ID khách hàng"].value == "123" and summary["ID khách hàng"].data_type == "s"
    assert summary["ID chuyến"].value == "101" and summary["ID chuyến"].data_type == "s"
    book.close()


def test_export_preserves_scope_rule_and_first_berth_evidence_as_literal_values():
    from datetime import timezone
    rows = [{**operation(1), 'production_scope': 'vietsun', 'berth_assignment_status': 'assigned',
             'initial_berth_id': 13, 'initial_berth_code': '=Cau5',
             'initial_berth_at': datetime(2026, 8, 31, 9, 15, tzinfo=timezone.utc)}]
    snapshot = report(rows)
    snapshot['meta']['filters']['production_scope'] = 'vietsun'
    snapshot['meta']['berth_rule_version'] = 'initial-berth-v1'
    book = load_workbook(BytesIO(workbook_io.report_workbook(snapshot, rows)), data_only=False)
    summary = {row[0].value: row[1].value for row in book['Tổng hợp'].iter_rows(min_row=2)}
    assert summary['Phạm vi sản lượng'] == 'Cầu 5'
    assert summary['Mã phạm vi sản lượng'] == 'vietsun'
    assert summary['Phiên bản quy tắc cầu cập đầu tiên'] == 'initial-berth-v1'
    sheet = book['Tác nghiệp']
    assert sheet['Q2'].value == 'Cầu 5' and sheet['R2'].value == 'Đã xác định'
    assert sheet['S2'].value == '13' and sheet['S2'].data_type == 's'
    assert sheet['T2'].value == '=Cau5' and sheet['T2'].data_type == 's'
    assert sheet['U2'].value == '2026-08-31T09:15:00+00:00'
    book.close()


@pytest.mark.parametrize('status', ['missing', 'ambiguous'])
def test_unclassified_export_keeps_unknown_berth_empty(status):
    rows = [{**operation(1), 'production_scope': 'unclassified', 'berth_assignment_status': status,
             'initial_berth_id': None, 'initial_berth_code': None, 'initial_berth_at': None}]
    snapshot = report(rows)
    snapshot['meta']['filters']['production_scope'] = 'unclassified'
    snapshot['meta']['berth_rule_version'] = 'initial-berth-v1'
    book = load_workbook(BytesIO(workbook_io.report_workbook(snapshot, rows)))
    sheet = book['Tác nghiệp']
    assert sheet['Q2'].value == 'Chưa xác định cầu'
    assert sheet['R2'].value == {'missing': 'Thiếu dữ liệu cầu', 'ambiguous': 'Không xác định duy nhất'}[status]
    assert all(sheet.cell(2, col).value is None for col in (19, 20, 21))
    book.close()


def test_legacy_export_is_labelled_without_reclassifying_or_mutating_saved_report():
    from copy import deepcopy
    rows = [operation(1)]
    snapshot = report(rows)
    unchanged = deepcopy(snapshot)
    book = load_workbook(BytesIO(workbook_io.report_workbook(snapshot, rows)))
    summary = {row[0].value: row[1].value for row in book['Tổng hợp'].iter_rows(min_row=2)}
    assert summary['Phạm vi sản lượng'].startswith('Phạm vi cũ')
    assert summary['Mã phạm vi sản lượng'] == 'legacy'
    assert summary['Phiên bản quy tắc cầu cập đầu tiên'] == 'Chưa được lưu trong bản dữ liệu này'
    assert book['Tác nghiệp']['R2'].value == 'Chưa lưu bằng chứng phân loại'
    assert snapshot == unchanged
    book.close()
