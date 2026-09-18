import test from 'node:test';
import assert from 'node:assert/strict';
import { completionView, planProvenance, progressPeriodLabel, progressPeriodOptions, reportSelectionForPlan, selectProgressItem, validateThroughputProgress } from './throughput-progress.js';

const report = { meta: { report_id: 'snapshot-1', filters: { start_date: '2026-07-01', end_date: '2026-09-17', terminal: 'all', production_scope: 'nghe_tinh' } }, overview: { total_tonnage: 400, tonnage_status: 'ready' } };
const item = { key: 'quarter/2026-Q3', period_type: 'quarter', start_date: '2026-07-01', end_date: '2026-09-30', target: 1000, actual: 400, actual_status: 'ready' };
const response = () => ({ report_id: 'snapshot-1', production_scope: 'nghe_tinh', berth_rule_version: 'initial-berth-v1', period: { ...report.meta.filters }, eligible: true, reason: null, items: [{ ...item }] });

test('weekly plans open Monday-to-date actuals while completed weeks retain Sunday', () => {
  const plan = { period_type: 'week', period_key: '2026-W38', period_start: '2026-09-14', period_end: '2026-09-20', terminal: 'all', metric: 'tonnage', status: 'approved', is_current: true };
  assert.deepEqual(reportSelectionForPlan(plan, 'cua_lo', '2026-09-18'), {
    filters: { start_date: '2026-09-14', end_date: '2026-09-18', terminal: 'all', production_scope: 'nghe_tinh' },
    periodType: 'week', periodKey: 'week:2026-W38',
  });
  assert.equal(reportSelectionForPlan(plan, 'all', '2026-09-21').filters.end_date, '2026-09-20');
  assert.throws(() => reportSelectionForPlan(plan, 'all', '2026-09-13'), /chưa bắt đầu/);
  assert.throws(() => reportSelectionForPlan({ ...plan, period_end: '2026-09-18' }, 'all', '2026-09-18'));
  const crossYear = { ...plan, period_key: '2020-W53', period_start: '2020-12-28', period_end: '2021-01-03' };
  assert.equal(reportSelectionForPlan(crossYear, 'all', '2021-01-01').filters.end_date, '2021-01-01');
  assert.equal(progressPeriodLabel({ period_type: 'week', start_date: '2024-12-30' }), 'Tuần 1/2025');
  assert.equal(progressPeriodLabel({ period_type: 'week', start_date: '2020-12-28' }), 'Tuần 53/2020');
});

test('weekly progress uses the same snapshot and full weekly target, preserving provisional status', () => {
  const weeklyReport = structuredClone(report);
  weeklyReport.meta.filters.start_date = '2026-09-14';
  weeklyReport.meta.filters.end_date = '2026-09-18';
  const weekly = { ...item, key: 'week:2026-W38', period_type: 'week', start_date: '2026-09-14', end_date: '2026-09-20' };
  const value = { ...response(), period: { ...weeklyReport.meta.filters }, items: [weekly],
    available_periods: [{ key: weekly.key, period_type: 'week', start_date: weekly.start_date, end_date: weekly.end_date, target: 1000, terminal: 'all' }] };
  assert.equal(validateThroughputProgress(value, weeklyReport).items[0].target, 1000);
  assert.equal(completionView(weekly).percent, 40);
  weeklyReport.overview.tonnage_status = weekly.actual_status = 'partial';
  assert.equal(completionView(validateThroughputProgress(value, weeklyReport).items[0]).provisional, true);
  for (const mutate of [
    (data) => { data.items[0].actual = 401; },
    (data) => { data.items[0].end_date = data.available_periods[0].end_date = '2026-09-18'; },
    (data) => { data.available_periods[0].start_date = '2026-09-15'; },
  ]) {
    const invalid = structuredClone(value); mutate(invalid);
    assert.throws(() => validateThroughputProgress(invalid, weeklyReport));
  }
  const custom = { ...weekly, key: 'custom:2026-09-14/2026-09-20', period_type: 'custom' };
  assert.equal(selectProgressItem([custom, weekly], '', 'week'), weekly);
  assert.equal(selectProgressItem([custom, weekly], custom.key, 'week'), custom);
});

test('approved annual plan opens year-to-date actuals instead of dividing September by the annual target', () => {
  const annual = { period_type: 'year', period_key: '2026', period_start: '2026-01-01', period_end: '2026-12-31', terminal: 'all', metric: 'tonnage', status: 'approved', is_current: true };
  assert.deepEqual(reportSelectionForPlan(annual, 'cua_lo', '2026-09-17'), {
    filters: { start_date: '2026-01-01', end_date: '2026-09-17', terminal: 'all', production_scope: 'nghe_tinh' }, periodType: 'year', periodKey: 'year:2026',
  });
  assert.throws(() => reportSelectionForPlan({ ...annual, status: 'draft' }, 'all', '2026-09-17'));
  assert.throws(() => reportSelectionForPlan({ ...annual, is_current: false }, 'all', '2026-09-17'));
  assert.throws(() => reportSelectionForPlan({ ...annual, metric: 'teu' }, 'all', '2026-09-17'));
});

test('plan navigation preserves completed bounds and refuses future, invalid and excessive periods', () => {
  const past = { ...item, start_date: '2026-04-01', end_date: '2026-06-30', key: 'quarter:2026-Q2', terminal: 'ben_thuy' };
  assert.equal(reportSelectionForPlan(past, 'all', '2026-09-17').filters.end_date, '2026-06-30');
  assert.equal(reportSelectionForPlan(past, 'all', '2026-09-17').filters.terminal, 'ben_thuy');
  for (const changes of [{ start_date: '2026-10-01', end_date: '2026-12-31' }, { start_date: '2026-02-30' }, { start_date: '2025-01-01' }, { terminal: 'unknown' }]) {
    assert.throws(() => reportSelectionForPlan({ ...past, ...changes }, 'all', '2026-09-17'));
  }
});

test('other approved periods can be offered without manufacturing their actuals', () => {
  const value = response();
  const candidate = { key: item.key, period_type: item.period_type, start_date: item.start_date, end_date: item.end_date, target: item.target, terminal: 'all' };
  value.available_periods = [candidate, { ...candidate, key: 'year:2026', period_type: 'year', start_date: '2026-01-01', end_date: '2026-12-31' }];
  assert.equal(progressPeriodOptions(validateThroughputProgress(value, report)).length, 2);
  assert.equal(value.items.length, 1);
  assert.equal(progressPeriodOptions(response()).length, 1); // older server/fixtures
  const mismatch = structuredClone(value);
  mismatch.available_periods[0].target = 5000;
  assert.throws(() => validateThroughputProgress(mismatch, report));
  value.available_periods[1].actual = 123;
  assert.throws(() => validateThroughputProgress(value, report));
});

test('five bands use exact values at thresholds, without rounding into the next band', () => {
  for (const [actual, band] of [[0, 'red'], [199.999, 'red'], [200, 'orange'], [399.999, 'orange'], [400, 'yellow'], [599.999, 'yellow'], [600, 'light-green'], [799.999, 'light-green'], [800, 'dark-green']]) {
    assert.equal(completionView({ ...item, actual }).band.name, band);
  }
  assert.equal(completionView({ ...item, actual: 0.565, target: 2.825 }).percent, 20);
  assert.equal(completionView({ ...item, actual: 0.565, target: 2.825 }).band.name, 'orange');
  assert.equal(completionView({ ...item, actual: 19.96, target: 100 }).band.name, 'red');
});

test('overflow keeps real completion while visual width stops at 100%', () => {
  const view = completionView({ ...item, actual: 1250 });
  assert.equal(view.percent, 125);
  assert.equal(view.width, 100);
  assert.equal(view.achieved, true);
  assert.equal(completionView({ ...item, actual: 999.999 }).achieved, false);
});

test('partial actual can only be provisional, even above target', () => {
  const view = completionView({ ...item, actual: 1200, actual_status: 'partial' });
  assert.equal(view.percent, 120);
  assert.equal(view.provisional, true);
  assert.equal(view.achieved, false);
});

test('zero/missing targets, missing/negative actuals and unavailable sources never show a percentage', () => {
  for (const changes of [{ target: 0 }, { target: null }, { target: -1 }, { actual: null }, { actual: -1 }, { actual: NaN }, { actual: Infinity }, { actual_status: 'unavailable' }]) {
    const view = completionView({ ...item, ...changes });
    assert.equal(view.percent, null);
    assert.equal(view.band, null);
    assert.equal(view.achieved, false);
  }
  assert.equal(completionView({ ...item, actual: 0, actual_status: 'empty' }).percent, 0);
});

test('progress accepts same-snapshot current quarter against its full target', () => {
  assert.deepEqual(validateThroughputProgress(response(), report), response());
  const partialReport = structuredClone(report);
  partialReport.overview.tonnage_status = 'partial';
  const partial = response();
  partial.items[0].actual_status = 'partial';
  assert.equal(validateThroughputProgress(partial, partialReport).items[0].actual_status, 'partial');
});

test('scope, report, date bounds, numerator and berth-version mismatches are rejected', () => {
  const mutations = [
    (value) => { value.report_id = 'old-snapshot'; },
    (value) => { value.production_scope = 'vietsun'; },
    (value) => { value.berth_rule_version = 'old-rule'; },
    (value) => { value.period.terminal = 'cua_lo'; },
    (value) => { value.period.end_date = '2026-09-16'; },
    (value) => { value.items[0].start_date = '2026-01-01'; },
    (value) => { value.items[0].end_date = '2026-08-31'; },
    (value) => { value.items[0].end_date = '2026-09-31'; },
    (value) => { value.items[0].end_date = '2027-09-30'; },
    (value) => { value.items[0].actual = 401; },
    (value) => { value.items[0].actual_status = 'empty'; },
    (value) => { value.items[0].target = '1000'; },
    (value) => { value.items.push({ ...value.items[0] }); },
    (value) => { value.eligible = false; },
  ];
  for (const mutate of mutations) {
    const value = response(); mutate(value);
    assert.throws(() => validateThroughputProgress(value, report));
  }
  const foreignReport = structuredClone(report);
  foreignReport.meta.filters.production_scope = 'vietsun';
  const foreignValue = response();
  foreignValue.production_scope = foreignValue.period.production_scope = 'vietsun';
  assert.throws(() => validateThroughputProgress(foreignValue, foreignReport));
});

test('missing plan is an explicit empty state, not a zero target', () => {
  const value = { ...response(), items: [], reason: 'Chưa có kế hoạch duyệt.' };
  assert.deepEqual(validateThroughputProgress(value, report).items, []);
  assert.equal(selectProgressItem([], '', 'quarter'), null);
});

test('explicit choice takes precedence over report preset without adding targets together', () => {
  const options = [item, { ...item, key: 'custom', period_type: 'custom' }];
  assert.equal(selectProgressItem(options, 'custom', 'quarter').key, 'custom');
  assert.equal(selectProgressItem(options, 'removed', 'quarter').key, item.key);
  assert.equal(selectProgressItem([...options].reverse(), '', null).key, item.key);
  assert.equal(progressPeriodLabel(item), 'Quý 3/2026');
  assert.equal(progressPeriodLabel({ ...item, period_type: 'year' }), 'Năm 2026');
});

test('plan provenance preserves official references and identifies only an explicit TEST reference prefix', () => {
  assert.deepEqual(planProvenance({ plans: [{ reference: ' KH-2026 ' }, { reference: 'KH-2026' }, { reference: 'QD-09' }] }), { references: ['KH-2026', 'QD-09'], isTest: false });
  for (const reference of ['TEST-DASHBOARD-20260917-MONTH', ' test quarter ', 'TEST']) {
    assert.equal(planProvenance({ plans: [{ reference: 'KH-2026' }, { reference }] }).isTest, true);
  }
  for (const reference of ['TESTING-2026', 'KH-TEST-2026', 'CONTEST-2026']) {
    assert.equal(planProvenance({ plans: [{ reference, note: 'TEST', is_test: true }] }).isTest, false);
  }
  assert.deepEqual(planProvenance({ plans: [null, {}, { reference: 12 }, { reference: ' ' }] }), { references: [], isTest: false });
  assert.deepEqual(planProvenance(null), { references: [], isTest: false });
});
