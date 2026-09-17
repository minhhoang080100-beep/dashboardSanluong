"""Top navigation, retained report context and role boundaries with intercepted APIs only."""
import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser_auth_support import install_auth_fixture
from browser_smoke import fixture
from playwright.sync_api import expect, sync_playwright


def install_navigation_fixture(page, role="admin"):
    state = {"requests": [], "reports": []}
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
        elif path.endswith("/plan-progress"):
            route.fulfill(json={"eligible": True, "reason": None, "rows": []})
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
        selected = dict(state["reports"][-1])
        report_count = len(state["reports"])
        page.screenshot(path=str(output / "browser-navigation-reports.png"), full_page=True)
        go_view(page, "management")
        expect(page.locator("#management-content")).to_be_visible()
        expect(page.locator("#management-content").get_by_role("tab")).to_have_count(3)
        expect(page.locator(".kpi-card").first).to_be_hidden()
        expect(page.get_by_label("Từ ngày", exact=True)).to_have_value("2026-08-01")
        expect(page.get_by_label("Đến ngày", exact=True)).to_have_value("2026-08-31")
        expect(page.get_by_label("Phạm vi xí nghiệp")).to_have_value("ben_thuy")
        expect(page.get_by_text("Chưa có kế hoạch trong kỳ và phạm vi đã chọn.", exact=True)).to_be_visible()
        assert f"/reports/{selected['report_id']}/plan-progress" in state["requests"]
        assert len(state["reports"]) == report_count
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
