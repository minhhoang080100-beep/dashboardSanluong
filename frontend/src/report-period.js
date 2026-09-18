import { isoWeekValue, todayInVietnam, weekDates } from './dashboard-data.js';

export const REPORT_PERIOD_TYPES = {
  day: 'Ngày',
  week: 'Tuần',
  month: 'Tháng',
  quarter: 'Quý',
  year: 'Năm',
  custom: 'Tùy chọn',
};

const DAY_MS = 86400000;

function calendarDay(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value < '1900-01-01') return null;
  const day = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(day.getTime()) && day.toISOString().slice(0, 10) === value ? day : null;
}

function selectedYear(value, today, week = false) {
  if (!calendarDay(today) || !/^\d{4}$/.test(String(value))) return null;
  const year = Number(value);
  const maximum = week ? Math.max(Number(today.slice(0, 4)), Number(isoWeekValue(today).slice(0, 4))) : Number(today.slice(0, 4));
  return year >= 1900 && year <= maximum ? year : null;
}

function rangeToToday(start_date, end_date, today) {
  if (!calendarDay(start_date) || !calendarDay(end_date) || start_date > today || start_date > end_date) return null;
  return { start_date, end_date: end_date > today ? today : end_date };
}

// All calculations use UTC calendar arithmetic on dates already expressed in
// Vietnam time. The browser's timezone cannot move a period boundary.
export function reportPeriodDates(selection, today = todayInVietnam()) {
  if (!selection || !calendarDay(today) || !Object.hasOwn(REPORT_PERIOD_TYPES, selection.type)) return null;
  if (selection.type === 'day') {
    return calendarDay(selection.day) && selection.day <= today ? { start_date: selection.day, end_date: selection.day } : null;
  }
  if (selection.type === 'custom') {
    const start = calendarDay(selection.start_date);
    const end = calendarDay(selection.end_date);
    if (!start || !end || start > end || selection.end_date > today || (end - start) / DAY_MS + 1 > 366) return null;
    return { start_date: selection.start_date, end_date: selection.end_date };
  }
  const year = selectedYear(selection.year, today, selection.type === 'week');
  if (year === null) return null;
  if (selection.type === 'week') {
    if (typeof selection.week !== 'string' || selection.week.slice(0, 4) !== String(year)) return null;
    return weekDates(selection.week, today);
  }
  if (selection.type === 'year') return rangeToToday(`${year}-01-01`, `${year}-12-31`, today);
  let firstMonth;
  let lastMonth;
  if (selection.type === 'month') {
    if (!/^(0[1-9]|1[0-2])$/.test(String(selection.month))) return null;
    firstMonth = Number(selection.month);
    lastMonth = firstMonth;
  } else {
    if (!/^[1-4]$/.test(String(selection.quarter))) return null;
    firstMonth = (Number(selection.quarter) - 1) * 3 + 1;
    lastMonth = firstMonth + 2;
  }
  const start_date = `${year}-${String(firstMonth).padStart(2, '0')}-01`;
  const end_date = new Date(Date.UTC(year, lastMonth, 0)).toISOString().slice(0, 10);
  return rangeToToday(start_date, end_date, today);
}

export function reportPeriodSelection(filters, today = todayInVietnam(), preferredType = null) {
  const start_date = typeof filters?.start_date === 'string' ? filters.start_date : '';
  const end_date = typeof filters?.end_date === 'string' ? filters.end_date : '';
  const seed = calendarDay(start_date) ? start_date : calendarDay(today) ? today : '1900-01-01';
  const week = isoWeekValue(seed);
  const selection = {
    type: 'custom',
    day: seed,
    week,
    month: seed.slice(5, 7),
    year: seed.slice(0, 4),
    quarter: String(Math.ceil(Number(seed.slice(5, 7)) / 3)),
    start_date,
    end_date,
  };
  if (!reportPeriodDates(selection, today)) return selection;
  const types = [...new Set([preferredType, ...Object.keys(REPORT_PERIOD_TYPES)])];
  for (const type of types) {
    if (!Object.hasOwn(REPORT_PERIOD_TYPES, type)) continue;
    const candidate = { ...selection, type, year: type === 'week' ? week.slice(0, 4) : selection.year };
    const dates = reportPeriodDates(candidate, today);
    if (dates?.start_date === start_date && dates.end_date === end_date) return candidate;
  }
  return selection;
}

export function switchReportPeriodType(selection, type, today = todayInVietnam()) {
  if (!selection || !Object.hasOwn(REPORT_PERIOD_TYPES, type) || selection.type === type) return { ...selection };
  const dates = reportPeriodDates(selection, today);
  const year = calendarDay(`${selection.year}-01-01`) ? String(selection.year) : '';
  let anchor = dates?.start_date;
  if (!anchor) {
    if (selection.type === 'custom') {
      anchor = calendarDay(selection.start_date) ? selection.start_date : calendarDay(selection.end_date) ? selection.end_date : null;
    } else if (selection.type === 'day') {
      anchor = calendarDay(selection.day) ? selection.day : null;
    } else if (selection.type === 'week' && year) {
      // A year change can temporarily clear the week (for example W53 in a
      // 52-week year). Do not reuse the previous week's stale calendar dates.
      const weekMatchesYear = typeof selection.week === 'string' && selection.week.slice(0, 4) === year;
      anchor = (weekMatchesYear && weekDates(selection.week, '9999-12-31')?.start_date) || `${year}-01-04`;
    } else if (year) {
      const month = selection.type === 'month' && /^(0[1-9]|1[0-2])$/.test(String(selection.month))
        ? selection.month
        : selection.type === 'quarter' && /^[1-4]$/.test(String(selection.quarter))
          ? String((Number(selection.quarter) - 1) * 3 + 1).padStart(2, '0') : '01';
      anchor = `${year}-${month}-01`;
    }
  }
  anchor ||= calendarDay(selection.day) ? selection.day : year ? `${year}-01-01` : today;
  const next = reportPeriodSelection(dates || { start_date: anchor, end_date: anchor }, today);
  next.type = type;
  next.year = type === 'week' ? next.week.slice(0, 4) : next.day.slice(0, 4);
  // A partially entered day stays visible when opening the custom date fields.
  // It must remain invalid rather than silently becoming today's report.
  if (selection.type === 'day' && type === 'custom') {
    next.start_date = selection.day || '';
    next.end_date = selection.day || '';
  }
  return next;
}

export function reportWeekOptions(yearValue, today = todayInVietnam()) {
  const year = selectedYear(yearValue, today, true);
  if (year === null) return [];
  const january4 = new Date(Date.UTC(year, 0, 4));
  const firstMonday = new Date(january4);
  firstMonday.setUTCDate(firstMonday.getUTCDate() - (firstMonday.getUTCDay() + 6) % 7);
  const options = [];
  for (let week = 1; week <= 53; week += 1) {
    const monday = new Date(firstMonday);
    monday.setUTCDate(monday.getUTCDate() + (week - 1) * 7);
    const start_date = monday.toISOString().slice(0, 10);
    const value = `${year}-W${String(week).padStart(2, '0')}`;
    if (isoWeekValue(start_date) !== value) break;
    const sunday = new Date(monday);
    sunday.setUTCDate(sunday.getUTCDate() + 6);
    const end_date = sunday.toISOString().slice(0, 10);
    if (!calendarDay(end_date)) break;
    const shortDate = (date) => `${date.slice(8, 10)}/${date.slice(5, 7)}`;
    options.push({ value, label: `Tuần ${week} · ${shortDate(start_date)} – ${shortDate(end_date)}`, start_date, end_date, disabled: start_date > today });
  }
  return options;
}
