"""Optional refresh, retained snapshots and cancelled downloads; intercepted fixtures only."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser_auth_support import install_auth_fixture
from browser_voyages import detail_fixture, report_fixture
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    checks, errors = [], []
    state = {"reports": [], "fail": False, "filters": None}

    def respond(route):
        parsed = urlparse(route.request.url)
        path = parsed.path.removeprefix("/api")
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        if path == "/dashboard":
            state["filters"] = {key: query[key] for key in ("start_date", "end_date", "terminal")}
            state["reports"].append(query)
            if state["fail"]:
                route.fulfill(status=503, json={"detail": "Synthetic SQL outage"})
            else:
                data = report_fixture(state["filters"])
                data["meta"].update(report_id=f"refresh-test-{len(state['reports'])}", source_read_at="2026-09-13T05:00:00Z")
                data["overview"]["tonnage_status"] = "partial"
                route.fulfill(json=data)
        elif path.startswith("/voyages/") and not path.endswith("/progress"):
            terminal = path.split("/")[2]
            detail = detail_fixture(state["filters"], terminal, int(query.get("page", 1)), int(query.get("page_size", 25)), query.get("operation_filter", "with_values"))
            detail["meta"]["report_id"] = query.get("report_id")
            route.fulfill(json=detail)
        elif path.endswith("/progress"):
            route.fulfill(json={"header": {}, "summary": {}, "planning": [], "daily": [], "shifts": [], "meta": {}})
        elif path.endswith("/plan-progress"):
            route.fulfill(json={"eligible": True, "rows": []})
        else:
            route.fallback()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.clock.install(time=datetime(2026, 9, 13, 5, 0, tzinfo=timezone.utc))
        auth = install_auth_fixture(page, role="admin")
        page.route("**/api/**", respond)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url)
        expect(page.locator(".kpi-card")).to_have_count(3)
        expect(page.get_by_role("link", name="Số liệu chưa đầy đủ", exact=True)).to_be_visible()
        assert page.locator("#data-quality").evaluate("element => !element.open")
        page.get_by_role("link", name="Số liệu chưa đầy đủ", exact=True).click()
        expect(page.locator("#data-quality .quality-coverage").first).to_be_visible()
        assert page.locator("#data-quality").evaluate("element => element.open")
        page.locator("#data-quality > summary").click()
        page.get_by_role("link", name="Số liệu chưa đầy đủ", exact=True).click()
        assert page.locator("#data-quality").evaluate("element => element.open")
        toggle = page.get_by_role("checkbox", name="Tự cập nhật mỗi 2 phút", exact=True)
        expect(toggle).not_to_be_checked()
        before = len(state["reports"])
        page.clock.fast_forward(120100)
        assert len(state["reports"]) == before
        toggle.check()
        page.get_by_role("heading", name="Báo cáo sản lượng", exact=True).click()
        page.clock.fast_forward(120100)
        expect(page.locator(".refresh-progress")).to_have_count(0)
        page.wait_for_function("document.querySelector('.report-context').textContent.includes('Đọc nguồn')")
        assert len(state["reports"]) == before + 1
        assert state["reports"][-1]["refresh"] == "true"
        checks.append("refresh defaults off, then reloads only on the selected two-minute interval")

        before = len(state["reports"])
        search = page.get_by_label("Tìm tàu hoặc mã chuyến", exact=True)
        search.fill("CUA LO")
        page.clock.fast_forward(120100)
        assert len(state["reports"]) == before
        search.fill("")
        page.get_by_role("heading", name="Báo cáo sản lượng", exact=True).click()
        page.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })")
        page.clock.fast_forward(120100)
        assert len(state["reports"]) == before
        page.evaluate("delete document.hidden")
        page.get_by_label("Từ ngày", exact=True).fill("2026-09-02")
        page.get_by_role("heading", name="Báo cáo sản lượng", exact=True).click()
        page.clock.fast_forward(120100)
        assert len(state["reports"]) == before
        page.get_by_role("button", name="Tháng này", exact=True).click()
        expect(page.locator(".refresh-progress")).to_have_count(0)
        page.locator('.top-nav a[href="#management"]').click()
        expect(page.locator("#management-content")).to_be_visible()
        before = len(state["reports"])
        page.clock.fast_forward(120100)
        assert len(state["reports"]) == before
        checks.append("typing, unapplied filters, hidden documents and management pages pause automatic reads")

        page.locator('.top-nav a[href="#overview"]').click()
        page.get_by_role("button", name="Tháng trước", exact=True).click()
        expect(page.locator(".kpi-card")).to_have_count(3)
        expect(page.locator(".report-refresh-control")).to_contain_text("Chỉ cập nhật tự động")
        before = len(state["reports"])
        page.clock.fast_forward(240100)
        assert len(state["reports"]) == before
        assert page.evaluate("localStorage.getItem('port-report-auto-refresh-9001')") == "true"
        checks.append("historical reports never trigger automatic SQL reads and account preference is saved")

        state["fail"] = True
        old_values = page.locator(".kpi-value").all_text_contents()
        page.get_by_role("button", name="Tải lại báo cáo đang chọn", exact=True).click()
        page.clock.run_for(1000)
        expect(page.locator(".refresh-error")).to_contain_text("Đang giữ số liệu lần đọc trước")
        assert old_values == page.locator(".kpi-value").all_text_contents()
        state["fail"] = False
        page.get_by_role("button", name="Thử cập nhật lại", exact=True).click()
        expect(page.locator(".refresh-error")).to_have_count(0)
        expect(page.locator(".refresh-progress")).to_have_count(0)
        checks.append("failed refresh retains matching snapshot with visible error and recovers manually")

        page.get_by_role("button", name="Tháng này", exact=True).click()
        expect(page.locator(".kpi-card")).to_have_count(3)
        expect(page.locator(".refresh-progress")).to_have_count(0)
        page.get_by_role("button", name="Xem chi tiết CUA LO TEST · TEST-101 · Cửa Lò", exact=True).click()
        modal = page.get_by_role("dialog", name="Chi tiết chuyến tàu", exact=True)
        export = modal.get_by_role("button", name="Xuất Excel chuyến tàu", exact=True)
        expect(export).to_be_enabled()
        # A successful header with a stalled body reproduces the real failure mode.
        page.evaluate("""() => {
          const realFetch = window.fetch;
          window.downloadCalls = 0; window.downloadAborted = false; window.downloadClicks = 0;
          const realClick = HTMLAnchorElement.prototype.click;
          HTMLAnchorElement.prototype.click = function () { if (this.download) window.downloadClicks++; return realClick.call(this); };
          window.fetch = (url, options) => {
            if (!String(url).includes('/export.xlsx')) return realFetch(url, options);
            window.downloadCalls++;
            options.signal.addEventListener('abort', () => { window.downloadAborted = true; });
            return Promise.resolve({ ok: true, status: 200, blob: () => new Promise(() => {}) });
          };
        }""")
        export.click()
        busy = modal.get_by_role("button", name="Đang xuất Excel…", exact=True)
        expect(busy).to_be_disabled()
        busy.dispatch_event("click")
        assert page.evaluate("window.downloadCalls") == 1
        modal.get_by_role("button", name="Đóng chi tiết chuyến tàu", exact=True).click()
        expect(modal).to_have_count(0)
        assert page.evaluate("window.downloadAborted && window.downloadClicks === 0")
        checks.append("stalled export disables double clicks and closing the dialog aborts body without downloading")

        page.get_by_role("button", name="Xem chi tiết CUA LO TEST · TEST-101 · Cửa Lò", exact=True).click()
        expect(modal.get_by_role("button", name="Xuất Excel chuyến tàu", exact=True)).to_be_enabled()
        expect(modal.locator(".voyage-native table").first).to_be_visible()
        before = len(state["reports"])
        page.clock.fast_forward(120100)
        assert len(state["reports"]) == before
        modal.get_by_role("button", name="Xuất Excel chuyến tàu", exact=True).click()
        page.clock.fast_forward(45100)
        expect(modal.locator(".voyage-dialog-body > .form-error")).to_contain_text("45 giây")
        expect(modal.get_by_role("button", name="Xuất Excel chuyến tàu", exact=True)).to_be_enabled()
        assert page.evaluate("window.downloadClicks") == 0
        checks.append("open modal pauses refresh and a stalled body times out with a usable retry button")
        assert not errors, errors
        assert not auth["unexpected"], auth["unexpected"]
        Path("outputs").mkdir(exist_ok=True)
        Path("outputs/browser-refresh-download-results.json").write_text(json.dumps({"checks": checks, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf8")
        browser.close()
    print(json.dumps({"status": "passed", "checks": len(checks), "errors": errors}))


if __name__ == "__main__":
    main()
