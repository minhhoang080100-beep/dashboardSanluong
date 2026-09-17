"""Quarter presets and leadership targets in React, with intercepted APIs only.

The browser and Python fixture share a fixed Vietnam calendar day. No source
reads, user changes, plan writes or approvals reach an application server.
"""
import argparse
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser_auth_support import install_auth_fixture
from browser_smoke import fixture
from backend import repository
from backend.control_store import plan_period, saved_plan_period, throughput_progress_item
from playwright.sync_api import Error, expect, sync_playwright


TODAY = date(2026, 9, 17)
FILTER_KEYS = ("start_date", "end_date", "terminal", "production_scope")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    repository.vietnam_today = lambda: TODAY
    output = Path("outputs")
    output.mkdir(exist_ok=True)
    state = {"reports": {}, "plans": [], "requests": [], "payloads": [], "filters": None,
             "data_mode": "ready", "progress_mode": "auto", "percentage": 50,
             "hold_next": False, "held": []}
    checks, errors = [], []

    def progress(report):
        filters = report["meta"]["filters"]
        eligible = filters["production_scope"] == "nghe_tinh"
        result = {"report_id": report["meta"]["report_id"], "period": dict(filters),
                  "production_scope": filters["production_scope"], "berth_rule_version": "initial-berth-v1",
                  "eligible": eligible, "reason": None, "items": [], "available_periods": []}
        if not eligible:
            result["reason"] = "Chỉ đối chiếu kế hoạch Nghệ Tĩnh với cùng phạm vi sản lượng."
            return result
        actual, status = report["overview"]["total_tonnage"], report["overview"]["tonnage_status"]
        if state["progress_mode"] == "auto":
            for plan in state["plans"]:
                if plan['status'] != 'approved' or not plan['is_current'] or plan['terminal'] != filters['terminal']:
                    continue
                result['available_periods'].append({'key': f"{plan['period_type']}:{plan['period_key']}",
                    'period_type': plan['period_type'], 'period_key': plan['period_key'], 'terminal': plan['terminal'],
                    'start_date': plan['period_start'], 'end_date': plan['period_end'], 'target': plan['amount'], 'plans': [plan]})
                if plan["period_start"] == filters["start_date"] and plan["period_end"] >= filters["end_date"]:
                    result["items"].append(throughput_progress_item(plan, [plan], actual, status, "company"))
        elif state["progress_mode"] != "missing":
            target = 0 if state["progress_mode"] == "zero" else (actual or 100) * 100 / state["percentage"]
            plan = {"id": 999, "version": 1, "terminal": filters["terminal"], "period_type": "custom",
                    "period_key": filters["start_date"] + "/" + filters["end_date"],
                    "period_start": filters["start_date"], "period_end": filters["end_date"],
                    "amount_decimal": str(target), "reference": "SYNTHETIC TARGET", "approved_at": "2026-09-17T05:00:00Z"}
            result["items"] = [throughput_progress_item(plan, [plan], actual, status, "company",
                                                         complete_target=state["progress_mode"] != "missing_target")]
            result['available_periods'] = [{key: result['items'][0][key] for key in ('key', 'period_type', 'period_key', 'start_date', 'end_date', 'target')}]
        if not result["items"]:
            result["reason"] = "Chưa có kế hoạch được duyệt khớp kỳ và phạm vi báo cáo."
        if state["progress_mode"] == "mismatch":
            result["production_scope"] = "vietsun"
        return result

    def respond(route):
        request = route.request
        path = urlparse(request.url).path.removeprefix("/api")
        query = {key: values[0] for key, values in parse_qs(urlparse(request.url).query).items()}
        state["requests"].append({"path": path, "method": request.method, "query": query})
        if path == "/dashboard":
            filters = {key: query[key] for key in FILTER_KEYS}
            assert filters["end_date"] <= TODAY.isoformat(), "future actual request"
            assert filters["start_date"] <= filters["end_date"]
            state["filters"] = filters
            data = fixture(filters, native=state["data_mode"] == "partial", missing=state["data_mode"] == "unavailable",
                           empty=state["data_mode"] == "empty")
            report_id = f"synthetic-targets-{len(state['reports']) + 1}"
            data["meta"].update(report_id=report_id, source_read_at="2026-09-17T05:00:00Z")
            state["reports"][report_id] = data
            route.fulfill(json=data)
        elif path.endswith("/throughput-progress"):
            report = state["reports"][path.split("/")[2]]
            response = progress(report)
            if state["hold_next"]:
                state["hold_next"] = False
                state["held"].append((route, response))
            else:
                route.fulfill(json=response)
        elif path.endswith("/plan-progress"):
            route.fulfill(json={"eligible": False, "reason": "Synthetic monthly table is outside this check.", "rows": []})
        elif path == "/plans" and request.method == "GET":
            items = [plan for plan in state["plans"] if plan["period_type"] == query.get("period_type", plan["period_type"])]
            route.fulfill(json={"items": items, "total": len(items), "page": 1, "page_size": 25})
        elif path == "/plans" and request.method == "POST":
            body = request.post_data_json
            state["payloads"].append(deepcopy(body))
            assert body["metric"] == "tonnage" and body["terminal"] == "all"
            key, _, _ = plan_period(body)
            plan = {**body, **saved_plan_period(body["period_type"], key), "id": len(state["plans"]) + 1,
                    "version": 1, "revision": 1, "status": "draft", "is_current": False,
                    "amount": float(body['amount']), "amount_decimal": str(body["amount"]), "created_at": "2026-09-17T05:00:00Z"}
            state["plans"].append(plan)
            route.fulfill(status=201, json=plan)
        elif path.startswith("/plans/") and path.endswith("/approve"):
            plan = next(row for row in state["plans"] if str(row["id"]) == path.split("/")[2])
            assert request.post_data_json == {"expected_revision": plan["revision"]}
            plan.update(status="approved", is_current=True, revision=plan["revision"] + 1,
                        approved_at="2026-09-17T05:00:00Z", approved_by=9001)
            route.fulfill(json=plan)
        else:
            route.fallback()  # The authentication fixture denies every unknown API.

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.set_default_timeout(10000)
        page.clock.set_fixed_time(datetime(2026, 9, 17, 5, tzinfo=timezone.utc))
        auth = install_auth_fixture(page, role="admin")
        page.on("pageerror", lambda error: (errors.append(str(error)), print("BROWSER_ERROR: " + str(error))))
        page.route("**/api/**", respond)
        page.goto(args.url)
        panel = page.locator(".throughput-progress")
        expect(panel).to_contain_text("Chưa có kế hoạch được duyệt")

        def wait_filters(start, end):
            expect(page.locator("#start-date")).to_have_value(start)
            expect(page.locator("#end-date")).to_have_value(end)
            expect(page.locator(".kpi-card")).to_have_count(3)
            expect(panel).not_to_contain_text("Đang đọc kế hoạch")
            assert state["filters"]["start_date"] == start and state["filters"]["end_date"] == end

        def refresh():
            prior = len(state["reports"])
            with page.expect_response(lambda response: urlparse(response.url).path.endswith("/throughput-progress")):
                with page.expect_response(lambda response: urlparse(response.url).path.endswith("/dashboard")):
                    page.get_by_role("button", name="Tải lại báo cáo đang chọn", exact=True).click()
            expect(page.locator(".refresh-progress")).to_have_count(0)
            expect(panel).not_to_contain_text("Đang đọc kế hoạch")
            assert len(state["reports"]) > prior

        quarters = page.get_by_role("group", name="Chọn quý báo cáo", exact=True)
        for quarter, start, end in [(1, "2026-01-01", "2026-03-31"), (2, "2026-04-01", "2026-06-30"),
                                    (3, "2026-07-01", "2026-09-17")]:
            quarters.get_by_role("button", name=f"Quý {quarter}", exact=True).click()
            wait_filters(start, end)
        expect(quarters.get_by_role("button", name="Quý 4", exact=True)).to_be_disabled()
        assert not any(row["path"] == "/dashboard" and row["query"].get("start_date") == "2026-10-01" for row in state["requests"])
        page.locator("#quarter-year").select_option("2025")
        quarters.get_by_role("button", name="Quý 4", exact=True).click()
        wait_filters("2025-10-01", "2025-12-31")
        page.locator("#quarter-year").select_option("2026")
        page.get_by_role("button", name="Tháng này", exact=True).click()
        wait_filters("2026-09-01", "2026-09-17")
        checks.append("Q1/Q2 exact dates; current Q3 stops today; future Q4 disabled; historical Q4 selectable")

        report_reads = len(state["requests"])
        page.get_by_role("navigation", name="Điều hướng chính", exact=True).get_by_role("link", name="Kế hoạch", exact=True).click()
        management = page.locator("#management-content")
        expect(page.get_by_role("heading", name="Kế hoạch", level=1, exact=True)).to_be_visible()
        expect(management).to_have_attribute("aria-label", "Kế hoạch")
        expect(page.locator(".filter-panel, .report-toolbar, .production-scope-selector")).to_have_count(0)
        expect(page.locator(".throughput-progress, .management-secondary-progress")).to_have_count(0)
        create = management.locator("details.management-editor").first
        management.get_by_role('combobox', name='Danh sách kế hoạch', exact=True).select_option('year')
        management.get_by_label('Năm kế hoạch', exact=True).fill('2025')
        management.get_by_role('button', name='Tạo kế hoạch', exact=True).click()
        expect(create.get_by_role('combobox', name='Loại kế hoạch', exact=True)).to_have_value('year')
        expect(create.get_by_label('Năm áp dụng', exact=True)).to_have_value('2025')
        assert state['payloads'] == [], 'opening a blank form from list filters must not save a plan'
        create.locator('summary').click()
        management.get_by_role('combobox', name='Danh sách kế hoạch', exact=True).select_option('month')
        management.get_by_role('button', name='Tạo kế hoạch', exact=True).click()
        expect(create.get_by_role('combobox', name='Loại kế hoạch', exact=True)).to_have_value('month')
        expect(create.get_by_label('Tháng áp dụng', exact=True)).to_have_value('2026-09')
        checks.append('blank plan form follows its own list period instead of the hidden report period')
        create.get_by_role('button', name='Lưu bản nháp', exact=True).click()
        expect(create.get_by_role('alert')).to_contain_text('Chưa lưu')
        expect(create.get_by_label('Giá trị kế hoạch', exact=True)).to_have_attribute('aria-invalid', 'true')
        assert state['payloads'] == [], 'invalid form must not reach API'
        dashboard_reads = sum(row['path'] == '/dashboard' for row in state['requests'])
        for kind in ("month", "quarter", "year", "custom"):
            create.get_by_role("combobox", name="Loại kế hoạch", exact=True).select_option(kind)
            create.get_by_role("combobox", name="Xí nghiệp kế hoạch", exact=True).select_option("all")
            if kind == "month":
                create.get_by_label("Tháng áp dụng", exact=True).fill("2026-09")
            elif kind == "quarter":
                create.get_by_role("combobox", name="Quý áp dụng", exact=True).select_option("3")
                create.get_by_label("Năm áp dụng", exact=True).fill("2026")
            elif kind == "year":
                create.get_by_label("Năm áp dụng", exact=True).fill("2026")
            else:
                create.get_by_label("Từ ngày kế hoạch", exact=True).fill("2026-09-05")
                create.get_by_label("Đến ngày kế hoạch", exact=True).fill("2026-09-25")
            create.get_by_label("Giá trị kế hoạch", exact=True).fill("4.000")
            expect(create).to_contain_text('Sẽ lưu: 4.000 tấn')
            reference = f"SYNTHETIC-{kind.upper()}"
            create.get_by_label("Số văn bản / nguồn phê duyệt", exact=True).fill(reference)
            create.get_by_role("button", name="Lưu bản nháp", exact=True).click()
            plan_row = management.get_by_role("row").filter(has_text=reference)
            expect(plan_row).to_contain_text("Bản nháp")
            plan_row.get_by_role("button", name="Duyệt", exact=True).click()
            expect(plan_row).to_contain_text("Đã duyệt")
        assert [row["period_type"] for row in state["payloads"]] == ["month", "quarter", "year", "custom"]
        assert state["payloads"][1]["quarter"] == "2026-Q3"
        assert state["payloads"][2]["year"] == 2026
        assert state["payloads"][3]["start_date"] == "2026-09-05"
        assert all(row['amount'] == '4000' for row in state['payloads'])
        assert sum(row['path'] == '/dashboard' for row in state['requests']) == dashboard_reads, 'approval must not reread production SQL'
        assert not any(row['path'] == '/dashboard' or row['path'].startswith('/reports/') for row in state['requests'][report_reads:]), 'plan entry and approval must not request report progress'
        checks.append("month/quarter/year/custom company targets create and approve using only planning APIs, with no report controls or progress panel")

        management.get_by_role('combobox', name='Danh sách kế hoạch', exact=True).select_option('year')
        annual_row = management.get_by_role('row').filter(has_text='SYNTHETIC-YEAR')
        annual_row.get_by_role('button', name='Tạo phiên bản mới', exact=True).click()
        expect(create.get_by_label('Giá trị kế hoạch', exact=True)).to_have_value('4.000')
        assert len(state['plans']) == 4 and all(plan['status'] == 'approved' for plan in state['plans'])
        annual_row.get_by_role('button', name='Xem tiến độ', exact=True).click()
        expect(page.locator('.top-nav a[href="#overview"]')).to_have_attribute('aria-current', 'page')
        wait_filters('2026-01-01', '2026-09-17')
        expect(panel).to_contain_text('Năm 2026')
        panel.get_by_role('combobox', name='Kế hoạch đối chiếu', exact=True).select_option('month:2026-09')
        wait_filters('2026-09-01', '2026-09-17')
        checks.append('approved annual plan opens year-to-date actuals; cloned version stays unsaved until explicit submission')
        expect(panel.get_by_role("progressbar")).to_have_attribute("aria-valuenow", "75.003125")
        expect(panel).to_contain_text("Tháng 9/2026")
        expect(panel).to_contain_text("30/09/2026")
        quarters.get_by_role("button", name="Quý 3", exact=True).click()
        expect(panel).to_contain_text("Quý 3/2026")
        page.get_by_role("button", name="Từ đầu năm", exact=True).click()
        expect(panel).to_contain_text("Năm 2026")
        expect(panel).to_contain_text("31/12/2026")
        page.locator("#start-date").fill("2026-09-05")
        page.locator("#end-date").fill("2026-09-17")
        page.get_by_role("button", name="Áp dụng", exact=True).click()
        expect(panel).to_contain_text("Kỳ tùy chọn")
        expect(panel).to_contain_text("25/09/2026")
        checks.append("approved target keeps its full bounds while actual follows the displayed report snapshot")

        state["progress_mode"] = "percentage"
        for percentage, band in [(10, "Dưới 20%"), (20, "20–<40%"), (40, "40–<60%"),
                                 (60, "60–<80%"), (80, "Từ 80%"), (125, "Từ 80%")]:
            state["percentage"] = percentage
            refresh()
            expect(panel.locator(".current-band")).to_have_text(band)
            expect(panel.locator(".throughput-progress-segment")).to_have_count(5)
            expect(panel.get_by_role("progressbar")).to_have_attribute("aria-valuetext", f"{percentage}% kế hoạch")
            assert abs(float(panel.get_by_role("progressbar").get_attribute("aria-valuenow")) - min(100, percentage)) < 1e-8
        expect(panel).to_contain_text("125%")
        expect(panel).to_contain_text("Đạt kế hoạch")
        checks.append("all five bands use their boundaries; >100% retains percentage and caps the bar")

        state["data_mode"] = "partial"
        refresh()
        expect(panel).to_contain_text("Tạm tính theo số liệu đã ghi nhận")
        expect(panel.get_by_role("progressbar")).to_have_attribute("aria-valuetext", "Tạm tính 125% kế hoạch")
        expect(panel.get_by_text("Đạt kế hoạch", exact=True)).to_have_count(0)
        panel.screenshot(path=str(output / "browser-targets-provisional.png"))
        state["data_mode"] = "unavailable"
        refresh()
        expect(panel.get_by_role("progressbar")).to_have_count(0)
        state["data_mode"] = "ready"
        state["progress_mode"] = "zero"
        refresh()
        expect(panel).to_contain_text("bằng 0")
        expect(panel.get_by_role("progressbar")).to_have_count(0)
        state["progress_mode"] = "missing_target"
        refresh()
        expect(panel.get_by_role("progressbar")).to_have_count(0)
        state["progress_mode"] = "missing"
        refresh()
        expect(panel).to_contain_text("Chưa có kế hoạch được duyệt")
        checks.append("partial >100% remains provisional; unavailable, zero and missing targets never claim achievement")

        state["progress_mode"] = "mismatch"
        refresh()
        expect(panel.get_by_role("alert")).to_contain_text("chưa khớp kỳ và phạm vi")
        expect(panel.get_by_role("progressbar")).to_have_count(0)
        state["progress_mode"] = "percentage"
        state["hold_next"] = True
        page.get_by_role("button", name="Tải lại báo cáo đang chọn", exact=True).click()
        expect(panel).to_contain_text("Đang đọc kế hoạch")
        page.get_by_role("tab", name="Cầu 5", exact=True).click()
        expect(panel).to_contain_text("Chỉ đối chiếu kế hoạch Nghệ Tĩnh")
        assert len(state["held"]) == 1
        for held_route, response in state["held"]:
            try:
                held_route.fulfill(json=response)
            except Error:
                pass  # The old request may already have been aborted by React.
        expect(panel.get_by_role("progressbar")).to_have_count(0)
        quarters.get_by_role("button", name="Quý 2", exact=True).click()
        wait_filters("2026-04-01", "2026-06-30")
        assert state["filters"]["production_scope"] == "vietsun" and state["filters"]["terminal"] == "all"
        expect(panel.get_by_role("progressbar")).to_have_count(0)
        page.get_by_role("tab", name="Chưa xác định cầu", exact=True).click()
        expect(panel).to_contain_text("Chỉ đối chiếu kế hoạch Nghệ Tĩnh")
        expect(panel.get_by_role("link", name="Nhập kế hoạch", exact=True)).to_have_count(0)
        checks.append("scope mismatch is rejected; late Nghệ Tĩnh response cannot populate Vietsun/unclassified")

        page.get_by_role("tab", name="Cảng Nghệ Tĩnh", exact=True).click()
        expect(panel.get_by_role("progressbar")).to_be_visible()
        page.screenshot(path=str(output / "browser-targets-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 375, "height": 900})
        expect(panel.get_by_role("progressbar")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile horizontal overflow"
        panel.screenshot(path=str(output / "browser-targets-mobile.png"))
        checks.append("375px progress and quarter controls stay within viewport")
        assert not errors, errors
        assert not auth["unexpected"], auth["unexpected"]
        assert all(row["status"] == "approved" for row in state["plans"])
        print(json.dumps({"passed": len(checks), "checks": checks, "page_errors": errors,
                          "unexpected_api": auth["unexpected"], "synthetic_approved_plans": len(state["plans"])}, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
