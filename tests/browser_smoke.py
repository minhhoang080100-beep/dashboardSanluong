"""Browser contract and interaction checks using synthetic, labeled data only.

Start Vite locally, then from the repository root run:
  .venv-audit/Scripts/python.exe tests/browser_smoke.py --url http://127.0.0.1:5175
Requires requirements-dev.txt and installed Google Chrome (or --channel msedge).
No fixture is exposed by the production API and no source database is queried.
"""
import argparse
import json
from datetime import date, timedelta
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.repository import DashboardRepository
from playwright.sync_api import Error, expect, sync_playwright


def fixture(filters, empty=False, native=False, missing=False):
    start = date.fromisoformat(filters["start_date"])
    rows = []
    terminals = [("cua_lo", "Cửa Lò", 1000.125, 10), ("ben_thuy", "Bến Thủy", 2000, 20)]
    for terminal, name, tonnage, teu in terminals:
        if filters["terminal"] not in ("all", terminal):
            continue
        rows.append({"kind": "source", "terminal_id": terminal, "latest_operation_at": start})
        if empty:
            continue
        for operation_day, ratio in [(start, 1), (start - timedelta(days=1), 0.5)]:
            rows.append({"kind": "fact", "terminal_id": terminal, "terminal_name": name,
                         "operation_day": operation_day, "vessel_id": "101",
                         "vessel_name": "CUA LO TEST" if terminal == "cua_lo" else "BEN THUY TEST",
                         "voyage_code": "TEST-101", "arrival_at": f"{start.isoformat()}T07:00:00",
                         "departure_at": None,
                         "cargo_name": "Hàng kiểm thử", "direction_id": 1 if terminal == "cua_lo" else 2,
                         "customer_id": "customer-1", "customer_name": "=SUM(1+1)",
                         "native_weight": None if missing else tonnage * ratio,
                         "unit_code": "TAN", "unit_name": "Tấn", "tonne_factor": 1,
                         "known_weight_count": 0 if missing else 1, "container_row_count": 1,
                         "teu": teu * ratio, "record_count": 1,
                         "latest_operation_at": operation_day, "missing_weight_count": 1 if missing else 0,
                         "missing_quantity_count": 0, "negative_value_count": 0})
        if native:
            rows.append({"kind": "fact", "terminal_id": terminal, "terminal_name": name,
                         "operation_day": start, "vessel_id": None,
                         "cargo_name": "Hàng kiểm thử đơn vị nguồn", "direction_id": 1,
                         "customer_id": "customer-2", "customer_name": "Đơn vị kiểm thử",
                         "native_weight": 5207, "unit_code": "GAU", "unit_name": "Gàu", "tonne_factor": None,
                         "known_weight_count": 1, "container_row_count": 0,
                         "teu": 0, "record_count": 1, "latest_operation_at": start,
                         "missing_weight_count": 0, "missing_quantity_count": 0, "negative_value_count": 0})
    repository = DashboardRepository()
    repository._execute_query = lambda *args, **kwargs: rows
    response = repository.get_dashboard(**filters)
    response["meta"]["warnings"].insert(0, "DỮ LIỆU KIỂM THỬ — không phải số liệu sản xuất.")
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5175")
    parser.add_argument("--channel", default="chrome")
    args = parser.parse_args()
    output = Path("outputs")
    output.mkdir(exist_ok=True)
    state = {"mode": "data", "delay_terminal": None}
    requests, held, errors, checks = [], [], [], []

    def respond(route):
        query = parse_qs(urlparse(route.request.url).query)
        filters = {key: query[key][0] for key in ("start_date", "end_date", "terminal")}
        requests.append(filters)
        if state["delay_terminal"] == filters["terminal"]:
            held.append((route, filters))
            return
        if state["mode"] == "error":
            route.fulfill(status=503, json={"detail": {"code": "DATABASE_UNAVAILABLE"}})
        elif state["mode"] == "malformed":
            route.fulfill(json={"overview": None})
        else:
            route.fulfill(json=fixture(filters, empty=state["mode"] == "empty",
                                      native=state["mode"] == "native", missing=state["mode"] == "missing"))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.channel, headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100}, accept_downloads=True)
        page = context.new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.route("**/api/dashboard?*", respond)
        page.goto(args.url)
        expect(page.get_by_role("heading", name="Bức tranh sản xuất")).to_be_visible()
        expect(page.locator(".kpi-card")).to_have_count(3)
        expect(page.locator(".kpi-value").first).to_contain_text("3.000,125")
        expect(page.get_by_text("Hai xí nghiệp. Một góc nhìn.", exact=True)).to_have_count(0)
        expect(page.get_by_text("Báo cáo quản trị nội bộ", exact=True)).to_have_count(0)
        expect(page.locator(".kpi-note, .metric-coverage, .metric-quality-tag, .provisional-note, .ratio-note")).to_have_count(0)
        expect(page.locator(".notice")).to_have_count(0)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        expect(page.locator(".history-chart svg").first).to_be_visible()
        page.screenshot(path=str(output / "browser-desktop.png"), full_page=True)
        checks.append("desktop: coherent totals, requested notes removed, no horizontal overflow")

        page.get_by_role("button", name="TEU", exact=True).click()
        expect(page.get_by_role("button", name="TEU", exact=True)).to_have_attribute("aria-pressed", "true")
        expect(page.get_by_role("heading", name="Sản lượng theo ngày", exact=True)).to_be_visible()
        page.get_by_role("button", name="Theo tháng", exact=True).click()
        expect(page.get_by_role("heading", name="Sản lượng theo tháng", exact=True)).to_be_visible()
        page.get_by_text("Xem bảng số liệu theo tháng", exact=True).click()
        expect(page.get_by_role("table").first).to_be_visible()
        checks.append("history: daily/monthly switch, unit switch and accessible data table")

        page.get_by_label("Phạm vi xí nghiệp").select_option("ben_thuy")
        expect(page.get_by_role("button", name="Xuất báo cáo CSV")).to_be_disabled()
        page.get_by_role("button", name="Áp dụng", exact=True).click()
        expect(page.locator(".kpi-value").first).to_contain_text("2.000")
        assert requests[-1]["terminal"] == "ben_thuy"
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Xuất báo cáo CSV").click()
        download = download_info.value
        csv = Path(download.path()).read_text(encoding="utf-8-sig")
        assert "Bến Thủy" in csv and "DỮ LIỆU KIỂM THỬ" in csv and "'=SUM(1+1)" in csv
        checks.append("filters and CSV: scope applied, draft disables export, formula escaping")

        old_count = len(requests)
        page.get_by_label("Từ ngày", exact=True).fill("2026-08-10")
        page.get_by_label("Đến ngày", exact=True).fill("2026-08-01")
        page.get_by_role("button", name="Áp dụng", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("Ngày bắt đầu")
        assert len(requests) == old_count
        checks.append("invalid dates: validation without a request")

        page.get_by_role("button", name="Tháng này", exact=True).click()
        expect(page.locator(".kpi-card")).to_have_count(3)
        state["delay_terminal"] = "ben_thuy"
        page.get_by_role("button", name="Tải lại báo cáo đang chọn").click()
        expect(page.get_by_text("Đang tổng hợp báo cáo", exact=True)).to_be_visible()
        page.get_by_label("Phạm vi xí nghiệp").select_option("cua_lo")
        page.get_by_role("button", name="Áp dụng", exact=True).click()
        expect(page.locator(".kpi-value").first).to_contain_text("1.000")
        for route, filters in held:
            try:
                route.fulfill(json=fixture(filters))
            except Error:
                pass  # The superseded request can already be aborted.
        state["delay_terminal"] = None
        expect(page.locator(".kpi-value").first).to_contain_text("1.000")
        checks.append("request race: superseded response cannot replace new scope")

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "browser-mobile.png"), full_page=True)
        checks.append("mobile: 390px layout without horizontal page overflow")

        state["mode"] = "error"
        page.get_by_role("button", name="Tải lại báo cáo đang chọn").click()
        expect(page.get_by_role("heading", name="Chưa tải được báo cáo")).to_be_visible()
        expect(page.locator(".kpi-card")).to_have_count(0)
        expect(page.get_by_role("button", name="Xuất báo cáo CSV")).to_be_disabled()
        state["mode"] = "data"
        page.get_by_role("button", name="Thử lại", exact=True).click()
        expect(page.locator(".kpi-card")).to_have_count(3)
        checks.append("503: explicit failure, no stale KPI/export; retry recovers")

        state["mode"] = "native"
        page.get_by_role("button", name="Tải lại báo cáo đang chọn").click()
        expect(page.locator(".kpi-value").first).to_contain_text("1.000,125")
        expect(page.locator(".native-units-panel")).to_contain_text("5.207")
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Xuất báo cáo CSV").click()
        native_csv = Path(download_info.value.path()).read_text(encoding="utf-8-sig")
        assert "Gàu" in native_csv and "5207" in native_csv
        page.screenshot(path=str(output / "browser-native-units.png"), full_page=True)
        checks.append("native units: separate from tonnes in KPI and CSV, decimal precision preserved")

        state["mode"] = "missing"
        page.get_by_role("button", name="Tải lại báo cáo đang chọn").click()
        expect(page.locator(".kpi-value").first).to_contain_text("—")
        expect(page.locator(".kpi-value").nth(1)).to_contain_text("10")
        checks.append("missing weights: unavailable tonnes displayed as dash, independent TEU retained")

        state["mode"] = "empty"
        page.get_by_role("button", name="Tải lại báo cáo đang chọn").click()
        expect(page.get_by_text("Không có phát sinh trong kỳ đã chọn", exact=True)).to_be_visible()
        expect(page.locator(".kpi-value").first).to_contain_text("0")
        checks.append("empty success: distinguish no records from service failure")

        state["mode"] = "malformed"
        page.get_by_role("button", name="Tải lại báo cáo đang chọn").click()
        expect(page.get_by_role("heading", name="Chưa tải được báo cáo")).to_be_visible()
        expect(page.locator(".kpi-card")).to_have_count(0)
        checks.append("malformed API data: visible failure without render crash")
        assert errors == [], errors
        browser.close()

    result = {"status": "passed", "data": "synthetic fixtures only; no live SQL verification",
              "checks": checks, "page_errors": errors}
    (output / "browser-checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
