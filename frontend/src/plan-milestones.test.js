import test from 'node:test';
import assert from 'node:assert/strict';
import { milestoneBounds, validMilestonePace, validateMilestones } from './plan-milestones.js';
import { buildPlanUpdatePayload, parseVietnamesePlanAmount, validatePlanEntry } from './management-data.js';

const form = { terminal: 'cua_lo', period_type: 'month', month: '2026-09', metric: 'tonnage', amount: '1.000', reference: 'Synthetic', note: '' };
test('milestones use full period bounds including ISO years and leap months', () => {
  assert.deepEqual(milestoneBounds({ period_type: 'week', week: '2020-W53' }), { start_date: '2020-12-28', end_date: '2021-01-03' });
  assert.equal(milestoneBounds({ period_type: 'month', month: '2024-02' }).end_date, '2024-02-29');
  assert.deepEqual(milestoneBounds({ period_type: 'quarter', quarter: '2026-Q3' }), { start_date: '2026-07-01', end_date: '2026-09-30' });
});
test('Vietnamese milestone values survive create and edit payloads exactly', () => {
  const entry = validatePlanEntry({ ...form, milestones: [{ date: '2026-09-05', amount: '250,125' }, { date: '2026-09-30', amount: '1.000' }] }, ['cua_lo']);
  assert.deepEqual(entry.errors, {});
  assert.deepEqual(entry.payload.milestones.map((row) => row.amount), ['250.125', '1000']);
  assert.deepEqual(buildPlanUpdatePayload(entry.payload, ['cua_lo']).milestones, entry.payload.milestones);
});
test('milestone validation rejects out of period, duplicate, decreasing and over-target schedules', () => {
  for (const rows of [
    [{ date: '2026-08-31', amount: '100' }], [{ date: '2026-09-31', amount: '100' }],
    [{ date: '2026-09-02', amount: '100' }, { date: '2026-09-02', amount: '200' }],
    [{ date: '2026-09-02', amount: '200' }, { date: '2026-09-05', amount: '100' }],
    [{ date: '2026-09-05', amount: '1.000,000001' }],
  ]) assert.throws(() => validateMilestones(rows, form, '1000', parseVietnamesePlanAmount));
  assert.throws(() => validateMilestones([{ date: '2026-09-05', amount: '10' }], { ...form, period_type: 'voyage' }, '1000', parseVietnamesePlanAmount));
});
test('milestone amounts retain six-decimal precision at the allowed maximum', () => {
  const rows = [{ date: '2026-09-05', amount: '999.999.999.999,999999' }];
  assert.equal(validateMilestones(rows, form, '1000000000000', parseVietnamesePlanAmount)[0].amount, '999999999999.999999');
});

const item = { start_date: '2026-09-01' };
const report = { meta: { filters: { end_date: '2026-09-03' } }, daily_history: [
  { date: '2026-09-01', tonnage: 200, tonnage_status: 'ready' },
  { date: '2026-09-02', tonnage: 0, tonnage_status: 'empty' },
  { date: '2026-09-03', tonnage: 800, tonnage_status: 'ready' },
] };
const pace = { status: 'ready', milestone_date: '2026-09-02', target: 500, actual: 200, difference: -300, completion_percent: 40 };
test('pace validates cumulative actuals at its milestone, never later report actuals', () => {
  assert.equal(validMilestonePace(pace, item, report), true);
  assert.equal(validMilestonePace({ ...pace, actual: 1000, difference: 500, completion_percent: 200 }, item, report), false);
  assert.equal(validMilestonePace({ ...pace, milestone_date: '2026-09-04' }, item, report), false);
});
test('missing, partial and duplicate days cannot claim a met milestone', () => {
  for (const daily of [report.daily_history.slice(1), [...report.daily_history, report.daily_history[0]], report.daily_history.map((row, i) => i ? row : { ...row, tonnage_status: 'partial' })]) {
    assert.equal(validMilestonePace(pace, item, { ...report, daily_history: daily }), false);
  }
  assert.equal(validMilestonePace({ status: 'unknown', reason: 'Missing schedule', actual: null, difference: null, completion_percent: null }, item, report), true);
  assert.equal(validMilestonePace({ status: 'unknown', reason: 'Missing schedule', actual: 200, difference: null, completion_percent: null }, item, report), false);
});
