"""Authenticated management workflows with intercepted synthetic data only.

No SQL reads, account changes, report closes or Excel imports reach a server.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser_auth_support import install_auth_fixture
from browser_voyages import report_fixture
from backend.control_store import plan_period, saved_plan_period, throughput_progress_item
from playwright.sync_api import expect, sync_playwright

PLAN_PERIOD_QUERY_KEYS = {"period_type", "week", "month", "quarter", "year", "start_date", "end_date", "voyage_id"}


def open_workspace(page, mode):
    labels = {"management": "Kế hoạch", "admin": "Quản trị"}
    page.get_by_role("navigation", name="Điều hướng chính", exact=True).get_by_role("link", name=labels[mode], exact=True).click()
    workspace = page.locator(f"#{mode}-content")
    expect(workspace).to_be_visible()
    return workspace


def open_reports(page):
    page.locator('.top-nav a[href="#overview"]').click()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    admin = {"id": 9001, "username": "test_admin", "display_name": "QUẢN TRỊ KIỂM THỬ", "role": "admin",
             "terminals": ["cua_lo", "ben_thuy"], "must_change_password": False, "is_active": True}
    viewer = {**admin, "id": 9002, "username": "test_viewer", "display_name": "NGƯỜI XEM KIỂM THỬ", "role": "viewer"}
    state = {"user": None, "plans": [], "closed": [], "issues": [], "users": [admin], "calls": [],
             "filters": None, "expire_plans": False, "dashboard_unavailable": False, "report_id": "synthetic-management-report",
             "hold_delete": False, "pending_delete": None}
    checks, errors = [], []

    def plan_values(body):
        key, _, _ = plan_period(body)
        return {**body, **saved_plan_period(body['period_type'], key),
                'amount': float(body['amount']), 'amount_decimal': str(body['amount'])}

    def respond(route):
        request = route.request
        path = urlparse(request.url).path.removeprefix("/api")
        query = {key: values[0] for key, values in parse_qs(urlparse(request.url).query).items()}
        method = request.method
        body = request.post_data_json if "application/json" in request.headers.get("content-type", "") else None
        state["calls"].append({"path": path, "method": method, "query": query})
        if path == "/auth/login":
            if body["username"] not in ("test_admin", "test_viewer"):
                route.fulfill(status=401, json={"detail": {"message": "Thông tin đăng nhập không đúng."}})
                return
            state["user"] = deepcopy(admin if body["username"] == "test_admin" else viewer)
            state["user"]["must_change_password"] = body["username"] == "test_admin"
            route.fulfill(json={"token": "synthetic-login-token", "user": state["user"]})
        elif path == "/auth/password":
            state["user"]["must_change_password"] = False
            route.fulfill(json={"token": "synthetic-updated-token", "user": state["user"]})
        elif path == "/auth/me":
            if state["user"]:
                route.fulfill(json=state["user"])
            else:
                route.fulfill(status=401, json={"detail": {"message": "Phiên đăng nhập đã hết hạn."}})
        elif path == "/auth/logout":
            state["user"] = None
            route.fulfill(json={"ok": True})
        elif path == "/dashboard":
            state["filters"] = {key: query[key] for key in ("start_date", "end_date", "terminal", "production_scope")}
            if state["dashboard_unavailable"]:
                route.fulfill(status=503, json={"detail": {"code": "DATABASE_UNAVAILABLE", "message": "Nguồn sản xuất tạm thời chưa truy cập được."}})
                return
            data = report_fixture(state["filters"])
            data["meta"].update(report_id=state["report_id"], source_read_at=data["meta"]["generated_at"])
            route.fulfill(json=data)
        elif path.endswith("/throughput-progress"):
            filters = state['filters']
            eligible = filters['production_scope'] == 'nghe_tinh'
            response = {'report_id': state['report_id'], 'period': dict(filters), 'production_scope': filters['production_scope'],
                        'berth_rule_version': 'initial-berth-v1', 'eligible': eligible, 'items': [], 'available_periods': [],
                        'reason': 'Chưa có kế hoạch được duyệt khớp kỳ và phạm vi báo cáo.'}
            report = report_fixture(filters)
            for plan in state['plans']:
                if not eligible or plan.get('is_deleted') or plan['status'] != 'approved' or not plan['is_current'] or plan['metric'] != 'tonnage' or plan['period_type'] == 'voyage' or plan['terminal'] != filters['terminal']:
                    continue
                response['available_periods'].append({'key': f"{plan['period_type']}:{plan['period_key']}",
                    'period_type': plan['period_type'], 'period_key': plan['period_key'], 'terminal': plan['terminal'],
                    'start_date': plan['period_start'], 'end_date': plan['period_end'], 'target': plan['amount'], 'plans': [plan]})
                if plan['period_start'] == filters['start_date'] and filters['end_date'] <= plan['period_end']:
                    response['items'].append(throughput_progress_item(plan, [plan], report['overview']['total_tonnage'], report['overview']['tonnage_status'], 'company'))
            route.fulfill(json=response)
        elif path.endswith("/plan-progress"):
            approved = next((plan for plan in state["plans"] if not plan.get("is_deleted") and plan["status"] == "approved" and plan["metric"] == "tonnage"), None)
            route.fulfill(json={"eligible": True, "reason": None, "period": state["filters"], "rows": [{
                "terminal": "cua_lo", "terminal_name": "Cửa Lò", "metric": "tonnage", "plan_id": approved["id"] if approved else None,
                "plan_version": approved["version"] if approved else None, "target": approved["amount"] if approved else None,
                "actual": 1000.125, "remaining": 0 if approved else None, "completion_percent": 1000.125 if approved else None,
                "actual_status": "ready", "status": "ready" if approved else "missing_plan"}]})
        elif path == "/plans" and method == "GET":
            if state["expire_plans"]:
                route.fulfill(status=401, json={"detail": {"message": "Phiên đăng nhập đã hết hạn."}})
                return
            assert query.get("period_type") != "all", "all plans must omit period parameters"
            items = [plan for plan in state["plans"]
                     if all(str(plan.get(key)) == query[key] for key in PLAN_PERIOD_QUERY_KEYS if key in query)
                     and (query.get("terminal", "all") == "all" or plan["terminal"] == query["terminal"])
                     and (query.get("include_deleted") == "true" or not plan.get("is_deleted"))]
            page_number = int(query.get("page", 1))
            route.fulfill(json={"items": items[(page_number - 1) * 25:page_number * 25], "total": len(items), "page": page_number, "page_size": 25})
        elif path == "/plans" and method == "POST":
            plan = {**plan_values(body), "id": len(state["plans"]) + 1, "version": 1, "revision": 1, "status": "draft", "is_current": False}
            state["plans"].append(plan)
            route.fulfill(status=201, json=plan)
        elif path.startswith("/plans/") and path.endswith("/approve"):
            plan = next(item for item in state["plans"] if str(item["id"]) == path.split("/")[2])
            assert body == {"expected_revision": plan["revision"]}
            plan.update(status="approved", is_current=True, revision=plan['revision'] + 1)
            route.fulfill(json=plan)
        elif path == "/planning/voyages":
            assert "start_date" not in query and "end_date" not in query
            route.fulfill(json={"items": [{"terminal": query["terminal"], "voyage_id": "901", "vessel_name": "TÀU NGOÀI KỲ", "voyage_code": "TEST-OUTSIDE-PERIOD", "arrival_date": "2026-10-01T00:00:00Z", "departure_date": None}], "limit": 30})
        elif path.startswith("/plans/") and path.endswith("/cancel"):
            plan = next(item for item in state["plans"] if str(item["id"]) == path.split("/")[2])
            assert body["expected_revision"] == plan["revision"] and body["note"]
            plan.update(status="cancelled", revision=plan["revision"] + 1)
            route.fulfill(json=plan)
        elif path.startswith("/plans/") and method == "DELETE":
            plan = next(item for item in state["plans"] if str(item["id"]) == path.split("/")[2])
            assert set(body) == {"revision"}
            if body["revision"] != plan["revision"]:
                route.fulfill(status=409, json={"detail": {"code": "PLAN_CONFLICT", "message": "Kế hoạch đã thay đổi. Tải lại danh sách trước khi xóa."}})
            elif state["hold_delete"]:
                state["pending_delete"] = (route, plan)
            else:
                plan.update(is_deleted=True, deleted_at="2026-09-13T08:00:00Z", deleted_by=9001, revision=plan["revision"] + 1, is_current=False)
                route.fulfill(json=plan)
        elif path.startswith("/plans/") and method == "PATCH":
            plan = next(item for item in state["plans"] if str(item["id"]) == path.split("/")[2])
            if body.pop("expected_revision") != plan["revision"]:
                route.fulfill(status=409, json={"detail": {"code": "PLAN_CONFLICT", "message": "Nháp đã thay đổi. Tải lại danh sách và kiểm tra trước khi sửa."}})
                return
            assert plan["status"] == "draft"
            plan.update(**plan_values(body), revision=plan["revision"] + 1)
            route.fulfill(json=plan)
        elif path.startswith("/plans/") and method == "GET" and path.split("/")[-1].isdigit():
            plan = next(item for item in state["plans"] if str(item["id"]) == path.split("/")[2])
            history = [{"id": 1, "action": "created", "actor_id": 9001, "created_at": "2026-09-13T07:00:00Z", "snapshot": plan, "note": "KIỂM THỬ LỊCH SỬ"}]
            if plan.get("is_deleted"):
                history.append({"id": 2, "action": "deleted", "actor_id": 9001, "created_at": plan["deleted_at"], "snapshot": plan, "note": ""})
            route.fulfill(json={**plan, "created_by": 9001, "created_at": "2026-09-13T07:00:00Z", "history": history})
        elif path == "/plans/import/preview":
            row = {"terminal": "cua_lo", "period_type": "month", "month": state["plans"][0]["month"],
                   "metric": "teu", "amount": 30, "reference": "KIỂM THỬ EXCEL", "note": ""}
            route.fulfill(json={"valid": True, "errors": [], "rows": [row]})
        elif path == "/plans/import":
            assert set(body) == {"rows"}
            items = [{**plan_values(row), "id": len(state["plans"]) + index + 1, "version": 1, "revision": 1, "status": "draft", "is_current": False} for index, row in enumerate(body["rows"])]
            state["plans"].extend(items)
            route.fulfill(status=201, json={"items": items, "count": len(items)})
        elif path.endswith("/operations"):
            row = {"id": "test-row-1", "source_id": "test-row-1", "source_type": "tally_shift", "row_key": "cua_lo:test-row-1",
                   "terminal_id": "cua_lo", "terminal_name": "Cửa Lò", "operation_code": "KIỂM THỬ-001",
                   "operation_date": state["filters"]["start_date"], "shift_code": "07-13", "vessel_name": "TÀU KIỂM THỬ",
                   "cargo_name": "Hàng kiểm thử", "quantity": 0, "quantity_unit": "TAN", "weight": None,
                   "weight_unit": "TAN", "tonnage": None, "teu": 0}
            rows = [row] if query.get("issue") == "missing_weight" else []
            route.fulfill(json={"report_id": state["report_id"], "operations": {"rows": rows, "total": len(rows), "total_all": 28, "page": 1, "page_size": 25, "total_pages": 1 if rows else 0}})
        elif path == "/issues" and method == "GET":
            route.fulfill(json={"items": state["issues"], "total": len(state["issues"]), "page": 1, "page_size": 25})
        elif path == "/issues" and method == "POST":
            assert body["namespace"] == "tally_shift" and body["source_id"] == "test-row-1"
            item = {**body, "id": len(state["issues"]) + 1, "actor_id": 9001, "updated_at": "2026-09-13T07:00:00Z"}
            state["issues"].append(item)
            route.fulfill(status=201, json=item)
        elif path == "/closed-reports" and method == "GET":
            route.fulfill(json={"items": state["closed"], "total": len(state["closed"]), "page": 1, "page_size": 25})
        elif path == "/closed-reports" and method == "POST":
            assert set(body) == {"report_id", "title", "note"} and body["report_id"] == state["report_id"]
            item = {"id": 1, **state["filters"], "berth_rule_version": "initial-berth-v1", "version": 1, "title": body["title"], "note": body["note"],
                    "source_fact_count": 56, "created_at": "2026-09-13T07:00:00Z", "created_by": 9001}
            state["closed"].append(item)
            route.fulfill(status=201, json=item)
        elif path.startswith("/closed-reports/") and path.endswith("/compare"):
            assert body == {"report_id": state["report_id"]}
            route.fulfill(json={"report_id": 1, "version": 1, "changed": True, "source_changed": True,
                                "changes": [{"metric": "total_tonnage", "closed": 1000, "current": 1001, "delta": 1}],
                                "compared_at": "2026-09-13T07:00:00Z"})
        elif path.startswith("/closed-reports/") and path.split("/")[-1].isdigit():
            item = state["closed"][0]
            route.fulfill(json={**item, "planning": {"captured": True, "eligible": True, "plans": [{"id": 1, "reference": "VĂN BẢN LƯU KHI CHỐT"}], "rows": [{"terminal": "cua_lo", "metric": "tonnage", "plan_id": 1, "plan_version": 1, "target": 100, "actual": 80, "completion_percent": 80}]}})
        elif path.endswith(".xlsx"):
            route.fulfill(body=b"PK\x03\x04synthetic-intercepted-download", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        elif path == "/users" and method == "GET":
            route.fulfill(json={"items": state["users"]})
        elif path == "/users" and method == "POST":
            assert "password" not in body
            item = {**body, "id": 9010, "is_active": True, "must_change_password": True}
            state["users"].append(item)
            route.fulfill(status=201, json={"user": item, "temporary_password": "synthetic-one-time-value"})
        elif path.startswith("/users/") and method == "PATCH":
            assert set(body) == {"display_name", "role", "terminals"}
            assert body == {"display_name": "QUẢN LÝ BẾN THỦY KIỂM THỬ", "role": "manager", "terminals": ["ben_thuy"]}
            item = next(item for item in state["users"] if str(item["id"]) == path.split("/")[2])
            item.update(body)
            route.fulfill(json=item)
        elif path.startswith("/users/") and path.endswith("/reset-password"):
            item = next(item for item in state["users"] if str(item["id"]) == path.split("/")[2])
            route.fulfill(json={"user": item, "temporary_password": "synthetic-reset-value"})
        elif path == "/admin/metrics":
            route.fulfill(json={"operations": {"get_report": {"request_count": 3, "success_count": 2, "failure_count": 1, "latency_ms_mean": 30, "latency_ms_max": 80}},
                                "cache": {"snapshots": 1, "stored_rows": 56, "inflight": 0, "hits": 2, "ttl_seconds": 60, "snapshot_ttl_seconds": 600, "max_snapshots": 10, "max_snapshot_rows": 10000}})
        else:
            route.fallback()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050}, accept_downloads=True)
        page.set_default_timeout(10000)
        auth = install_auth_fixture(page, signed_in=False)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", respond)
        page.goto(f"{args.url.rstrip('/')}#management")
        expect(page.get_by_role("heading", name="Đăng nhập", exact=True)).to_be_visible()
        page.get_by_label("Tài khoản", exact=True).fill("invalid_test")
        page.get_by_label("Mật khẩu", exact=True).fill("synthetic-password")
        page.get_by_role("button", name="Đăng nhập", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("không đúng")
        page.get_by_label("Tài khoản", exact=True).fill("test_admin")
        page.get_by_role("button", name="Đăng nhập", exact=True).click()
        expect(page.get_by_role("heading", name="Đổi mật khẩu", exact=True)).to_be_visible()
        expect(page.locator(".management")).to_have_count(0)
        page.get_by_label("Mật khẩu hiện tại", exact=True).fill("synthetic-password")
        page.get_by_label("Mật khẩu mới", exact=True).fill("synthetic-new-password")
        page.get_by_label("Nhập lại mật khẩu mới", exact=True).fill("synthetic-new-password")
        page.get_by_role("button", name="Lưu mật khẩu", exact=True).click()
        management = open_workspace(page, "management")
        expect(management.get_by_role("tab")).to_have_count(3)
        expect(page.get_by_role("heading", name="Kế hoạch", level=1, exact=True)).to_be_visible()
        expect(management).to_have_attribute("aria-label", "Kế hoạch")
        expect(management.get_by_role("heading", name="Danh sách kế hoạch", exact=True)).to_be_visible()
        expect(management.locator("details.management-editor").first).to_be_hidden()
        expect(management.get_by_role("button", name="Tạo kế hoạch", exact=True)).to_have_attribute("aria-expanded", "false")
        expect(management.get_by_role("button", name="Nhập Excel", exact=True)).to_have_attribute("aria-expanded", "false")
        expect(management.get_by_label("Tệp kế hoạch (.xlsx)", exact=True)).to_be_hidden()
        expect(management.get_by_text("Chưa có kế hoạch trong kỳ và phạm vi đã chọn.", exact=True)).to_be_visible()
        expect(management.get_by_role("combobox", name="Danh sách kế hoạch", exact=True)).to_have_value("all")
        expect(management.get_by_role("combobox", name="Xí nghiệp", exact=True)).to_have_value("all")
        initial_plan_query = next(call["query"] for call in state["calls"] if call["path"] == "/plans")
        assert PLAN_PERIOD_QUERY_KEYS.isdisjoint(initial_plan_query)
        assert initial_plan_query["terminal"] == "all" and initial_plan_query["page_size"] == "25"
        expect(page.locator(".filter-panel, .report-toolbar, .production-scope-selector")).to_have_count(0)
        expect(page.locator(".kpi-card, .throughput-progress, .management-secondary-progress")).to_have_count(0)
        assert not any(call["path"] == "/dashboard" or call["path"].startswith("/reports/") for call in state["calls"])
        checks.append("direct plans entry authenticates and changes the initial password without requesting production reports")

        management.get_by_role("button", name="Tạo kế hoạch", exact=True).click()
        expect(management.get_by_role("button", name="Tạo kế hoạch", exact=True)).to_have_attribute("aria-expanded", "true")
        expect(management.get_by_role("combobox", name="Loại kế hoạch", exact=True).locator('option[value="all"]')).to_have_count(0)
        management.get_by_label("Giá trị kế hoạch", exact=True).fill("100")
        management.get_by_label("Số văn bản / nguồn phê duyệt", exact=True).fill("KIỂM THỬ KH-01")
        management.get_by_role("button", name="Lưu bản nháp", exact=True).click()
        expect(management.locator(".plans-table").get_by_role("cell", name="KIỂM THỬ KH-01", exact=True)).to_be_visible()
        expect(management.get_by_role("combobox", name="Danh sách kế hoạch", exact=True)).to_have_value("all")
        assert PLAN_PERIOD_QUERY_KEYS.isdisjoint(next(call["query"] for call in reversed(state["calls"]) if call["path"] == "/plans" and call["method"] == "GET"))
        draft_row = management.get_by_role("row").filter(has_text="KIỂM THỬ KH-01")
        draft_row.get_by_role("button", name="Sửa nháp", exact=True).click()
        editor = management.get_by_role("region", name="Sửa bản nháp kế hoạch", exact=True)
        editor.get_by_label("Giá trị kế hoạch", exact=True).fill("120")
        state["plans"][0].update(amount=105, amount_decimal='105', revision=2)
        editor.get_by_role("button", name="Lưu thay đổi", exact=True).click()
        expect(editor.get_by_role("alert")).to_contain_text("Nháp đã thay đổi")
        expect(editor.get_by_label("Giá trị kế hoạch", exact=True)).to_have_value("120")
        assert state["plans"][0]["amount"] == 105
        editor.get_by_role("button", name="Đóng chỉnh sửa", exact=True).click()
        management.get_by_role("button", name="Tải lại", exact=True).click()
        expect(draft_row.get_by_role("cell", name="105", exact=True)).to_be_visible()
        draft_row.get_by_role("button", name="Sửa nháp", exact=True).click()
        editor.get_by_label("Giá trị kế hoạch", exact=True).fill("120")
        editor.get_by_role("button", name="Lưu thay đổi", exact=True).click()
        expect(editor).to_have_count(0)
        assert state["plans"][0]["amount"] == 120 and state["plans"][0]["revision"] == 3
        management.get_by_role("button", name="Duyệt", exact=True).click()
        expect(management.get_by_text("Đã duyệt", exact=True)).to_be_visible()
        assert state["plans"][0]["amount"] == 120 and state["plans"][0]["status"] == "approved"
        expect(draft_row.get_by_role("button", name="Sửa nháp", exact=True)).to_have_count(0)
        expect(draft_row.get_by_role("button", name="Hủy nháp", exact=True)).to_have_count(0)
        draft_row.get_by_role("button", name="Lịch sử", exact=True).click()
        expect(management.get_by_role("region", name="Lịch sử kế hoạch", exact=True)).to_contain_text("KIỂM THỬ LỊCH SỬ")
        management.get_by_role("button", name="Đóng lịch sử", exact=True).click()
        management.get_by_role("button", name="Nhập Excel", exact=True).click()
        expect(management.get_by_role("button", name="Nhập Excel", exact=True)).to_have_attribute("aria-expanded", "true")
        management.get_by_label("Tệp kế hoạch (.xlsx)", exact=True).set_input_files({"name": "synthetic-plan.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"intercepted fixture only"})
        management.get_by_role("button", name="Xem trước", exact=True).click()
        expect(management.get_by_text("KIỂM THỬ EXCEL", exact=True)).to_be_visible()
        assert len(state["plans"]) == 1
        management.get_by_role("button", name="Nhập 1 dòng kế hoạch", exact=True).click()
        expect(management.get_by_text("Đã nhập kế hoạch dưới dạng bản nháp.", exact=True)).to_be_visible()
        expect(management.get_by_label("Tệp kế hoạch (.xlsx)", exact=True)).to_have_value("")
        assert len(state["plans"]) == 2 and state["plans"][1]["status"] == "draft"
        imported_row = management.get_by_role("row").filter(has_text="KIỂM THỬ EXCEL")
        imported_row.get_by_role("button", name="Hủy nháp", exact=True).click()
        cancellation = management.get_by_role("region", name="Hủy bản nháp kế hoạch", exact=True)
        cancellation.get_by_label("Lý do hủy", exact=True).fill("Hủy bản nháp kiểm thử")
        cancellation.get_by_role("button", name="Xác nhận hủy bản nháp", exact=True).click()
        expect(imported_row).to_contain_text("Đã hủy")
        create = management.locator("details.management-editor").first
        if not create.evaluate("element => element.open"):
            management.get_by_role("button", name="Tạo kế hoạch", exact=True).click()
        create.get_by_role("combobox", name="Loại kế hoạch", exact=True).select_option("voyage")
        create.get_by_label("Tìm tàu hoặc mã chuyến", exact=True).fill("OUTSIDE")
        create.get_by_role("button", name="Tìm chuyến", exact=True).click()
        expect(create.get_by_role("combobox", name="Chuyến tàu kế hoạch", exact=True).locator("option[value='901']")).to_have_count(1)
        create.get_by_role("combobox", name="Chuyến tàu kế hoạch", exact=True).select_option("901")
        create.get_by_label("Giá trị kế hoạch", exact=True).fill("200")
        create.get_by_label("Số văn bản / nguồn phê duyệt", exact=True).fill("CHUYẾN NGOÀI KỲ")
        create.get_by_role("button", name="Lưu bản nháp", exact=True).click()
        expect(management.locator(".plans-table").get_by_role("cell", name="CHUYẾN NGOÀI KỲ", exact=True)).to_be_visible()
        assert state["plans"][2]["voyage_id"] == 901
        management.get_by_role("combobox", name="Danh sách kế hoạch", exact=True).select_option("month")
        checks.append("plan edit/cancel send revisions, approved versions stay immutable, history and out-of-period voyage lookup work")
        checks.append("Excel preview requires explicit import and creates cancellable drafts")

        # This extra approved TEU target exists only in the intercepted fixture.
        deletion_plan = {**deepcopy(state["plans"][0]), "id": 900, "metric": "teu", "reference": "KIỂM THỬ XÓA",
                         "amount": 30, "amount_decimal": "30", "revision": 1, "status": "approved", "is_current": True}
        state["plans"].append(deletion_plan)
        management.get_by_role("button", name="Tải lại", exact=True).click()
        deletion_row = management.locator(".plans-table tbody tr").filter(has_text="KIỂM THỬ XÓA")
        expect(deletion_row).to_be_visible()
        for width in (390, 700, 1024, 1366):
            page.set_viewport_size({"width": width, "height": 900})
            deletion_row.scroll_into_view_if_needed()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            assert deletion_row.evaluate("""row => {
              const section = row.closest('.plans-section'); const style = getComputedStyle(section);
              const width = section.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
              return getComputedStyle(row).display === (width <= 1000 ? 'grid' : 'table-row')
                && [...row.querySelectorAll('button')].every(button => {
                  const box = button.getBoundingClientRect(); return box.left >= 0 && box.right <= innerWidth;
                });
            }"""), width
        page.set_viewport_size({"width": 1440, "height": 1050})
        delete_button = deletion_row.get_by_role("button", name="Xóa", exact=True)
        delete_button.click()
        confirmation = page.get_by_role("dialog", name="Xóa kế hoạch?", exact=True)
        expect(confirmation.get_by_role("button", name="Giữ kế hoạch", exact=True)).to_be_focused()
        expect(confirmation).to_contain_text("Lịch sử và báo cáo đã chốt vẫn được giữ nguyên")
        page.keyboard.press("Escape")
        expect(confirmation).to_have_count(0)
        expect(delete_button).to_be_focused()
        assert not any(call["method"] == "DELETE" for call in state["calls"])
        delete_button.click()
        deletion_plan.update(revision=2, amount=31, amount_decimal="31")
        confirmation.get_by_role("button", name="Xóa kế hoạch", exact=True).click()
        expect(confirmation.get_by_role("alert")).to_contain_text("Kế hoạch đã thay đổi")
        assert not deletion_plan.get("is_deleted")
        confirmation.get_by_role("button", name="Đóng và tải lại danh sách", exact=True).click()
        expect(deletion_row.get_by_role("cell", name="31", exact=True)).to_be_visible()
        deletion_row.get_by_role("button", name="Lịch sử", exact=True).click()
        history = management.get_by_role("region", name="Lịch sử kế hoạch", exact=True)
        expect(history).to_be_visible()
        state["hold_delete"] = True
        delete_button.click()
        before_deletes = len([call for call in state["calls"] if call["method"] == "DELETE"])
        before_progress = len([call for call in state["calls"] if call["path"].endswith("/throughput-progress")])
        before_reports = len([call for call in state["calls"] if call["path"] == "/dashboard"])
        confirmation.get_by_role("button", name="Xóa kế hoạch", exact=True).click()
        busy = confirmation.get_by_role("button", name="Đang xóa…", exact=True)
        expect(busy).to_be_disabled()
        busy.dispatch_event("click")
        page.keyboard.press("Escape")
        expect(confirmation).to_be_visible()
        for _ in range(50):
            if state["pending_delete"]:
                break
            page.wait_for_timeout(10)
        pending_route, pending_plan = state["pending_delete"]
        assert len([call for call in state["calls"] if call["method"] == "DELETE"]) == before_deletes + 1
        pending_plan.update(is_deleted=True, deleted_at="2026-09-13T08:00:00Z", deleted_by=9001, revision=3, is_current=False)
        pending_route.fulfill(json=pending_plan)
        state["hold_delete"] = False
        state["pending_delete"] = None
        expect(confirmation).to_have_count(0)
        expect(deletion_row).to_have_count(0)
        expect(history).to_have_count(0)
        expect(management.get_by_role("heading", name="Danh sách kế hoạch", exact=True)).to_be_focused()
        assert len([call for call in state["calls"] if call["path"] == "/dashboard"]) == before_reports
        expect(management.locator(".throughput-progress, .management-secondary-progress")).to_have_count(0)
        assert len([call for call in state["calls"] if call["path"].endswith("/throughput-progress")]) == before_progress
        management.get_by_role("checkbox", name="Hiện kế hoạch đã xóa", exact=True).check()
        expect(deletion_row).to_contain_text("Đã xóa")
        expect(deletion_row.get_by_role("button")).to_have_count(1)
        deletion_row.get_by_role("button", name="Lịch sử", exact=True).click()
        expect(history).to_contain_text("Xóa kế hoạch")
        history.get_by_role("button", name="Đóng lịch sử", exact=True).click()
        management.get_by_role("checkbox", name="Hiện kế hoạch đã xóa", exact=True).uncheck()
        assert not any(call["path"] == "/dashboard" or call["path"].startswith("/reports/") for call in state["calls"]), "plan CRUD, import, history and deletion must not read production reports"
        checks.append("plans use one responsive table/card tree at 390/700/1024/1366; deletion confirms, handles conflicts, prevents doubles and preserves readable history")
        checks.append("plan CRUD, Excel preview/import, history and deletion work independently of dashboard or report APIs")

        with page.expect_response(lambda response: urlparse(response.url).path.removeprefix("/api") == "/dashboard"):
            management.get_by_role("tab", name="Đối soát", exact=True).click()
        expect(page.locator(".filter-panel")).to_be_visible()
        expect(page.locator(".production-scope-selector")).to_be_visible()
        expect(management.locator(".management-operations")).to_contain_text("KIỂM THỬ-001")
        monthly = management.locator(".management-secondary-progress")
        assert not any(call["path"].endswith("/plan-progress") for call in state["calls"])
        with page.expect_response(lambda response: urlparse(response.url).path.endswith("/plan-progress")):
            monthly.locator("summary").click()
        expect(monthly.get_by_role("heading", name="Thực hiện so với kế hoạch tháng", exact=True)).to_be_visible()
        monthly.locator("summary").click()
        assert len([call for call in state["calls"] if call["path"] == "/dashboard"]) == 1
        checks.append("reconciliation starts the first report read; its monthly comparison loads only when expanded")
        management.get_by_role("button", name="Ghi nhận", exact=True).click()
        management.locator(".management-issue-editor").get_by_role("textbox", name="Nội dung đối soát", exact=True).fill("Đã kiểm tra chứng từ nguồn")
        management.locator(".management-issue-editor").get_by_role("combobox").select_option("resolved")
        management.get_by_role("button", name="Lưu đối soát", exact=True).click()
        expect(management.get_by_text("Đã lưu kết quả đối soát.", exact=True)).to_be_visible()
        assert state["issues"][0]["status"] == "resolved"
        checks.append("reconciliation preserves null source weight and records an independent issue annotation")

        management.get_by_role("tab", name="Báo cáo đã chốt", exact=True).click()
        management.get_by_text("Chốt báo cáo đang xem", exact=True).click()
        management.get_by_label("Tên báo cáo", exact=True).fill("BÁO CÁO KIỂM THỬ")
        management.get_by_role("button", name="Chốt báo cáo hiện tại", exact=True).click()
        expect(management.get_by_role("rowheader").filter(has_text="BÁO CÁO KIỂM THỬ")).to_be_visible()
        management.get_by_role("button", name="Kế hoạch đã chốt", exact=True).click()
        saved_plan = management.get_by_role("region", name="Kế hoạch tại thời điểm chốt", exact=True)
        expect(saved_plan).to_contain_text("VĂN BẢN LƯU KHI CHỐT")
        expect(saved_plan).to_contain_text("80%")
        management.get_by_role("button", name="Đóng kế hoạch đã chốt", exact=True).click()
        management.get_by_role("button", name="So sánh", exact=True).click()
        expect(management.get_by_label("Kết quả so sánh báo cáo")).to_contain_text("Chênh lệch")
        with page.expect_download() as download:
            management.get_by_role("button", name="Excel", exact=True).click()
        assert download.value.suggested_filename == "bao-cao-da-chot-nghe_tinh-1-v1.xlsx"
        checks.append("closing sends only the trusted report ID; closed/current comparison and authenticated download work")

        for scope in ("Cầu 5", "Chưa xác định cầu"):
            page.get_by_role("tab", name=scope, exact=True).click()
            expect(management.get_by_role("button", name="Chốt báo cáo hiện tại", exact=True)).to_be_enabled()
            expect(management.get_by_role("button", name="So sánh", exact=True)).to_be_disabled()
        page.get_by_role("tab", name="Cảng Nghệ Tĩnh", exact=True).click()
        expect(management.get_by_role("button", name="So sánh", exact=True)).to_be_enabled()
        checks.append("closed reports retain their scope; comparison is disabled for Vietsun and unclassified views")

        management = open_workspace(page, "admin")
        expect(management.get_by_role("tab")).to_have_count(2)
        expect(page.get_by_role("region", name="Bộ lọc báo cáo", exact=True)).to_have_count(0)
        management.get_by_role("tab", name="Tài khoản", exact=True).click()
        management.get_by_text("Tạo tài khoản", exact=True).first.click()
        management.get_by_label("Tên đăng nhập", exact=True).fill("new_viewer")
        management.get_by_label("Tên người dùng", exact=True).fill("NGƯỜI DÙNG KIỂM THỬ")
        management.get_by_label("Cửa Lò", exact=True).check()
        management.get_by_role("button", name="Tạo tài khoản", exact=True).click()
        password = management.locator(".management-password input")
        expect(password).to_have_attribute("type", "password")
        management.get_by_role("button", name="Hiện mật khẩu", exact=True).click()
        expect(password).to_have_attribute("type", "text")
        management.get_by_role("button", name="Đã ghi nhận", exact=True).click()
        expect(management.locator(".management-password")).to_have_count(0)
        row = management.get_by_role("row").filter(has_text="new_viewer")
        row.get_by_role("button", name="Sửa quyền", exact=True).click()
        editor = management.get_by_role("region", name="Sửa tài khoản", exact=True)
        editor.get_by_label("Tên hiển thị", exact=True).fill("QUẢN LÝ BẾN THỦY KIỂM THỬ")
        editor.get_by_role("combobox").select_option("manager")
        editor.get_by_role("group", name="Xí nghiệp được cấp", exact=True).get_by_label("Cửa Lò", exact=True).uncheck()
        patches_before = len([call for call in state["calls"] if call["method"] == "PATCH"])
        editor.get_by_role("button", name="Lưu tài khoản", exact=True).click()
        expect(editor.get_by_role("alert")).to_contain_text("Chọn ít nhất một xí nghiệp")
        assert len([call for call in state["calls"] if call["method"] == "PATCH"]) == patches_before
        editor.get_by_role("group", name="Xí nghiệp được cấp", exact=True).get_by_label("Bến Thủy", exact=True).check()
        editor.get_by_role("button", name="Lưu tài khoản", exact=True).click()
        expect(editor).to_have_count(0)
        expect(row.get_by_role("rowheader")).to_contain_text("QUẢN LÝ BẾN THỦY KIỂM THỬ")
        expect(row.get_by_role("cell", name="Quản lý", exact=True)).to_be_visible()
        expect(row.get_by_role("cell", name="Bến Thủy", exact=True)).to_be_visible()
        assert state["users"][-1]["username"] == "new_viewer"
        checks.append("editing an account validates nonempty scope and updates only its name, role and terminals")
        row.get_by_role("button", name="Cấp mật khẩu mới", exact=True).click()
        expect(management.locator(".management-password")).to_be_visible()
        management.get_by_role("tab", name="Vận hành", exact=True).click()
        expect(management.get_by_text("Tải báo cáo", exact=True)).to_be_visible()
        checks.append("admin creates scoped viewers, reveals/resets transient credentials and views operational metrics")

        state["dashboard_unavailable"] = True
        open_reports(page)
        page.get_by_role("button", name="Tải lại báo cáo đang chọn", exact=True).click()
        expect(page.locator(".refresh-error")).to_contain_text("Đang giữ số liệu lần đọc trước.")
        expect(page.locator(".kpi-card")).to_have_count(3)
        management = open_workspace(page, "admin")
        management.get_by_role("tab", name="Tài khoản", exact=True).click()
        expect(management.get_by_role("row").filter(has_text="new_viewer")).to_be_visible()
        management = open_workspace(page, "management")
        management.get_by_role("tab", name="Kế hoạch", exact=True).click()
        expect(management.get_by_text("Đã duyệt", exact=True)).to_be_visible()
        management.get_by_role("tab", name="Báo cáo đã chốt", exact=True).click()
        expect(management.get_by_role("rowheader").filter(has_text="BÁO CÁO KIỂM THỬ")).to_be_visible()
        management.get_by_text("Chốt báo cáo đang xem", exact=True).click()
        expect(management.get_by_role("button", name="Chốt báo cáo hiện tại", exact=True)).to_have_count(0)
        checks.append("refresh 503 retains a marked snapshot; closing is blocked while accounts, plans and saved reports stay accessible")
        state["dashboard_unavailable"] = False
        open_reports(page)
        page.get_by_role("button", name="Thử cập nhật lại", exact=True).click()
        expect(page.locator(".kpi-card")).to_have_count(3)
        management = open_workspace(page, "admin")
        management.get_by_role("tab", name="Vận hành", exact=True).click()

        page.set_viewport_size({"width": 390, "height": 844})
        management.scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        Path("outputs").mkdir(exist_ok=True)
        page.screenshot(path="outputs/browser-management-mobile.png", full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1050})
        management = open_workspace(page, "management")
        management.get_by_role("tab", name="Kế hoạch", exact=True).click()
        expect(management.get_by_text("Đã duyệt", exact=True)).to_be_visible()
        state["expire_plans"] = True
        management.get_by_role("button", name="Tải lại", exact=True).click()
        expect(page.get_by_role("heading", name="Đăng nhập", exact=True)).to_be_visible()
        expect(page.locator(".management")).to_have_count(0)
        assert page.evaluate("sessionStorage.getItem('port-dashboard-session')") is None
        state["expire_plans"] = False
        page.get_by_label("Tài khoản", exact=True).fill("test_viewer")
        page.get_by_label("Mật khẩu", exact=True).fill("synthetic-viewer-password")
        page.get_by_role("button", name="Đăng nhập", exact=True).click()
        management = open_workspace(page, "management")
        expect(management.get_by_role("tab")).to_have_count(3)
        expect(page.locator('.top-nav a[href="#admin"]')).to_have_count(0)
        expect(management.get_by_role("tab", name="Tài khoản", exact=True)).to_have_count(0)
        expect(management.get_by_role("button", name="Xóa", exact=True)).to_have_count(0)
        management.get_by_role("checkbox", name="Hiện kế hoạch đã xóa", exact=True).check()
        deleted_viewer_row = management.locator(".plans-table tbody tr").filter(has_text="KIỂM THỬ XÓA")
        expect(deleted_viewer_row).to_contain_text("Đã xóa")
        expect(deleted_viewer_row.get_by_role("button")).to_have_count(1)
        expect(management.get_by_text("Tạo phiên bản kế hoạch", exact=True)).to_have_count(0)
        expect(management.get_by_role("button", name="Duyệt", exact=True)).to_have_count(0)
        management.get_by_role("tab", name="Đối soát", exact=True).click()
        management.get_by_role("button", name="Xem ghi chú", exact=True).click()
        expect(management.get_by_text("Đã kiểm tra chứng từ nguồn", exact=True)).to_be_visible()
        expect(management.get_by_role("button", name="Lưu đối soát", exact=True)).to_have_count(0)
        checks.append("mobile stays within viewport; expired sessions clear report access; viewers have read-only controls")
        assert errors == [], errors
        assert auth["unexpected"] == [], auth["unexpected"]
        browser.close()

    result = {"status": "passed", "data": "synthetic intercepted browser fixtures only", "checks": checks, "page_errors": errors}
    Path("outputs/browser-management-checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
