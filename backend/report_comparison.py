"""Calendar comparison windows; no source access or KPI changes."""
from datetime import date, timedelta

COMPARISON_MODES = frozenset({'previous_period', 'previous_year'})


def comparison_period(start: date, end: date, comparison='previous_period'):
    if not isinstance(comparison, str) or comparison not in COMPARISON_MODES:
        raise ValueError('Kỳ so sánh không hợp lệ.')
    if comparison == 'previous_period':
        length = (end - start).days + 1
        previous_start, previous_end = start - timedelta(days=length), start - timedelta(days=1)
        label = f'{length} ngày liền trước'
    else:
        def previous_year(day):
            # Calendar endpoint policy: Feb 29 maps to Feb 28, never March 1.
            try:
                return day.replace(year=day.year - 1)
            except ValueError:
                return day.replace(year=day.year - 1, day=28)
        previous_start, previous_end = previous_year(start), previous_year(end)
        label = 'Cùng ngày/tháng năm trước'
        if any(day.month == 2 and day.day == 29 for day in (start, end)):
            label += ' · 29/02 quy về 28/02'
    return {'start_date': previous_start, 'end_date': previous_end, 'mode': comparison,
            'label': label, 'day_count': (previous_end - previous_start).days + 1}
