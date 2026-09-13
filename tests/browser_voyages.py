"""Browser checks for voyage drill-down; all intercepted data is synthetic.

Run with the same local Vite URL used by browser_smoke.py. No SQL is read.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import json
from urllib.parse import parse_qs, urlparse

from browser_smoke import fixture
from playwright.sync_api import Error, expect, sync_playwright


def report_fixture(filters):
    data = fixture(filters)
    # 26 nonzero rows plus two source rows without a nonzero value.
    for voyage in data["voyages"]:
        voyage["record_count"] = 28
    data["overview"]["record_count"] = 28 * len(data["voyages"])
    return data


def detail_fixture(filters, terminal, page, page_size, operation_filter="with_values"):
    data = report_fixture({**filters, "terminal": terminal})
    header = deepcopy(data["voyages"][0])
    total = header["tonnage"]
    rows = []
    for index in range(26):
        weight = 1 if index < 25 else round(total - 25, 3)
        rows.append({
            "id": str(index + 1), "operation_code": f"TEST-PHIEU-{index + 1:03d}",
            "operation_date": filters["start_date"], "job_method": "Phương án kiểm thử",
            "shift_id": "1", "shift_code": "07-13",
            "job_method_code": "TEST-QUACANG", "cargo_name": "Hàng kiểm thử",
            "direction": "Hàng xếp", "quantity": 1, "quantity_unit": "TAN",
            "quantity_unit_name": "Tấn", "weight": weight, "weight_unit": "TAN",
            "weight_unit_name": "Tấn", "tonnage": weight,
            "teu": header["teu"] if index == 0 else 0,
            "tonnage_status": "ready", "teu_status": "ready",
        })
    for index in (27, 28):
        missing = index == 28 and terminal == "cua_lo"
        rows.append({**rows[0], "id": str(index), "operation_code": f"TEST-PHIEU-{index:03d}",
                     "quantity": 0, "weight": None if missing else 0,
                     "tonnage": None if missing else 0, "teu": 0})
    counts = {"all": len(rows), "with_values": 26,
              "missing_weight": sum(row["weight"] is None for row in rows)}
    if operation_filter == "with_values":
        rows = [row for row in rows if row["quantity"] != 0 or row["weight"] not in (None, 0)]
    elif operation_filter == "missing_weight":
        rows = [row for row in rows if row["weight"] is None]
    coverage = deepcopy(data["meta"]["metric_coverage"])
    coverage["tonnage"].update(eligible_rows=28, known_rows=28 - counts["missing_weight"])
    return {
        "header": header,
        "summary": {key: header[key] for key in ("tonnage", "teu", "record_count", "tonnage_status", "teu_status")},
        "cargo": data["cargo"], "daily": data["daily_history"], "native_units": [],
        "operations": {"page": page, "page_size": page_size, "filter": operation_filter,
                       "counts": counts, "total_all": counts["all"], "total": len(rows),
                       "total_pages": (len(rows) + page_size - 1) // page_size,
                       "rows": rows[(page - 1) * page_size:page * page_size]},
        "meta": {"filters": {**filters, "terminal": terminal, "voyage_id": "101", "operation_filter": operation_filter, "timezone": "Asia/Ho_Chi_Minh"},
                 "generated_at": data["meta"]["generated_at"], "metric_coverage": coverage,
                 "operations_order": "operation_date, id"},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5175")
    args = parser.parse_args()
    output = Path("outputs")
    state = {"detail": "data"}
    held, requests, errors, checks = [], [], [], []

    def respond_dashboard(route):
        query = parse_qs(urlparse(route.request.url).query)
        filters = {key: query[key][0] for key in ("start_date", "end_date", "terminal")}
        route.fulfill(json=report_fixture(filters))

    def respond_detail(route):
        parsed = urlparse(route.request.url)
        query = parse_qs(parsed.query)
        terminal, voyage_id = parsed.path.rstrip("/").split("/")[-2:]
        filters = {key: query[key][0] for key in ("start_date", "end_date")}
        page = int(query.get("page", [1])[0])
        page_size = int(query.get("page_size", [25])[0])
        operation_filter = query.get("operation_filter", ["all"])[0]
        request = {**filters, "terminal": terminal, "voyage_id": voyage_id, "page": page, "operation_filter": operation_filter}
        requests.append(request)
        if state["detail"] == "error":
            route.fulfill(status=503, json={"detail": {"code": "DATABASE_UNAVAILABLE"}})
        elif state["detail"] == "malformed":
            route.fulfill(json={"header": None})
        elif state["detail"] == "hold":
            held.append((route, filters, terminal, page, page_size, operation_filter))
        else:
            route.fulfill(json=detail_fixture(filters, terminal, page, page_size, operation_filter))

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/dashboard?*", respond_dashboard)
        page.route("**/api/voyages/**", respond_detail)
        page.goto(args.url)
        expect(page.locator(".voyages-panel")).to_be_visible()
        expect(page.locator(".kpi-value").nth(2)).to_contain_text("2")
        expect(page.locator(".voyage-table tbody tr")).to_have_count(2)
        page.get_by_label("Tìm tàu hoặc mã chuyến").fill("CUA LO")
        expect(page.locator(".voyage-table tbody tr")).to_have_count(1)
        open_button = page.get_by_role("button", name="Xem chi tiết CUA LO TEST · TEST-101 · Cửa Lò")
        open_button.click()
        dialog = page.get_by_role("dialog", name="Chi tiết chuyến tàu")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("CUA LO TEST")
        expect(dialog).to_contain_text("1.000,125")
        expect(dialog).to_contain_text("TEST-PHIEU-001")
        expect(dialog.get_by_label("Hiển thị tác nghiệp")).to_have_value("with_values")
        expect(dialog.locator(".voyage-section-heading > span")).to_have_text("26/28 dòng")
        expect(dialog.locator(".voyage-summary")).to_contain_text("28 dòng nguồn")
        expect(dialog).to_contain_text("Ca 07-13")
        assert requests[-1]["terminal"] == "cua_lo" and requests[-1]["voyage_id"] == "101"
        checks.append("list and search: KPI count matches voyages; same ID is namespaced by terminal")

        page.get_by_role("button", name="Trang phiếu sau").click()
        expect(dialog).to_contain_text("TEST-PHIEU-026")
        assert requests[-1]["page"] == 2
        expect(dialog.get_by_text("TEST-PHIEU-001", exact=True)).to_have_count(0)
        dialog.get_by_label("Hiển thị tác nghiệp").select_option("all")
        expect(dialog).to_contain_text("TEST-PHIEU-001")
        assert requests[-1]["page"] == 1 and requests[-1]["operation_filter"] == "all"
        expect(dialog.locator(".voyage-section-heading > span")).to_have_text("28/28 dòng")
        page.get_by_role("button", name="Trang phiếu sau").click()
        expect(dialog).to_contain_text("TEST-PHIEU-027")
        expect(dialog).to_contain_text("TEST-PHIEU-028")
        dialog.get_by_label("Hiển thị tác nghiệp").select_option("missing_weight")
        expect(dialog.locator(".operations-table tbody tr")).to_have_count(1)
        expect(dialog).to_contain_text("TEST-PHIEU-028")
        assert requests[-1]["page"] == 1
        expect(dialog.locator(".voyage-summary")).to_contain_text("1.000,125")
        expect(dialog.locator(".operations-table tbody tr td").nth(3)).to_contain_text("—")
        dialog.get_by_label("Hiển thị tác nghiệp").select_option("with_values")
        expect(dialog).to_contain_text("TEST-PHIEU-001")
        page.get_by_role("button", name="Trang phiếu sau").click()
        expect(dialog).to_contain_text("TEST-PHIEU-026")
        checks.append("operation filters reset page, keep source totals, and expose zero/missing rows on demand")
        page.get_by_role("button", name="Trang phiếu trước").click()
        expect(dialog).to_contain_text("TEST-PHIEU-001")
        page.screenshot(path=str(output / "browser-voyage-desktop.png"), full_page=True)
        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        expect(open_button).to_be_focused()
        checks.append("detail: source rows paginate; Escape closes and returns keyboard focus")

        page.get_by_label("Tìm tàu hoặc mã chuyến").fill("")
        page.get_by_role("button", name="Xem chi tiết BEN THUY TEST · TEST-101 · Bến Thủy").click()
        expect(dialog).to_contain_text("2.000")
        assert requests[-1]["terminal"] == "ben_thuy"
        dialog.get_by_label("Hiển thị tác nghiệp").select_option("missing_weight")
        expect(dialog.locator(".operation-empty")).to_be_visible()
        expect(dialog.locator(".voyage-summary")).to_contain_text("2.000")
        expect(dialog.locator(".voyage-metadata")).to_be_visible()
        expect(dialog.locator(".voyage-breakdowns")).to_be_visible()
        expect(dialog.locator(".voyage-section-heading > span")).to_have_text("0/28 dòng")
        expect(dialog.locator(".operations-table")).to_have_count(0)
        checks.append("empty filtered table retains voyage metadata and all-source summary/charts")
        page.get_by_role("button", name="Đóng chi tiết chuyến tàu").click()
        checks.append("terminal identity: a matching numeric ID opens the correct source voyage")

        state["detail"] = "error"
        open_button.click()
        expect(dialog.get_by_role("alert")).to_be_visible()
        expect(dialog.get_by_text("TEST-PHIEU-001", exact=True)).to_have_count(0)
        expect(page.locator(".kpi-card")).to_have_count(3)
        page.get_by_role("button", name="Đóng chi tiết chuyến tàu").click()
        checks.append("detail failure: visible error, no stale source rows, dashboard remains intact")

        state["detail"] = "data"
        open_button.click()
        expect(dialog).to_contain_text("TEST-PHIEU-001")
        state["detail"] = "hold"
        dialog.get_by_label("Hiển thị tác nghiệp").select_option("all")
        expect(dialog.locator(".voyage-detail-loading")).to_be_visible()
        page.keyboard.press("Escape")
        page.get_by_label("Phạm vi xí nghiệp").select_option("ben_thuy")
        page.get_by_role("button", name="Áp dụng", exact=True).click()
        expect(page.locator(".kpi-value").nth(2)).to_contain_text("1")
        state["detail"] = "data"
        for route, filters, terminal, number, size, operation_filter in held:
            try:
                route.fulfill(json=detail_fixture(filters, terminal, number, size, operation_filter))
            except Error:
                pass
        expect(dialog).not_to_be_visible()
        expect(page.locator(".voyages-panel")).not_to_contain_text("CUA LO TEST")
        checks.append("filter race: cancelled operation-filter response cannot reopen detail after scope changes")

        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="Xem chi tiết BEN THUY TEST · TEST-101 · Bến Thủy").click()
        expect(dialog).to_contain_text("TEST-PHIEU-001")
        page.wait_for_function("document.documentElement.scrollWidth <= innerWidth")
        box = dialog.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 391
        page.screenshot(path=str(output / "browser-voyage-mobile.png"), full_page=True)
        page.get_by_role("button", name="Đóng chi tiết chuyến tàu").click()
        checks.append("mobile: detail and source-table scrolling stay inside the 390px viewport")

        state["detail"] = "malformed"
        page.get_by_role("button", name="Xem chi tiết BEN THUY TEST · TEST-101 · Bến Thủy").click()
        expect(dialog.get_by_role("alert")).to_be_visible()
        assert errors == [], errors
        checks.append("contract: malformed detail response fails visibly without a render crash")
        browser.close()

    result = {"status": "passed", "data": "synthetic browser fixtures only", "checks": checks, "page_errors": errors}
    (output / "browser-voyage-checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
