import assert from 'node:assert/strict';
import test from 'node:test';
import { sameClosedReportScope } from './management-data.js';
import { buildPlanPayload, buildPlanUpdatePayload, buildUserPayload, canManage, parseVietnamesePlanAmount, planAmountInput, planDateDefaults, planDeletePayload, planEntryPeriod, planPeriodEligibility, planPeriodFields, planPeriodLabel, planTerminals, planWeekDates, queryPath, userTerminals, validatedItems, validatePlanEntry, validatedPlanPreview } from './management-data.js';

test('deleting a plan requires its actual revision and never guesses a missing version', () => {
  for (const status of ['draft', 'approved', 'cancelled']) {
    assert.deepEqual(planDeletePayload({ id: 5, revision: 4, status }), { revision: 4 });
  }
  for (const plan of [null, {}, { version: 2 }, { revision: '4' }, { revision: 0 }, { revision: -1 }, { revision: 1.5 }, { revision: Infinity }, { revision: 4, is_deleted: true }]) {
    assert.throws(() => planDeletePayload(plan), /tải lại danh sách/);
  }
});

test('management scope follows assigned terminals and roles', () => {
  assert.deepEqual(userTerminals({ role: 'admin', terminals: ['cua_lo'] }), ['cua_lo']);
  assert.deepEqual(userTerminals({ terminals: ['unknown'] }), []);
  assert.equal(canManage({ role: 'viewer' }), false);
  assert.equal(canManage({ role: 'manager' }), true);
});

test('closed reports compare only within the saved comparison mode and keep legacy defaults compatible', () => {
  const filters = { start_date: '2026-09-01', end_date: '2026-09-18', terminal: 'cua_lo', production_scope: 'nghe_tinh' };
  const report = { meta: { filters, berth_rule_version: 'initial-berth-v1' } };
  const legacy = { ...filters, berth_rule_version: 'initial-berth-v1' };
  const previous = { ...legacy, comparison: 'previous_period' };
  const annual = { ...legacy, comparison: 'previous_year' };
  const annualReport = { meta: { ...report.meta, filters: { ...filters, comparison: 'previous_year' } } };
  assert.equal(sameClosedReportScope(legacy, report), true);
  assert.equal(sameClosedReportScope(previous, report), true);
  assert.equal(sameClosedReportScope(annual, report), false);
  assert.equal(sameClosedReportScope(legacy, annualReport), false);
  assert.equal(sameClosedReportScope(previous, annualReport), false);
  assert.equal(sameClosedReportScope(annual, annualReport), true);
  for (const change of [{ terminal: 'ben_thuy' }, { production_scope: 'vietsun' }, { berth_rule_version: 'old' }, { start_date: '2026-08-01' }, { end_date: '2026-09-17' }]) {
    assert.equal(sameClosedReportScope({ ...annual, ...change }, annualReport), false);
  }
});

test('monthly plan comparison accepts any valid end date in a period beginning on the first day', () => {
  assert.equal(planPeriodEligibility({ start_date: '2026-09-01', end_date: '2026-09-13' }, '2026-09-13').eligible, true);
  assert.equal(planPeriodEligibility({ start_date: '2026-08-01', end_date: '2026-08-31' }, '2026-09-13').eligible, true);
  assert.equal(planPeriodEligibility({ start_date: '2028-02-01', end_date: '2028-02-29' }, '2028-03-01').eligible, true);
  assert.equal(planPeriodEligibility({ start_date: '2026-09-01', end_date: '2026-09-12' }, '2026-09-13').eligible, true);
  assert.equal(planPeriodEligibility({ start_date: '2026-08-01', end_date: '2026-08-05' }, '2026-09-13').eligible, true);
  assert.equal(planPeriodEligibility({ start_date: '2026-09-01', end_date: '2026-09-01' }).eligible, true);
  for (const filters of [
    { start_date: '2026-09-02', end_date: '2026-09-13' },
    { start_date: '2026-08-01', end_date: '2026-09-13' },
    { start_date: '2026-09-01', end_date: '2026-08-31' },
    { start_date: '2026-09-01', end_date: '2026-09-31' },
    { start_date: '2026-02-01', end_date: '2026-02-29' },
    { start_date: '2026-09-01', end_date: '' },
  ]) assert.equal(planPeriodEligibility(filters, '2026-09-13').eligible, false);
});

test('plans require an authorized terminal, approved-document reference and explicit nonnegative amount', () => {
  const form = { terminal: 'cua_lo', period_type: 'month', month: '2026-09', metric: 'tonnage', amount: '0', reference: ' KH-09 ', note: '' };
  assert.equal(buildPlanPayload(form, ['cua_lo']).amount, 0);
  assert.equal(buildPlanPayload(form, ['cua_lo']).reference, 'KH-09');
  for (const change of [{ terminal: 'ben_thuy' }, { amount: '' }, { amount: '-1' }, { amount: 'NaN' }, { amount: 'Infinity' }, { month: '2026-13' }, { month: '2100-01' }, { reference: ' ' }]) {
    assert.throws(() => buildPlanPayload({ ...form, ...change }, ['cua_lo']));
  }
  const voyage = buildPlanPayload({ ...form, period_type: 'voyage', voyage_id: '101' }, ['cua_lo']);
  assert.equal(voyage.voyage_id, 101);
  assert.equal(voyage.month, undefined);
  assert.throws(() => buildPlanPayload({ ...form, period_type: 'voyage', voyage_id: '2147483648' }, ['cua_lo']));
});

test('Vietnamese amounts preserve decimal precision and reject mixed or ambiguous separators', () => {
  for (const [input, expected] of [['150000', '150000'], ['150.000', '150000'], ['150.000,5', '150000.5'], [' 00150000,5000000 ', '150000.5'], ['0', '0'], ['0,000001', '0.000001'], ['999.999.999.999,999999', '999999999999.999999'], ['1.000.000.000.000', '1000000000000']]) assert.equal(parseVietnamesePlanAmount(input), expected);
  for (const input of ['', '150,000.5', '150000.5', '150.00', '1.23.456', '1 000', '1e6', '-1', 'NaN', 'Infinity', '0,0000001', '1.000.000.000.000,000001']) assert.throws(() => parseVietnamesePlanAmount(input));
  for (const canonical of ['0', '150000.5', '0.000001', '999999999999.999999', '1000000000000']) assert.equal(parseVietnamesePlanAmount(planAmountInput(canonical)), canonical);
});

test('plan entry validates every field locally and sends an exact canonical decimal string', () => {
  const form = { terminal: 'all', period_type: 'quarter', quarter: '2026-Q3', metric: 'tonnage', amount: '999.999.999.999,999999', reference: ' KH-Q3 ', note: '' };
  const valid = validatePlanEntry(form, ['cua_lo', 'ben_thuy']);
  assert.deepEqual(valid.errors, {});
  assert.equal(valid.payload.amount, '999999999999.999999');
  assert.equal(valid.payload.reference, 'KH-Q3');
  assert.equal(valid.payload.quarter, '2026-Q3');
  const invalid = validatePlanEntry({ ...form, quarter: '2026-Q5', amount: '1,2.3', reference: '', note: 'x'.repeat(4001) }, ['cua_lo']);
  assert.equal(invalid.payload, null);
  assert.deepEqual(Object.keys(invalid.errors).sort(), ['amount', 'note', 'period', 'reference', 'terminal']);
  assert.equal(validatePlanEntry({ ...form, amount: '0' }, ['cua_lo', 'ben_thuy']).payload.amount, '0');
});

test('planning entry follows the selected quarter or year without treating a custom period as a month', () => {
  assert.equal(planEntryPeriod({ start_date: '2026-07-01', end_date: '2026-09-17' }), 'quarter');
  assert.equal(planEntryPeriod({ start_date: '2026-01-01', end_date: '2026-03-31' }), 'quarter');
  assert.equal(planEntryPeriod({ start_date: '2026-01-01', end_date: '2026-09-17' }), 'year');
  assert.equal(planEntryPeriod({ start_date: '2026-09-01', end_date: '2026-09-17' }), 'month');
  assert.equal(planEntryPeriod({ start_date: '2026-09-05', end_date: '2026-09-17' }), 'custom');
  assert.equal(planEntryPeriod({ start_date: '2026-01-01', end_date: '2026-01-17' }, 'year'), 'year');
  assert.equal(planEntryPeriod({ start_date: '2026-01-01', end_date: '2026-03-31' }, 'custom'), 'custom');
  assert.equal(planEntryPeriod({ start_date: '2026-06-01', end_date: '2026-06-07' }, 'week'), 'week');
});

test('time-based targets support company scope only with both terminal grants and never for a voyage', () => {
  assert.deepEqual(planTerminals(['cua_lo']), ['cua_lo']);
  assert.deepEqual(planTerminals(['all']), []);
  assert.deepEqual(planTerminals(['cua_lo', 'ben_thuy']), ['all', 'cua_lo', 'ben_thuy']);
  assert.deepEqual(planTerminals(['cua_lo', 'ben_thuy'], 'voyage'), ['cua_lo', 'ben_thuy']);
  const form = { terminal: 'all', period_type: 'quarter', quarter: '2026-Q3', metric: 'tonnage', amount: 1000, reference: 'KH-Q3' };
  assert.equal(buildPlanPayload(form, ['cua_lo', 'ben_thuy']).terminal, 'all');
  assert.throws(() => buildPlanPayload(form, ['cua_lo']));
  assert.throws(() => buildPlanPayload({ ...form, period_type: 'voyage', voyage_id: 101 }, ['cua_lo', 'ben_thuy']));
  assert.deepEqual(buildUserPayload({ username: 'viewer', display_name: 'Viewer', role: 'viewer', terminals: ['all', 'cua_lo'] }).terminals, ['cua_lo']);
});

test('weekly plans keep all seven days, permit future weeks and use the ISO year across calendar boundaries', () => {
  assert.deepEqual(planWeekDates('2026-W38'), { start_date: '2026-09-14', end_date: '2026-09-20' });
  assert.deepEqual(planWeekDates('2030-W01'), { start_date: '2029-12-31', end_date: '2030-01-06' });
  assert.deepEqual(planWeekDates('2026-W53'), { start_date: '2026-12-28', end_date: '2027-01-03' });
  assert.deepEqual(planWeekDates('2099-W53'), { start_date: '2099-12-28', end_date: '2100-01-03' });
  assert.deepEqual(planWeekDates('2000-W01'), { start_date: '2000-01-03', end_date: '2000-01-09' });
  assert.equal(planDateDefaults('2027-01-01').week, '2026-W53');
  assert.equal(planPeriodLabel({ period_type: 'week', week: '2026-W38' }), 'Tuần 38/2026 · 14/09/2026 – 20/09/2026');
  assert.equal(planPeriodLabel({ period_type: 'week', week: '2026-W53' }), 'Tuần 53/2026 · 28/12/2026 – 03/01/2027');
});

test('weekly plan validation rejects invalid weeks and safely clears inactive fields when switching draft types', () => {
  const form = { terminal: 'all', period_type: 'week', week: '2026-W38', month: '2026-09', year: 2026, metric: 'tonnage', amount: '12.345,67', reference: ' KH-W38 ' };
  const validated = validatePlanEntry(form, ['cua_lo', 'ben_thuy']);
  assert.deepEqual(validated.errors, {});
  assert.deepEqual(validated.payload, { terminal: 'all', period_type: 'week', week: '2026-W38', metric: 'tonnage', amount: '12345.67', reference: 'KH-W38', note: '' });
  assert.equal(validatePlanEntry(form, ['cua_lo']).payload, null);
  for (const week of ['', undefined, null, '2025-W53', '2026-W00', '2026-W54', '2026-W3', '2026-w38', '1999-W52', '2100-W01']) {
    assert.equal(planWeekDates(week), null);
    assert.equal(validatePlanEntry({ ...form, week }, ['cua_lo', 'ben_thuy']).payload, null);
    assert.throws(() => planPeriodFields({ period_type: 'week', week }));
  }
  assert.equal(planPeriodLabel({ period_type: 'week', week: '2025-W53' }), '—');
  const weeklyUpdate = buildPlanUpdatePayload(validated.payload, ['cua_lo', 'ben_thuy']);
  assert.equal(weeklyUpdate.week, '2026-W38');
  for (const field of ['month', 'quarter', 'year', 'start_date', 'end_date', 'voyage_id']) assert.equal(weeklyUpdate[field], null);
  const monthlyUpdate = buildPlanUpdatePayload({ ...validated.payload, period_type: 'month', month: '2026-09' }, ['cua_lo', 'ben_thuy']);
  assert.equal(monthlyUpdate.week, null);
  assert.equal(monthlyUpdate.month, '2026-09');
  assert.equal(queryPath('/plans', { period_type: 'week', ...planPeriodFields(form), terminal: 'all' }), '/plans?period_type=week&week=2026-W38&terminal=all');
});

test('targets send only the active period fields and permit full future targets', () => {
  const form = { terminal: 'cua_lo', period_type: 'month', week: '2099-W53', month: '2026-09', quarter: '2026-Q4', year: '2027', start_date: '2026-07-01', end_date: '2026-12-31', voyage_id: 101, metric: 'tonnage', amount: '200', reference: 'KH' };
  const periods = { week: { week: '2099-W53' }, month: { month: '2026-09' }, quarter: { quarter: '2026-Q4' }, year: { year: 2027 }, custom: { start_date: '2026-07-01', end_date: '2026-12-31' }, voyage: { voyage_id: 101 } };
  for (const [period_type, fields] of Object.entries(periods)) {
    const payload = buildPlanPayload({ ...form, period_type }, ['cua_lo']);
    assert.deepEqual(payload, { terminal: 'cua_lo', period_type, metric: 'tonnage', amount: 200, reference: 'KH', note: '', ...fields });
  }
  assert.deepEqual(planPeriodFields({ period_type: 'custom', start_date: '2028-01-01', end_date: '2028-12-31' }), { start_date: '2028-01-01', end_date: '2028-12-31' });
  for (const change of [
    { period_type: 'quarter', quarter: '2026-Q5' }, { period_type: 'quarter', quarter: '1999-Q1' },
    { period_type: 'quarter', quarter: '2026-q3' }, { period_type: 'year', year: 2100 },
    { period_type: 'year', year: '' }, { period_type: 'year', year: 2026.5 },
    { period_type: 'custom', start_date: '2026-02-29', end_date: '2026-03-31' },
    { period_type: 'custom', start_date: '2026-09-02', end_date: '2026-09-01' },
    { period_type: 'custom', start_date: '2028-01-01', end_date: '2029-01-01' },
    { period_type: 'custom', start_date: '', end_date: '2026-12-31' },
    { period_type: 'custom', start_date: '1999-12-31', end_date: '2000-01-01' },
  ]) assert.throws(() => buildPlanPayload({ ...form, ...change }, ['cua_lo']));
});

test('period labels and defaults keep quarter, year and custom targets distinguishable in history and lists', () => {
  assert.deepEqual(planDateDefaults('2026-10-01', '2026-10-17'), { week: '2026-W40', month: '2026-10', quarter: '2026-Q4', year: '2026', start_date: '2026-10-01', end_date: '2026-10-17' });
  assert.equal(planPeriodLabel({ period_type: 'quarter', quarter: '2026-Q3' }), 'Quý 3/2026');
  assert.equal(planPeriodLabel({ period_type: 'year', year: 2026 }), 'Năm 2026');
  assert.equal(planPeriodLabel({ period_type: 'custom', start_date: '2026-09-01', end_date: '2026-10-15' }), '01/09/2026 – 15/10/2026');
  assert.equal(planPeriodLabel({ period_type: 'voyage', voyage_id: 101 }), 'Chuyến 101');
  assert.equal(planPeriodLabel({ period_type: 'quarter', quarter: null }), '—');
});

test('changing a draft period explicitly clears old period fields when PATCH is merged server side', () => {
  const month = { terminal: 'cua_lo', period_type: 'month', month: '2026-09', voyage_id: null, metric: 'tonnage', amount: 200, reference: 'KH' };
  const quarter = { ...month, period_type: 'quarter', quarter: '2026-Q3' };
  const update = buildPlanUpdatePayload(quarter, ['cua_lo']);
  assert.deepEqual({ ...month, ...update }, { terminal: 'cua_lo', period_type: 'quarter', week: null, month: null, quarter: '2026-Q3', year: null, start_date: null, end_date: null, voyage_id: null, metric: 'tonnage', amount: 200, reference: 'KH', note: '' });
  const unchanged = buildPlanUpdatePayload({ ...month, amount: 300 }, ['cua_lo']);
  assert.equal(unchanged.month, '2026-09');
  assert.equal(unchanged.amount, 300);
});

test('new users need an explicit role and terminal scope; passwords are generated by the server', () => {
  const form = { username: 'operator.1', display_name: 'Người dùng', role: 'viewer', terminals: ['cua_lo', 'unknown'], password: 'ignored' };
  assert.deepEqual(buildUserPayload(form), { username: 'operator.1', display_name: 'Người dùng', role: 'viewer', terminals: ['cua_lo'] });
  for (const change of [{ username: 'a' }, { display_name: '' }, { display_name: 'a'.repeat(161) }, { role: 'owner' }, { terminals: [] }]) assert.throws(() => buildUserPayload({ ...form, ...change }));
});

test('list contracts fail visibly and query parameters retain scope safely', () => {
  assert.throws(() => validatedItems({ rows: [] }));
  assert.deepEqual(validatedItems({ items: [] }), []);
  assert.equal(queryPath('/issues', { terminal: 'cua_lo', source_id: 'a&b', page: 1, empty: '' }), '/issues?terminal=cua_lo&source_id=a%26b&page=1');
});

test('Excel confirmation requires a valid preview and every row stays within assigned scope', () => {
  const row = { terminal: 'cua_lo', period_type: 'month', month: '2026-09', metric: 'tonnage', amount: 12.5, reference: 'KH-09' };
  assert.equal(validatedPlanPreview({ valid: true, rows: [row], errors: [] }, ['cua_lo']).rows[0].amount, 12.5);
  for (const preview of [
    { valid: true, rows: [], errors: [] },
    { valid: true, rows: [row], errors: [{ row: 2, message: 'Lỗi' }] },
    { valid: true, rows: [{ ...row, terminal: 'ben_thuy' }], errors: [] },
    { valid: true, rows: [{ ...row, amount: -1 }], errors: [] },
  ]) assert.throws(() => validatedPlanPreview(preview, ['cua_lo']));
  const extended = [
    { ...row, period_type: 'week', week: '2026-W38' },
    { ...row, terminal: 'all', period_type: 'quarter', quarter: '2026-Q3' },
    { ...row, period_type: 'year', year: 2026 },
    { ...row, period_type: 'custom', start_date: '2026-07-01', end_date: '2026-09-30' },
  ];
  const preview = validatedPlanPreview({ valid: true, rows: extended, errors: [] }, ['cua_lo', 'ben_thuy']);
  assert.deepEqual(preview.rows.map((item) => item.period_type), ['week', 'quarter', 'year', 'custom']);
  assert.ok(preview.rows.every((item) => !Object.hasOwn(item, 'month')));
  assert.throws(() => validatedPlanPreview({ valid: true, rows: extended, errors: [] }, ['cua_lo']));
});
