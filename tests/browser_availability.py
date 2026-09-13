"""Browser recovery/cancellation checks with intercepted synthetic reports only."""
import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser_voyages import report_fixture, detail_fixture
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    state = {"dashboard_mode": "ok", "dashboard_calls": 0, "detail_mode": "ok", "detail_calls": 0}
    errors, checks = [], []

    def respond(route):
        parsed = urlparse(route.request.url)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        filters = {key: query[key] for key in ("start_date", "end_date")}
        kind = "detail" if "/voyages/" in parsed.path else "dashboard"
        state[kind + "_calls"] += 1
        mode = state[kind + "_mode"]
        if mode == "down" or (mode == "transient" and state[kind + "_calls"] == 1):
            route.fulfill(status=503, json={"detail": {"code": "DATABASE_UNAVAILABLE"}})
            return
        if kind == "dashboard":
            route.fulfill(json=report_fixture({**filters, "terminal": query["terminal"]}))
        else:
            terminal = parsed.path.split("/")[-2]
            route.fulfill(json=detail_fixture(filters, terminal, int(query["page"]), int(query["page_size"]), query.get("operation_filter", "all")))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/dashboard?*", respond)
        page.route("**/api/voyages/**", respond)
        page.goto(args.url)
        expect(page.locator(".kpi-card")).to_have_count(3)

        state.update(dashboard_mode="transient", dashboard_calls=0)
        page.get_by_role("button", name="Tải lại báo cáo đang chọn").click()
        expect(page.locator(".kpi-card")).to_have_count(3)
        assert state["dashboard_calls"] == 2, state
        expect(page.locator(".error-state")).to_have_count(0)
        checks.append("dashboard recovers from one 503 with exactly two requests")

        state.update(dashboard_mode="down", dashboard_calls=0)
        page.get_by_role("button", name="Tải lại báo cáo đang chọn").click()
        expect(page.locator(".error-state")).to_be_visible()
        expect(page.locator(".kpi-card")).to_have_count(0)
        assert state["dashboard_calls"] == 2, state
        checks.append("persistent error stops after two requests and shows no stale report")

        state.update(dashboard_mode="ok", dashboard_calls=0)
        page.get_by_role("button", name="Thử lại", exact=True).click()
        expect(page.locator(".kpi-card")).to_have_count(3)
        assert state["dashboard_calls"] == 1, state
        checks.append("manual retry recovers after the automatic retry is exhausted")

        page.get_by_role("button", name="Xem chi tiết CUA LO TEST · TEST-101 · Cửa Lò").click()
        dialog = page.get_by_role("dialog", name="Chi tiết chuyến tàu")
        expect(dialog).to_contain_text("TEST-PHIEU-001")
        state.update(detail_mode="transient", detail_calls=0)
        page.get_by_role("button", name="Trang phiếu sau").click()
        expect(dialog).to_contain_text("TEST-PHIEU-026")
        assert state["detail_calls"] == 2, state
        expect(dialog.get_by_text("TEST-PHIEU-001", exact=True)).to_have_count(0)
        checks.append("voyage pagination retries the same page without retaining old rows")

        state.update(detail_mode="transient", detail_calls=0)
        with page.expect_response(lambda response: "/api/voyages/" in response.url and response.status == 503):
            page.get_by_role("button", name="Trang phiếu trước").click()
        page.keyboard.press("Escape")
        # Observe for longer than the specified retry backoff to detect a leaked retry.
        page.wait_for_timeout(700)
        assert state["detail_calls"] == 1, state
        expect(dialog).not_to_be_visible()
        checks.append("closing a voyage during retry backoff cancels the second request")
        assert errors == [], errors
        browser.close()

    output = Path("outputs")
    output.mkdir(exist_ok=True)
    result = {"status": "passed", "data": "synthetic browser fixtures only", "checks": checks, "page_errors": errors}
    (output / "browser-availability-checks.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
