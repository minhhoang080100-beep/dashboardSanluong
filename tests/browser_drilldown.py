"""Snapshot drill-down and expiry recovery using intercepted synthetic facts only.

Run from the repository root with Vite running locally. Every API request is
handled by a fixture or denied by browser_auth_support; no production data is read.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser_auth_support import install_auth_fixture
from browser_voyages import detail_fixture, report_fixture
from playwright.sync_api import expect, sync_playwright


def operation_fixture(report, selection):
    filters = {key: report["meta"]["filters"][key] for key in ("start_date", "end_date", "terminal", "production_scope")}
    rows = []
    for terminal in ("cua_lo", "ben_thuy"):
        if filters["terminal"] not in ("all", terminal):
            continue
        detail = detail_fixture(filters, terminal, 1, 100, "all")
        for row in detail["operations"]["rows"]:
            rows.append({**row, **{key: detail["header"][key] for key in ("initial_berth_id", "initial_berth_code", "initial_berth_at", "berth_assignment_status", "production_scope")}, "source_id": row["id"], "source_type": "tally_shift",
                "row_key": f"{terminal}:{row['id']}", "terminal_id": terminal,
                "terminal_name": detail["header"]["terminal_name"], "voyage_id": "101",
                "source_voyage_id": "101", "vessel_name": detail["header"]["vessel_name"],
                "voyage_code": "TEST-101", "customer_id": "customer-1",
                "customer_name": "KHÁCH KIỂM THỬ", "cargo_group": "Hàng kiểm thử"})
    for key, field in (("day", "operation_date"), ("terminal", "terminal_id"),
                       ("cargo", "cargo_group"), ("customer_id", "customer_id"),
                       ("customer_terminal", "terminal_id"), ("voyage_id", "voyage_id")):
        if selection.get(key) not in (None, "all"):
            rows = [row for row in rows if row[field] == selection[key]]
    summary = {"tonnage": round(sum(row["tonnage"] or 0 for row in rows), 3),
               "teu": sum(row["teu"] or 0 for row in rows), "record_count": len(rows)}
    groups = {"all": rows, "with_values": [row for row in rows if row["quantity"] != 0 or row["weight"] not in (None, 0)],
              "missing_weight": [row for row in rows if row["weight"] is None]}
    operation_filter = selection.get("operation_filter", "all")
    selected = groups[operation_filter]
    page, size = int(selection.get("page", 1)), int(selection.get("page_size", 25))
    return {"report_id": report["meta"]["report_id"], "summary": summary,
            "operations": {"filter": operation_filter, "page": page, "page_size": size,
                "total": len(selected), "total_all": len(rows),
                "total_pages": (len(selected) + size - 1) // size,
                "counts": {key: len(value) for key, value in groups.items()},
                "rows": selected[(page - 1) * size:page * size]},
            "meta": {"report_id": report["meta"]["report_id"], "filters": {**selection, "production_scope": filters["production_scope"]}, "berth_rule_version": "initial-berth-v1",
                     "source_read_at": report["meta"]["source_read_at"]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    output = Path("outputs")
    output.mkdir(exist_ok=True)
    state = {"reports": {}, "current_id": None, "expired": set(), "requests": []}
    errors, checks = [], []

    def respond(route):
        parsed = urlparse(route.request.url)
        path = parsed.path.removeprefix("/api")
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        state["requests"].append({"path": path, "query": query})
        if path == "/dashboard":
            filters = {key: query[key] for key in ("start_date", "end_date", "terminal", "production_scope")}
            data = report_fixture(filters)
            identifier = f"synthetic-report-{len(state['reports']) + 1}"
            state["current_id"] = identifier
            data["meta"].update(report_id=identifier, source_read_at=data["meta"]["generated_at"])
            names = {"Cửa Lò": "cua_lo", "Bến Thủy": "ben_thuy"}
            for row in data["terminals"]:
                row["terminal_id"] = names[row["name"]]
            for row in data["customers"]:
                row["drilldown_customer_id"] = row["customer_id"]
            state["reports"][identifier] = deepcopy(data)
            route.fulfill(json=data)
            return
        if path.startswith("/reports/"):
            identifier = path.split("/")[2]
            if identifier in state["expired"]:
                route.fulfill(status=410, json={"detail": {"code": "REPORT_EXPIRED",
                    "message": "Phiên dữ liệu đã hết hạn. Hãy tải lại báo cáo rồi mở chi tiết."}})
            elif identifier not in state["reports"]:
                route.fulfill(status=404, json={"detail": "Unknown synthetic report"})
            elif path.endswith("/plan-progress"):
                route.fulfill(json={"eligible": True, "rows": []})
            elif path.endswith("/operations"):
                route.fulfill(json=operation_fixture(state["reports"][identifier], query))
            else:
                route.fallback()
            return
        if path.startswith("/voyages/"):
            terminal = path.split("/")[2]
            if path.endswith("/progress"):
                data = state["reports"][state["current_id"]]
                filters = {key: data["meta"]["filters"][key] for key in ("start_date", "end_date", "terminal", "production_scope")}
                detail = detail_fixture(filters, terminal, 1, 25)
                route.fulfill(json={"header": detail["header"], "summary": detail["summary"], "shifts": [], "planning": [],
                    "meta": {"scope": "whole_voyage", "filters": {"production_scope": query["production_scope"]}, "berth_rule_version": "initial-berth-v1", "source_read_at": data["meta"]["source_read_at"]}})
                return
            identifier = query.get("report_id")
            if identifier in state["expired"]:
                route.fulfill(status=410, json={"detail": {"code": "REPORT_EXPIRED"}})
                return
            data = state["reports"][identifier]
            filters = {key: query[key] for key in ("start_date", "end_date", "production_scope")}
            detail = detail_fixture(filters, terminal, int(query.get("page", 1)), int(query.get("page_size", 25)),
                                    query.get("operation_filter", "all"))
            detail["meta"].update(report_id=identifier, source_read_at=data["meta"]["source_read_at"])
            route.fulfill(json=detail)
            return
        route.fallback()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(10000)
        auth = install_auth_fixture(page)
        page.route("**/api/**", respond)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url)
        expect(page.locator(".kpi-card")).to_have_count(3)
        expect(page.get_by_role("button", name="Xem chi tiết Sản lượng thông qua", exact=True)).to_be_visible()
        inspector = page.get_by_role("dialog", name="Chi tiết sản lượng", exact=True)
        voyage_dialog = page.get_by_role("dialog", name="Chi tiết chuyến tàu", exact=True)

        def open_kpi():
            page.get_by_role("button", name="Xem chi tiết Sản lượng thông qua", exact=True).click()
            expect(inspector).to_be_visible()
            expect(inspector.locator(".operations-table tbody tr")).to_have_count(25)

        def recover(dialog, previous_id):
            expect(dialog.get_by_role("button", name="Tải lại báo cáo", exact=True)).to_be_visible()
            with page.expect_response("**/api/dashboard?*"):
                dialog.get_by_role("button", name="Tải lại báo cáo", exact=True).click()
            expect(dialog).not_to_be_visible()
            expect(page.locator(".kpi-card")).to_have_count(3)
            assert state["current_id"] != previous_id
            dashboards = [row for row in state["requests"] if row["path"] == "/dashboard"]
            assert dashboards[-1]["query"].get("refresh") == "true"

        open_kpi()
        initial_id = state["current_id"]
        expect(inspector.locator(".voyage-summary")).to_contain_text("3.000,125")
        assert state["requests"][-1]["path"] == f"/reports/{initial_id}/operations"
        assert "day" not in state["requests"][-1]["query"]
        inspector.get_by_role("button", name="Sau", exact=True).click()
        expect(inspector.locator(".voyage-pagination")).to_contain_text("Trang 2/3")
        assert state["requests"][-1]["query"]["page"] == "2"
        inspector.get_by_role("combobox").select_option("missing_weight")
        expect(inspector.locator(".operations-table tbody tr")).to_have_count(1)
        expect(inspector.locator(".voyage-summary")).to_contain_text("3.000,125")
        assert state["requests"][-1]["query"]["page"] == "1"
        page.keyboard.press("Escape")
        expect(inspector).not_to_be_visible()
        checks.append("KPI drills retain report ID, totals and compound rows; filtering resets pagination")

        expect(page.locator(".history-chart .recharts-bar-rectangle").first).to_be_visible()
        page.locator(".history-chart .recharts-bar-rectangle").first.click()
        expect(inspector).to_be_visible()
        expect(inspector.locator(".operations-table tbody tr")).to_have_count(25)
        request = state["requests"][-1]
        assert request["query"]["day"] == state["reports"][initial_id]["meta"]["filters"]["start_date"]
        assert request["path"] == f"/reports/{initial_id}/operations"
        page.screenshot(path=str(output / "browser-drilldown-day.png"), full_page=True)
        page.keyboard.press("Escape")
        checks.append("Clicking a chart bar opens operations for that exact day in the same report")

        open_kpi()
        state["expired"].add(initial_id)
        inspector.get_by_role("button", name="Sau", exact=True).click()
        expect(inspector.get_by_role("alert")).to_contain_text("hết hạn")
        expect(inspector.locator(".operations-table")).to_have_count(0)
        recover(inspector, initial_id)
        expired_call_count = sum(row["path"] == f"/reports/{initial_id}/operations" for row in state["requests"])
        open_kpi()
        assert state["requests"][-1]["path"] == f"/reports/{state['current_id']}/operations"
        assert sum(row["path"] == f"/reports/{initial_id}/operations" for row in state["requests"]) == expired_call_count
        page.keyboard.press("Escape")
        checks.append("Expired operations remove stale rows and reload the dashboard with a new report ID")

        open_voyage = page.get_by_role("button", name="Xem chi tiết CUA LO TEST · TEST-101 · Cửa Lò", exact=True)
        open_voyage.click()
        expect(voyage_dialog).to_contain_text("TEST-PHIEU-001")
        previous_id = state["current_id"]
        state["expired"].add(previous_id)
        voyage_dialog.get_by_role("button", name="Trang phiếu sau", exact=True).click()
        expect(voyage_dialog.get_by_role("alert")).to_contain_text("hết hạn")
        expect(voyage_dialog.get_by_role("button", name="Thử lại chi tiết", exact=True)).to_have_count(0)
        recover(voyage_dialog, previous_id)
        open_voyage.click()
        expect(voyage_dialog).to_contain_text("TEST-PHIEU-001")
        detail_calls = [row for row in state["requests"] if row["path"] == "/voyages/cua_lo/101"]
        assert detail_calls[-1]["query"]["report_id"] == state["current_id"]
        assert detail_calls[-1]["query"]["page"] == "1"
        checks.append("Expired voyage page closes detail and invokes parent report refresh before reopening page 1")

        previous_id = state["current_id"]
        state["expired"].add(previous_id)
        voyage_dialog.get_by_role("button", name="Xuất Excel chuyến tàu", exact=True).click()
        expect(voyage_dialog.get_by_role("alert")).to_contain_text("hết hạn")
        recover(voyage_dialog, previous_id)
        checks.append("Expired voyage Excel export uses the same parent report recovery action")

        open_kpi()
        previous_id = state["current_id"]
        state["expired"].add(previous_id)
        inspector.get_by_role("button", name="Xuất Excel", exact=True).click()
        expect(inspector.get_by_role("alert")).to_contain_text("hết hạn")
        recover(inspector, previous_id)
        checks.append("Expired general Excel export offers a fresh dashboard report")
        assert not errors, errors
        assert not auth["unexpected"], auth["unexpected"]
        browser.close()

    result = {"status": "passed", "data": "synthetic browser fixtures only", "checks": checks,
              "page_errors": errors, "unexpected_api_requests": auth["unexpected"]}
    (output / "browser-drilldown-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
