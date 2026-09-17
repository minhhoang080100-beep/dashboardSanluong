import assert from 'node:assert/strict';
import test from 'node:test';
import { allowedTerminals, rememberFilters, restoreFilters } from './filter-preferences.js';
import { presetDates, todayInVietnam } from './dashboard-data.js';

function environment(t, now = '2026-09-13T03:00:00Z', storage) {
  t.mock.timers.enable({ apis: ['Date'], now: new Date(now) });
  const data = new Map();
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage || { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) } });
  t.after(() => { if (descriptor) Object.defineProperty(globalThis, 'localStorage', descriptor); else delete globalThis.localStorage; });
  return data;
}

test('filter preferences stay separate for each internal user', (t) => {
  environment(t);
  const first = { id: 1, terminals: ['cua_lo', 'ben_thuy'] };
  const second = { id: 2, terminals: ['cua_lo', 'ben_thuy'] };
  const filters = { start_date: '2026-08-01', end_date: '2026-08-31', terminal: 'ben_thuy', production_scope: 'nghe_tinh' };
  rememberFilters(first, filters);
  assert.deepEqual(restoreFilters(first), filters);
  assert.deepEqual(restoreFilters(second), { start_date: '2026-09-01', end_date: '2026-09-13', terminal: 'all', production_scope: 'nghe_tinh' });
  rememberFilters(second, { ...filters, terminal: 'cua_lo' });
  assert.equal(restoreFilters(first).terminal, 'ben_thuy');
});

test('a reduced terminal scope discards a previously saved wider filter', (t) => {
  environment(t);
  const user = { id: 1, role: 'admin', terminals: ['cua_lo', 'ben_thuy'] };
  rememberFilters(user, { start_date: '2026-08-01', end_date: '2026-08-31', terminal: 'all', production_scope: 'nghe_tinh' });
  assert.deepEqual(restoreFilters({ ...user, terminals: ['ben_thuy'] }), { start_date: '2026-09-01', end_date: '2026-09-13', terminal: 'ben_thuy', production_scope: 'nghe_tinh' });
  assert.deepEqual(allowedTerminals({ role: 'admin', terminals: ['cua_lo'] }), ['cua_lo']);
  assert.deepEqual(allowedTerminals({ terminals: ['cua_lo', 'cua_lo', 'unknown'] }), ['cua_lo']);
  assert.deepEqual(allowedTerminals({ terminals: ['cua_lo', 'ben_thuy'] }), ['all', 'cua_lo', 'ben_thuy']);
});

test('corrupt, impossible, future, overlong and unauthorized saved filters revert to current scoped defaults', (t) => {
  const data = environment(t);
  const user = { id: 5, terminals: ['cua_lo'] };
  const defaults = { start_date: '2026-09-01', end_date: '2026-09-13', terminal: 'cua_lo', production_scope: 'nghe_tinh' };
  const base = { start_date: '2026-08-01', end_date: '2026-08-31', terminal: 'cua_lo', production_scope: 'nghe_tinh' };
  for (const saved of ['{', 'null', '42', JSON.stringify({}), ...[
    { start_date: '2026-02-30' }, { start_date: '2026-09-01' }, { end_date: '2026-09-14' },
    { start_date: '2024-01-01' }, { terminal: 'all' }, { terminal: 'ben_thuy' },
  ].map((change) => JSON.stringify({ ...base, ...change }))]) {
    data.set('port-report-filters-5', saved);
    assert.deepEqual(restoreFilters(user), defaults, saved);
  }
});

test('unavailable preference storage does not prevent loading or applying reports', (t) => {
  const blocked = () => { throw new Error('Storage blocked'); };
  environment(t, undefined, { getItem: blocked, setItem: blocked });
  const user = { id: 8, terminals: ['ben_thuy'] };
  assert.doesNotThrow(() => rememberFilters(user, { terminal: 'ben_thuy' }));
  assert.deepEqual(restoreFilters(user), { start_date: '2026-09-01', end_date: '2026-09-13', terminal: 'ben_thuy', production_scope: 'nghe_tinh' });
});

test('today and yesterday presets follow Vietnam midnight across the year boundary', (t) => {
  environment(t, '2026-12-31T17:00:00Z');
  assert.equal(todayInVietnam(), '2027-01-01');
  assert.deepEqual(presetDates('today'), { start_date: '2027-01-01', end_date: '2027-01-01' });
  assert.deepEqual(presetDates('yesterday'), { start_date: '2026-12-31', end_date: '2026-12-31' });
  assert.deepEqual(presetDates('year'), { start_date: '2027-01-01', end_date: '2027-01-01' });
  assert.deepEqual(restoreFilters({ id: 1, terminals: ['cua_lo'] }), { start_date: '2027-01-01', end_date: '2027-01-01', terminal: 'cua_lo', production_scope: 'nghe_tinh' });
  assert.deepEqual(presetDates('yesterday', '2028-03-01'), { start_date: '2028-02-29', end_date: '2028-02-29' });
});

test('scope preference persists per user while legacy or invalid scope resets to a fresh Nghệ Tĩnh selection', (t) => {
  environment(t);
  const user = { id: 31, terminals: ['cua_lo', 'ben_thuy'] };
  const filters = { start_date: '2026-08-01', end_date: '2026-08-31', terminal: 'all', production_scope: 'vietsun' };
  rememberFilters(user, filters);
  assert.deepEqual(restoreFilters(user), filters);
  assert.equal(restoreFilters({ ...user, id: 32 }).production_scope, 'nghe_tinh');
  for (const production_scope of [undefined, null, 'all', 'invalid']) {
    rememberFilters(user, { ...filters, production_scope });
    const restored = restoreFilters(user);
    assert.equal(restored.production_scope, 'nghe_tinh');
    assert.equal(restored.start_date, '2026-09-01');
  }
});
