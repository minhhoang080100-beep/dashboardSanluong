"""Shared interactions for report-period drafts in intercepted browser suites."""


def period_controls(page):
    return page.locator(".filter-panel")


def choose_period(page, kind):
    period_controls(page).get_by_label("Loại kỳ", exact=True).select_option(kind)


def custom_period(page, start, end):
    choose_period(page, "custom")
    period_controls(page).get_by_label("Từ ngày", exact=True).fill(start)
    period_controls(page).get_by_label("Đến ngày", exact=True).fill(end)


def apply_report(page):
    period_controls(page).get_by_role("button", name="Xem báo cáo", exact=True).click()


def report_shortcut(page, kind, label):
    choose_period(page, kind)
    period_controls(page).get_by_role("button", name=label, exact=True).click()
    apply_report(page)


def quarter_period(page, year, quarter):
    choose_period(page, "quarter")
    period_controls(page).get_by_label("Năm", exact=True).select_option(str(year))
    period_controls(page).get_by_label("Quý", exact=True).select_option(str(quarter))
    apply_report(page)
