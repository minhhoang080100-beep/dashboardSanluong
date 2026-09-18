import assert from 'node:assert/strict';
import test from 'node:test';
import { REPORT_PERIOD_TYPES, reportPeriodDates, reportPeriodSelection, reportWeekOptions, switchReportPeriodType } from './report-period.js';

const today = '2026-09-18';
const dates = (start_date, end_date) => ({ start_date, end_date });

test('named report periods keep past periods complete and clamp the active period', () => {
  assert.deepEqual(Object.keys(REPORT_PERIOD_TYPES), ['day', 'week', 'month', 'quarter', 'year', 'custom']);
  for (const [selection, expected] of [
    [{ type: 'day', day: '2026-09-16' }, dates('2026-09-16', '2026-09-16')],
    [{ type: 'week', year: '2026', week: '2026-W37' }, dates('2026-09-07', '2026-09-13')],
    [{ type: 'week', year: '2026', week: '2026-W38' }, dates('2026-09-14', today)],
    [{ type: 'month', year: '2026', month: '08' }, dates('2026-08-01', '2026-08-31')],
    [{ type: 'month', year: '2026', month: '09' }, dates('2026-09-01', today)],
    [{ type: 'quarter', year: '2026', quarter: '2' }, dates('2026-04-01', '2026-06-30')],
    [{ type: 'quarter', year: '2026', quarter: '3' }, dates('2026-07-01', today)],
    [{ type: 'year', year: '2025' }, dates('2025-01-01', '2025-12-31')],
    [{ type: 'year', year: '2026' }, dates('2026-01-01', today)],
  ]) assert.deepEqual(reportPeriodDates(selection, today), expected);
});

test('future periods, malformed fields and impossible calendar days never roll over silently', () => {
  for (const selection of [
    { type: 'day', day: '2026-09-19' },
    { type: 'day', day: '2026-02-29' },
    { type: 'day', day: '2026-04-31' },
    { type: 'day', day: '2026-2-01' },
    { type: 'day', day: '1899-12-31' },
    { type: 'day', day: '2026-09-18T00:00:00Z' },
    { type: 'week', year: '2026', week: '2026-W39' },
    { type: 'week', year: '2026', week: '2025-W38' },
    { type: 'week', year: '2025', week: '2025-W53' },
    { type: 'month', year: '2026', month: '10' },
    { type: 'month', year: '2026', month: '1' },
    { type: 'month', year: '2026', month: '00' },
    { type: 'month', year: '2026', month: '13' },
    { type: 'quarter', year: '2026', quarter: '4' },
    { type: 'quarter', year: '2026', quarter: '0' },
    { type: 'quarter', year: '2026', quarter: '5' },
    { type: 'year', year: '2027' },
    { type: 'year', year: '1899' },
    { type: 'year', year: '02026' },
    { type: 'year', year: '2e3' },
    { type: 'toString' },
    null,
  ]) assert.equal(reportPeriodDates(selection, today), null, JSON.stringify(selection));
  assert.equal(reportPeriodDates({ type: 'day', day: today }, '2026-02-30'), null);
});

test('leap years and the supported 1900 calendar boundary are exact', () => {
  assert.deepEqual(reportPeriodDates({ type: 'month', year: '2024', month: '02' }, today), dates('2024-02-01', '2024-02-29'));
  assert.deepEqual(reportPeriodDates({ type: 'month', year: '1900', month: '02' }, today), dates('1900-02-01', '1900-02-28'));
  assert.deepEqual(reportPeriodDates({ type: 'quarter', year: '1900', quarter: '1' }, today), dates('1900-01-01', '1900-03-31'));
  assert.deepEqual(reportPeriodDates({ type: 'week', year: '1900', week: '1900-W01' }, '1900-01-01'), dates('1900-01-01', '1900-01-01'));
});

test('custom periods preserve exact dates and enforce the existing 366-day report limit', () => {
  const selection = { type: 'custom', ...dates('2026-08-17', '2026-09-03') };
  assert.deepEqual(reportPeriodDates(selection, today), dates('2026-08-17', '2026-09-03'));
  assert.deepEqual(reportPeriodDates({ type: 'custom', ...dates('2024-01-01', '2024-12-31') }, today), dates('2024-01-01', '2024-12-31'));
  for (const invalid of [dates('2024-01-01', '2025-01-01'), dates('2026-09-19', today), dates(today, '2026-09-19'), dates('2026-02-30', today), dates('', today)]) {
    assert.equal(reportPeriodDates({ type: 'custom', ...invalid }, today), null);
  }
});

test('period inference recognizes complete periods and current-period-to-date ranges only', () => {
  for (const [range, expectedType] of [
    [dates(today, today), 'day'],
    [dates('2026-09-07', '2026-09-13'), 'week'],
    [dates('2026-09-14', today), 'week'],
    [dates('2026-08-01', '2026-08-31'), 'month'],
    [dates('2026-09-01', today), 'month'],
    [dates('2026-04-01', '2026-06-30'), 'quarter'],
    [dates('2026-07-01', today), 'quarter'],
    [dates('2025-01-01', '2025-12-31'), 'year'],
    [dates('2026-01-01', today), 'year'],
    [dates('2026-09-14', '2026-09-17'), 'custom'],
    [dates('2026-08-01', '2026-08-20'), 'custom'],
    [dates('2026-07-01', '2026-08-31'), 'custom'],
    [dates('2026-01-01', '2026-06-15'), 'custom'],
    [dates('2026-09-15', today), 'custom'],
  ]) {
    const inferred = reportPeriodSelection(range, today);
    assert.equal(inferred.type, expectedType, JSON.stringify(range));
    assert.deepEqual(reportPeriodDates(inferred, today), range);
  }
});

test('preferred types resolve ambiguous dates without reclassifying nonmatching custom periods', () => {
  const january = dates('2026-01-01', '2026-01-18');
  assert.equal(reportPeriodSelection(january, january.end_date).type, 'month');
  for (const preferred of ['month', 'quarter', 'year', 'custom']) {
    assert.equal(reportPeriodSelection(january, january.end_date, preferred).type, preferred);
  }
  assert.equal(reportPeriodSelection(january, january.end_date, 'week').type, 'month');
  assert.equal(reportPeriodSelection(january, today, 'year').type, 'custom');
  assert.equal(reportPeriodSelection(dates('2026-08-01', '2026-08-31'), today, 'custom').type, 'custom');
  assert.equal(reportPeriodSelection(dates(today, today), today, 'unknown').type, 'day');
  const invalid = reportPeriodSelection(dates('2026-02-30', today), today);
  assert.equal(invalid.type, 'custom');
  assert.equal(invalid.start_date, '2026-02-30');
  assert.equal(reportPeriodDates(invalid, today), null);
});

test('ISO weeks retain the ISO year across calendar boundaries and accept valid week 53', () => {
  assert.deepEqual(reportPeriodDates({ type: 'week', year: '2020', week: '2020-W53' }, '2021-01-01'), dates('2020-12-28', '2021-01-01'));
  assert.deepEqual(reportPeriodDates({ type: 'week', year: '2025', week: '2025-W01' }, '2024-12-30'), dates('2024-12-30', '2024-12-30'));
  const selection = reportPeriodSelection(dates('2024-12-30', '2025-01-05'), '2025-01-10');
  assert.equal(selection.type, 'week');
  assert.equal(selection.year, '2025');
  assert.equal(selection.week, '2025-W01');
  assert.equal(selection.month, '12');
  assert.deepEqual(reportPeriodDates(selection, '2025-01-10'), dates('2024-12-30', '2025-01-05'));
  const custom = reportPeriodSelection(dates('2024-12-30', '2025-01-04'), '2025-01-10');
  assert.equal(custom.type, 'custom');
  assert.equal(custom.year, '2024');
  assert.equal(reportPeriodSelection(dates('2024-12-30', '2024-12-30'), '2024-12-30', 'week').year, '2025');
});

test('week options include complete week labels, disabled future weeks and only actual ISO weeks', () => {
  const weeks = reportWeekOptions('2026', today);
  assert.equal(weeks.length, 53);
  assert.deepEqual(weeks[37], { value: '2026-W38', label: 'Tuần 38 · 14/09 – 20/09', start_date: '2026-09-14', end_date: '2026-09-20', disabled: false });
  assert.equal(weeks[38].disabled, true);
  assert.deepEqual(weeks[52], { value: '2026-W53', label: 'Tuần 53 · 28/12 – 03/01', start_date: '2026-12-28', end_date: '2027-01-03', disabled: true });
  const previous = reportWeekOptions('2025', today);
  assert.equal(previous.length, 52);
  assert.equal(previous.some((week) => week.disabled), false);
  assert.equal(previous[0].start_date, '2024-12-30');
  assert.equal(previous.at(-1).end_date, '2025-12-28');
  const nextIsoYear = reportWeekOptions('2025', '2024-12-30');
  assert.equal(nextIsoYear[0].disabled, false);
  assert.equal(nextIsoYear[1].disabled, true);
  assert.deepEqual(reportWeekOptions('2025', '2024-12-29'), []);
  assert.deepEqual(reportWeekOptions('2027', today), []);
  assert.deepEqual(reportWeekOptions('1899', today), []);
  assert.deepEqual(reportWeekOptions('2026', 'invalid'), []);
});

test('week option ranges remain consecutive seven-day periods across leap day', () => {
  for (const year of ['1900', '2020', '2024', '2025', '2026']) {
    const weeks = reportWeekOptions(year, today);
    assert.ok(weeks.length === 52 || weeks.length === 53);
    for (const [index, week] of weeks.entries()) {
      assert.equal((Date.parse(week.end_date) - Date.parse(week.start_date)) / 86400000, 6);
      assert.equal(new Date(`${week.start_date}T00:00:00Z`).getUTCDay(), 1);
      if (index) assert.equal((Date.parse(week.start_date) - Date.parse(weeks[index - 1].end_date)) / 86400000, 1);
    }
  }
  assert.deepEqual(reportWeekOptions('2024', today)[8], { value: '2024-W09', label: 'Tuần 9 · 26/02 – 03/03', start_date: '2024-02-26', end_date: '2024-03-03', disabled: false });
});

test('switching away from a cleared week keeps the newly selected year, including a lost week 53', () => {
  const original = reportPeriodSelection(dates('2020-12-28', '2021-01-03'), today, 'week');
  const edited = { ...original, year: '2021', week: '' };
  assert.equal(reportPeriodDates(edited, today), null);
  const annual = switchReportPeriodType(edited, 'year', today);
  assert.equal(annual.year, '2021');
  assert.deepEqual(reportPeriodDates(annual, today), dates('2021-01-01', '2021-12-31'));
  for (const type of ['month', 'quarter']) assert.equal(switchReportPeriodType(edited, type, today).year, '2021');
  const staleWeek = switchReportPeriodType({ ...edited, week: '2020-W53' }, 'year', today);
  assert.equal(staleWeek.year, '2021');
  assert.deepEqual(edited, { ...original, year: '2021', week: '' });
});

test('switching named modes uses their edited fields instead of stale draft dates', () => {
  const original = reportPeriodSelection(dates('2026-09-01', today), today, 'month');
  for (const edited of [
    { ...original, year: '2021', month: '' },
    { ...original, type: 'quarter', year: '2021', quarter: '' },
    { ...original, type: 'year', year: '2021' },
  ]) {
    for (const type of ['month', 'quarter', 'year']) {
      assert.equal(switchReportPeriodType(edited, type, today).year, '2021');
    }
  }
  const futureMonth = { ...original, month: '12' };
  assert.equal(reportPeriodDates(futureMonth, today), null);
  const quarter = switchReportPeriodType(futureMonth, 'quarter', today);
  assert.equal(quarter.year, '2026');
  assert.equal(quarter.quarter, '4');
  assert.equal(reportPeriodDates(quarter, today), null);
});

test('switching custom dates preserves an edited valid start despite an invalid end', () => {
  const original = reportPeriodSelection(dates('2026-09-01', today), today);
  const edited = { ...original, type: 'custom', start_date: '2024-02-29', end_date: '' };
  assert.equal(reportPeriodDates(edited, today), null);
  const monthly = switchReportPeriodType(edited, 'month', today);
  assert.equal(monthly.year, '2024');
  assert.equal(monthly.month, '02');
  assert.deepEqual(reportPeriodDates(monthly, today), dates('2024-02-01', '2024-02-29'));
  assert.equal(switchReportPeriodType(edited, 'day', today).day, '2024-02-29');
  const reversed = { ...edited, end_date: '2024-02-15' };
  assert.equal(switchReportPeriodType(reversed, 'day', today).day, '2024-02-29');
  const sameMode = switchReportPeriodType(edited, 'custom', today);
  assert.deepEqual(sameMode, edited);
  assert.notEqual(sameMode, edited);
});

test('switching day and custom retains entered dates and reports unresolved dates as invalid', () => {
  const original = reportPeriodSelection(dates('2026-09-01', today), today);
  const daily = { ...original, type: 'day', day: '2023-05-19' };
  const custom = switchReportPeriodType(daily, 'custom', today);
  assert.deepEqual(reportPeriodDates(custom, today), dates('2023-05-19', '2023-05-19'));
  assert.equal(switchReportPeriodType(custom, 'day', today).day, '2023-05-19');
  const cleared = switchReportPeriodType({ ...daily, day: '' }, 'custom', today);
  assert.equal(cleared.start_date, '');
  assert.equal(cleared.end_date, '');
  assert.equal(reportPeriodDates(cleared, today), null);
  const future = switchReportPeriodType({ ...daily, day: '2027-05-19' }, 'month', today);
  assert.equal(future.year, '2027');
  assert.equal(future.month, '05');
  assert.equal(reportPeriodDates(future, today), null);
});

test('mode switches distinguish the calendar year from the ISO week year at New Year', () => {
  const custom = reportPeriodSelection(dates('2024-12-30', '2025-01-04'), today, 'custom');
  const weekly = switchReportPeriodType(custom, 'week', today);
  assert.equal(weekly.week, '2025-W01');
  assert.equal(weekly.year, '2025');
  assert.deepEqual(reportPeriodDates(weekly, today), dates('2024-12-30', '2025-01-05'));
  assert.equal(switchReportPeriodType(weekly, 'year', today).year, '2024');
  assert.deepEqual(reportPeriodDates(switchReportPeriodType(weekly, 'custom', today), today), dates('2024-12-30', '2025-01-05'));
  const january = reportPeriodSelection(dates('2021-01-01', '2021-01-31'), today, 'month');
  const boundary = switchReportPeriodType(january, 'week', today);
  assert.equal(boundary.week, '2020-W53');
  assert.equal(boundary.year, '2020');
  assert.deepEqual(reportPeriodDates(boundary, today), dates('2020-12-28', '2021-01-03'));
});
