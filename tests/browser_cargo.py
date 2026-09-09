"""Focused cargo-card browser checks using explicitly synthetic responses only.

The shared fixture replaces the repository query with in-memory facts; all browser
API requests are intercepted. This test never connects to production SQL.
Run: .venv-audit/Scripts/python.exe tests/browser_cargo.py --url http://127.0.0.1:5175
"""
import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser_smoke import fixture
from playwright.sync_api import expect, sync_playwright


PREFIX = "KIỂM THỬ — "
LONG_NAME = PREFIX + "Nhóm hàng có tên rất dài để kiểm tra xuống dòng " + "MÃHÀNGKHÔNGCÓKHOẢNGTRẮNG" * 5
POSITIVE = [
    {"name": PREFIX + "Hàng 100", "tonnage": 100, "value": None},
    {"name": LONG_NAME, "tonnage": 99500.125, "value": None},
    {"name": PREFIX + "Hàng 20.000", "tonnage": 20000, "value": None},
    {"name": PREFIX + "Hàng 31", "tonnage": 31, "value": None},
    {"name": PREFIX + "Hàng 550", "tonnage": 550, "value": None},
    {"name": PREFIX + "Hàng 0,125", "tonnage": 0.125, "value": None},
    {"name": PREFIX + "Hàng 7.100", "tonnage": 7100, "value": None},
    {"name": PREFIX + "Hàng 50.000", "tonnage": 50000, "value": None},
    {"name": PREFIX + "Hàng 1,5", "tonnage": 1.5, "value": None},
    {"name": PREFIX + "Hàng 1.000", "tonnage": 1000, "value": None},
    {"name": PREFIX + "Hàng 40.000", "tonnage": 40000, "value": None},
    {"name": PREFIX + "Hàng 3.500", "tonnage": 3500, "value": None},
]
NEGATIVE = {"name": PREFIX + "Điều chỉnh giảm", "tonnage": -20, "value": None}
ZERO = {"name": PREFIX + "Giá trị bằng không", "tonnage": 0, "value": None}
UNKNOWN = {"name": PREFIX + "Chưa có đơn vị tấn xác định", "tonnage": None, "value": None}
CASES = {
    "mixed": [*POSITIVE, NEGATIVE, ZERO, UNKNOWN],
    "zero": [ZERO],
    "unknown": [UNKNOWN],
    "negative": [NEGATIVE],
    "empty": [],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5175")
    parser.add_argument("--channel", default="chrome")
    args = parser.parse_args()
    output = Path("outputs")
    output.mkdir(exist_ok=True)
    state = {"case": "mixed"}
    errors, checks, requests = [], [], []

    def respond(route):
        query = parse_qs(urlparse(route.request.url).query)
        filters = {key: query[key][0] for key in ("start_date", "end_date", "terminal")}
        data = fixture(filters, empty=state["case"] == "empty")
        # Override only the card under test; these labelled synthetic rows are
        # deliberately unsorted and cover states absent from the basic fixture.
        data["cargo"] = CASES[state["case"]]
        requests.append(state["case"])
        route.fulfill(json=data)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.channel, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/dashboard?*", respond)
        page.goto(args.url)
        card = page.locator(".cargo-panel")
        ranking = card.locator("#cargo-ranking")
        expect(card).to_be_visible()
        expect(card.locator(".cargo-count")).to_have_text("15 nhóm")
        expected = [row["name"] for row in sorted(POSITIVE, key=lambda row: row["tonnage"], reverse=True)]
        expect(ranking.locator(".cargo-name")).to_have_text(expected[:8])
        expect(card.locator(".cargo-row-negative .cargo-value")).to_have_text("-20 tấn")
        expect(card.locator(".cargo-row-negative .cargo-bar")).to_have_count(0)
        expect(card.locator(".cargo-other-list")).not_to_be_visible()
        expect(card.locator(".cargo-row-leading .cargo-value")).to_have_text("99.500,125 tấn")
        assert "%" not in card.inner_text()
        checks.append("mixed cargo: first 8 sorted positive rows; signed adjustment remains visible; no percentage labels")

        expand = card.locator(".cargo-expand")
        expect(expand).to_have_attribute("aria-expanded", "false")
        expect(expand).to_have_attribute("aria-controls", "cargo-ranking")
        expand.focus()
        expand.press("Enter")
        expect(expand).to_have_attribute("aria-expanded", "true")
        expect(ranking.locator(".cargo-name")).to_have_text(expected)
        expect(ranking.locator(".cargo-value").last).to_have_text("0,125 tấn")
        widths = ranking.locator(".cargo-bar > span").evaluate_all("elements => elements.map(element => parseFloat(element.style.width))")
        assert widths[0] == 100 and all(0 < value <= 100 for value in widths), widths
        assert widths == sorted(widths, reverse=True), widths
        assert all(value == value for value in widths), widths
        assert ranking.locator(".cargo-bar").evaluate_all("elements => elements.every(element => element.getAttribute('aria-hidden') === 'true')")
        expect(expand).to_be_focused()
        expand.press("Space")
        expect(expand).to_have_attribute("aria-expanded", "false")
        expect(ranking.locator(".cargo-row")).to_have_count(8)
        expect(card.locator(".cargo-row-negative")).to_be_visible()
        checks.append("expansion: Enter/Space toggles all 12 positive rows; finite bars scale against largest positive value")

        summary = card.locator(".cargo-other > summary")
        expect(summary).to_contain_text("1 nhóm bằng 0")
        expect(summary).to_contain_text("1 nhóm chưa có số tấn")
        summary.focus()
        summary.press("Enter")
        expect(card.locator(".cargo-other")).to_have_attribute("open", "")
        expect(card.locator(".cargo-other-value")).to_have_text(["0 tấn", "Chưa có số tấn"])
        expect(summary).to_be_focused()
        assert summary.evaluate("element => getComputedStyle(element).outlineStyle") != "none"
        checks.append("zero/unknown: keyboard opens native details; zero remains 0 tấn and NULL is explicitly distinct")

        expand.click()
        for width in [1440, 768, 601, 390, 320]:
            page.set_viewport_size({"width": width, "height": 1100 if width > 800 else 844})
            card.scroll_into_view_if_needed()
            # Responsive charts elsewhere on the dashboard resize asynchronously;
            # the bounded wait verifies settled document and card geometry.
            page.wait_for_function("document.documentElement.scrollWidth <= innerWidth", timeout=3000)
            assert card.evaluate("element => element.scrollWidth <= element.clientWidth + 1"), width
            assert card.locator(".cargo-name").evaluate_all("elements => elements.every(element => element.scrollWidth <= element.clientWidth + 1)"), width
            assert card.locator(".cargo-value").evaluate_all("elements => elements.every(element => element.scrollWidth <= element.clientWidth + 1)"), width
            expect(card.locator(".cargo-row-leading .cargo-name")).to_have_text(LONG_NAME)
            card.screenshot(path=str(output / f"browser-cargo-test-{width}.png"))
        checks.append("responsive: long names, figures and expanded details stay within 1440/768/601/390/320px layouts")

        for mode in ["zero", "unknown", "negative", "empty"]:
            state["case"] = mode
            page.locator('.filter-form button[aria-label="Tải lại báo cáo đang chọn"]').click()
            expect(card.locator(".cargo-count")).to_have_text("0 nhóm" if mode == "empty" else "1 nhóm")
            expect(card.locator(".cargo-expand, .cargo-bar")).to_have_count(0)
            if mode in ("zero", "unknown"):
                expect(card.locator(".empty-panel")).to_have_count(0)
                folded = card.locator(".cargo-other")
                if not folded.evaluate("element => element.open"):
                    folded.locator("summary").focus()
                    folded.locator("summary").press("Space")
                expect(card.locator(".cargo-other-value")).to_have_text("0 tấn" if mode == "zero" else "Chưa có số tấn")
            elif mode == "negative":
                expect(card.locator(".cargo-row-negative .cargo-value")).to_have_text("-20 tấn")
                expect(card.locator(".empty-panel, .cargo-other")).to_have_count(0)
            else:
                expect(card.locator(".empty-panel")).to_contain_text("Không có phát sinh trong kỳ đã chọn.")
                expect(card.locator(".cargo-ranking, .cargo-other")).to_have_count(0)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), mode
        checks.append("edge states: all-zero, all-NULL, all-negative and empty stay distinct without invalid bars")
        assert not errors, errors
        browser.close()

    result = {"status": "passed", "data": "labelled synthetic browser fixtures only; no SQL connections", "checks": checks, "viewports": [1440, 768, 601, 390, 320], "page_errors": errors, "requested_cases": requests}
    (output / "browser-cargo-test-checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
