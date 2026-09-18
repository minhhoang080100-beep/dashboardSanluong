"""Top navigation, retained report context and role boundaries with intercepted APIs only."""
import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser_auth_support import install_auth_fixture
from browser_smoke import fixture
from playwright.sync_api import expect, sync_playwright

PLAN_PERIOD_QUERY_KEYS = {"period_type", "week", "month", "quarter", "year", "start_date", "end_date", "voyage_id"}


def install_navigation_fixture(page, role="admin"):
    state = {"requests": [], "reports": [], "plan_queries": []}
    auth = install_auth_fixture(page, role=role)

    def respond(route):
        path = urlparse(route.request.url).path.removeprefix("/api")
        state["requests"].append(path)
        if path == "/dashboard":
            query = parse_qs(urlparse(route.request.url).query)
            filters = {key: query[key][0] for key in ("start_date", "end_date", "terminal", "production_scope")}
            data = fixture(filters)
            data["meta"]["report_id"] = f"synthetic-navigation-{len(state['reports']) + 1}"
            data["meta"]["source_read_at"] = data["meta"]["generated_at"]
            state["reports"].append({"filters": filters, "report_id": data["meta"]["report_id"]})
            route.fulfill(json=data)
        elif path.endswith("/throughput-progress"):
            report = next(item for item in state["reports"] if item["report_id"] == path.split("/")[2])
            filters = report["filters"]
            route.fulfill(json={"report_id": report["report_id"], "period": filters,
                                "production_scope": filters["production_scope"], "berth_rule_version": "initial-berth-v1",
                                "eligible": filters["production_scope"] == "nghe_tinh", "items": [], "available_periods": [],
                                "reason": "Chưa có kế hoạch được duyệt khớp kỳ và phạm vi báo cáo."})
        elif path.endswith("/plan-progress"):
            route.fulfill(json={"eligible": True, "reason": None, "rows": []})
        elif path.endswith("/operations"):
            route.fulfill(json={"report_id": path.split("/")[2], "operations": {
                "rows": [], "total": 0, "total_all": 0, "page": 1, "page_size": 25, "total_pages": 0}})
        elif path == "/plans" and route.request.method == "GET":
            state["plan_queries"].append(parse_qs(urlparse(route.request.url).query))
            route.fulfill(json={"items": [], "total": 0, "page": 1, "page_size": 25})
        elif path == "/admin/metrics":
            route.fulfill(json={"operations": {}, "cache": {}})
        else:
            route.fallback()

    page.route("**/api/**", respond)
    return state, auth


def active_view(page, view):
    nav = page.get_by_role("navigation", name="Điều hướng chính", exact=True)
    expect(nav.locator(f'a[href="#{view}"]')).to_have_attribute("aria-current", "page")
    expect(nav.locator('[aria-current="page"]')).to_have_count(1)


def go_view(page, view):
    page.locator(f'.top-nav a[href="#{view}"]').click()
    active_view(page, view)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    checks, errors = [], []
    output = Path("outputs")
    output.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.set_default_timeout(10000)
        state, auth = install_navigation_fixture(page)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{args.url.rstrip('/')}#admin")
        active_view(page, "admin")
        expect(page.locator(".sidebar")).to_have_count(0)
        expect(page.locator(".top-nav a")).to_have_count(3)
        expect(page.locator("#admin-content")).to_be_visible()
        expect(page.get_by_role("heading", name="Tài khoản nội bộ", exact=True)).to_be_visible()
        expect(page.get_by_text("Chưa có tài khoản.", exact=True)).to_be_visible()
        expect(page.locator(".filter-panel")).to_be_hidden()
        assert state["reports"] == []
        assert not any(path.startswith('/reports/') for path in state["requests"])
        page.screenshot(path=str(output / "browser-navigation-admin.png"), full_page=True)
        checks.append("direct admin entry shows only admin tools and never requests production report data")

        go_view(page, "overview")
        expect(page.locator(".kpi-card")).to_have_count(3)
        expect(page.locator(".management")).to_be_hidden()
        page.get_by_label("Từ ngày", exact=True).fill("2026-08-01")
        page.get_by_label("Đến ngày", exact=True).fill("2026-08-31")
        page.get_by_label("Phạm vi xí nghiệp").select_option("ben_thuy")
        page.get_by_role("button", name="Áp dụng", exact=True).click()
        expect(page.locator(".kpi-value").first).to_contain_text("2.000")
        expect(page.locator(".throughput-progress")).to_contain_text("Chưa có kế hoạch được duyệt")
        selected = dict(state["reports"][-1])
        report_count = len(state["reports"])
        progress_path = f"/reports/{selected['report_id']}/throughput-progress"
        progress_reads = state["requests"].count(progress_path)
        page.screenshot(path=str(output / "browser-navigation-reports.png"), full_page=True)
        go_view(page, "management")
        expect(page.locator("#management-content")).to_be_visible()
        expect(page.locator("#management-content").get_by_role("tab")).to_have_count(3)
        expect(page.locator(".kpi-card").first).to_be_hidden()
        expect(page.get_by_role("heading", name="Kế hoạch", level=1, exact=True)).to_be_visible()
        expect(page.locator("#management-content")).to_have_attribute("aria-label", "Kế hoạch")
        expect(page.locator(".filter-panel, .report-toolbar, .production-scope-selector")).to_have_count(0)
        expect(page.locator(".throughput-progress, .management-secondary-progress")).to_have_count(0)
        expect(page.get_by_text("Chưa có kế hoạch trong kỳ và phạm vi đã chọn.", exact=True)).to_be_visible()
        assert state["requests"].count(progress_path) == progress_reads
        monthly_path = f"/reports/{selected['report_id']}/plan-progress"
        assert monthly_path not in state["requests"]

        page.locator("#management-content").get_by_role("tab", name="Đối soát", exact=True).click()
        expect(page.locator(".filter-panel")).to_be_visible()
        expect(page.locator(".production-scope-selector")).to_be_visible()
        expect(page.get_by_label("Từ ngày", exact=True)).to_have_value("2026-08-01")
        expect(page.get_by_label("Đến ngày", exact=True)).to_have_value("2026-08-31")
        expect(page.get_by_label("Phạm vi xí nghiệp")).to_have_value("ben_thuy")
        assert monthly_path not in state["requests"], "secondary monthly comparison should load only when opened"
        monthly = page.locator("#management-content .management-secondary-progress")
        with page.expect_response(lambda response: urlparse(response.url).path.removeprefix("/api") == monthly_path):
            monthly.locator("summary").click()
        expect(monthly.get_by_role("heading", name="Thực hiện so với kế hoạch tháng", exact=True)).to_be_visible()
        assert monthly_path in state["requests"]
        monthly.locator("summary").click()
        assert len(state["reports"]) == report_count
        page.locator("#management-content").get_by_role("tab", name="Kế hoạch", exact=True).click()
        expect(page.locator(".filter-panel, .report-toolbar, .production-scope-selector")).to_have_count(0)
        expect(page.locator(".throughput-progress, .management-secondary-progress")).to_have_count(0)
        page.screenshot(path=str(output / "browser-navigation-management.png"), full_page=True)
        go_view(page, "admin")
        expect(page.locator("#admin-content").get_by_role("tab")).to_have_count(2)
        expect(page.locator("#management-content")).to_be_hidden()
        expect(page.locator(".filter-panel")).to_be_hidden()
        assert len(state["reports"]) == report_count
        checks.append("three separate views retain the exact selected filters and report snapshot without rereading SQL")

        page.go_back()
        active_view(page, "management")
        expect(page.locator("#management-content")).to_be_visible()
        page.go_back()
        active_view(page, "overview")
        expect(page.locator(".kpi-value").first).to_contain_text("2.000")
        expect(page.get_by_label("Phạm vi xí nghiệp")).to_have_value("ben_thuy")
        page.go_forward()
        active_view(page, "management")
        assert len(state["reports"]) == report_count
        checks.append("browser Back and Forward restore the selected view without resetting report context")

        page.evaluate("window.location.hash = 'voyages'")
        active_view(page, "overview")
        expect(page.locator("#voyages")).to_be_in_viewport()
        assert page.evaluate("window.scrollY > 100")
        assert len(state["reports"]) == report_count
        page.evaluate("window.location.hash = 'unknown-navigation-test'")
        active_view(page, "overview")
        expect(page.get_by_role("heading", name="Báo cáo sản lượng", exact=True)).to_be_visible()
        checks.append("legacy voyage anchors select and scroll the report view; unknown anchors safely select reports")

        for width in (1440, 1024, 850, 390, 320):
            page.set_viewport_size({"width": width, "height": 844})
            for view in ("overview", "management", "admin"):
                go_view(page, view)
                expect(page.locator(".top-nav")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), {"view": view, "width": width}
            page.screenshot(path=str(output / f"browser-navigation-mobile-{width}.png"), full_page=True)
        checks.append("top navigation and all views fit 1440, 1024, 850, 390 and 320px without horizontal page overflow")
        assert auth["unexpected"] == [], auth["unexpected"]
        page.close()

        # Starting in Plans must not need any source report. Its own list
        # filters still work before the first report-backed tab is opened.
        direct = browser.new_page(viewport={"width": 1440, "height": 1050})
        direct.set_default_timeout(10000)
        direct_state, direct_auth = install_navigation_fixture(direct)
        direct.on("pageerror", lambda error: errors.append(str(error)))
        direct.goto(f"{args.url.rstrip('/')}#management")
        active_view(direct, "management")
        workspace = direct.get_by_role("region", name="Kế hoạch", exact=True)
        expect(workspace.get_by_role("tab", name="Kế hoạch", exact=True)).to_have_attribute("aria-selected", "true")
        expect(workspace.get_by_role("heading", name="Danh sách kế hoạch", exact=True)).to_be_visible()
        expect(workspace.locator("details.management-editor").first).to_be_hidden()
        expect(workspace.get_by_text("Chưa có kế hoạch trong kỳ và phạm vi đã chọn.", exact=True)).to_be_visible()
        expect(workspace.get_by_role("combobox", name="Danh sách kế hoạch", exact=True)).to_have_value("all")
        expect(workspace.get_by_role("combobox", name="Xí nghiệp", exact=True)).to_have_value("all")
        initial_plan_query = direct_state["plan_queries"][-1]
        assert PLAN_PERIOD_QUERY_KEYS.isdisjoint(initial_plan_query)
        assert initial_plan_query["terminal"] == ["all"] and initial_plan_query["page_size"] == ["25"]
        expect(direct.locator(".filter-panel, .report-toolbar, .production-scope-selector")).to_have_count(0)
        expect(direct.locator(".throughput-progress, .management-secondary-progress")).to_have_count(0)
        workspace.get_by_role("combobox", name="Danh sách kế hoạch", exact=True).select_option("year")
        with direct.expect_response(lambda response: urlparse(response.url).path.removeprefix("/api") == "/plans"
                                    and parse_qs(urlparse(response.url).query).get("year") == ["2025"]):
            workspace.get_by_label("Năm kế hoạch", exact=True).fill("2025")
        with direct.expect_response(lambda response: urlparse(response.url).path.removeprefix("/api") == "/plans"
                                    and parse_qs(urlparse(response.url).query).get("terminal") == ["ben_thuy"]):
            workspace.get_by_role("combobox", name="Xí nghiệp", exact=True).select_option("ben_thuy")
        assert direct_state["plan_queries"][-1]["period_type"] == ["year"]
        assert direct_state["plan_queries"][-1]["year"] == ["2025"]
        with direct.expect_response(lambda response: urlparse(response.url).path.removeprefix("/api") == "/plans"
                                    and PLAN_PERIOD_QUERY_KEYS.isdisjoint(parse_qs(urlparse(response.url).query))):
            workspace.get_by_role("combobox", name="Danh sách kế hoạch", exact=True).select_option("all")
        assert PLAN_PERIOD_QUERY_KEYS.isdisjoint(direct_state["plan_queries"][-1])
        assert direct_state["plan_queries"][-1]["terminal"] == ["ben_thuy"]
        expect(workspace.get_by_label("Năm kế hoạch", exact=True)).to_have_count(0)
        assert direct_state["reports"] == []
        assert not any(path.startswith("/reports/") for path in direct_state["requests"])
        checks.append("direct plans defaults to all periods and permitted terminals; filtered year selection and returning to all clear date parameters without reading a report")

        with direct.expect_response(lambda response: urlparse(response.url).path.removeprefix("/api") == "/dashboard"):
            workspace.get_by_role("tab", name="Báo cáo đã chốt", exact=True).click()
        expect(direct.locator(".filter-panel")).to_be_visible()
        expect(direct.locator(".production-scope-selector")).to_be_visible()
        expect(workspace.get_by_text("Chưa có báo cáo đã chốt trong phạm vi được cấp.", exact=True)).to_be_visible()
        assert len(direct_state["reports"]) == 1
        # The independent plan-list selection must not replace report filters.
        assert direct_state["reports"][0]["filters"]["terminal"] == "all"
        first_report = direct_state["reports"][0]["report_id"]
        with direct.expect_response(lambda response: urlparse(response.url).path.removeprefix("/api") == f"/reports/{first_report}/operations"):
            workspace.get_by_role("tab", name="Đối soát", exact=True).click()
        expect(workspace.locator(".management-secondary-progress")).to_be_visible()
        assert len(direct_state["reports"]) == 1
        assert not any(path.endswith("/plan-progress") for path in direct_state["requests"])
        workspace.get_by_role("tab", name="Kế hoạch", exact=True).click()
        expect(direct.locator(".filter-panel, .report-toolbar, .production-scope-selector")).to_have_count(0)
        expect(direct.locator(".throughput-progress, .management-secondary-progress")).to_have_count(0)
        assert len(direct_state["reports"]) == 1
        assert direct_auth["unexpected"] == [], direct_auth["unexpected"]
        direct.close()
        checks.append("opening closed reports starts one lazy report read; reconciliation reuses that snapshot and plans hides report controls")

        for role in ("viewer", "manager"):
            scoped_page = browser.new_page(viewport={"width": 1280, "height": 900})
            scoped_state, scoped_auth = install_navigation_fixture(scoped_page, role)
            scoped_page.on("pageerror", lambda error: errors.append(str(error)))
            scoped_page.goto(f"{args.url.rstrip('/')}#admin")
            active_view(scoped_page, "overview")
            expect(scoped_page.locator(".kpi-card")).to_have_count(3)
            expect(scoped_page.locator(".top-nav a")).to_have_count(2)
            expect(scoped_page.locator('.top-nav a[href="#admin"]')).to_have_count(0)
            expect(scoped_page.locator("#admin-content")).to_have_count(0)
            assert not any(path.startswith(('/users', '/admin/')) for path in scoped_state["requests"])
            go_view(scoped_page, "management")
            expect(scoped_page.locator("#management-content").get_by_role("tab")).to_have_count(3)
            assert scoped_auth["unexpected"] == [], scoped_auth["unexpected"]
            scoped_page.close()
        checks.append("viewers and managers cannot enter admin or trigger its APIs, including direct admin URLs")
        assert errors == [], errors
        browser.close()
    result = {"status": "passed", "data": "synthetic intercepted browser fixtures only", "checks": checks, "page_errors": errors}
    (output / "browser-navigation-checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
