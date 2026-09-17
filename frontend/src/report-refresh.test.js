import assert from 'node:assert/strict';
import test from 'node:test';
import { AUTO_REFRESH_MS, currentReportPeriod, rememberAutoRefresh, reportRequestRefresh, restoreAutoRefresh, shouldAutoRefresh } from './report-refresh.js';

const filters = { start_date: '2026-09-01', end_date: '2026-09-13', terminal: 'all' };
const ready = { enabled: true, filters, today: '2026-09-13', visible: true, reportView: true, loading: false, hasDraft: false, modalOpen: false, editing: false };
test('initial retries reuse completed reports while explicit refresh is consumed for its selection only', () => {
  const annual = JSON.stringify({ ...filters, start_date: '2026-01-01', production_scope: 'nghe_tinh' });
  const monthly = JSON.stringify({ ...filters, production_scope: 'nghe_tinh' });
  const otherScope = JSON.stringify({ ...filters, start_date: '2026-01-01', production_scope: 'vietsun' });
  const retry = { revision: 1, filterKey: annual, forceRefresh: false };
  assert.equal(reportRequestRefresh(retry, annual, 0), false);
  const explicit = { revision: 2, filterKey: annual, forceRefresh: true };
  assert.equal(reportRequestRefresh(explicit, annual, 1), true);
  assert.equal(reportRequestRefresh(explicit, annual, 2), false);
  assert.equal(reportRequestRefresh(explicit, monthly, 1), false);
  assert.equal(reportRequestRefresh(explicit, otherScope, 1), false);
  // A filter change consumes a pending action even when its key does not match.
  // Navigating back cannot silently force another annual SQL read.
  assert.equal(reportRequestRefresh(explicit, annual, explicit.revision), false);
  const staleRetry = { revision: 3, filterKey: annual, forceRefresh: true };
  assert.equal(reportRequestRefresh(staleRetry, annual, 2), true);
  const recovery = { revision: 4, filterKey: annual, forceRefresh: false };
  assert.equal(reportRequestRefresh(recovery, annual, 3), false);
});
test('auto refresh only includes reports ending today, never historical or future periods', () => {
  assert.ok(AUTO_REFRESH_MS >= 60000);
  assert.equal(currentReportPeriod(filters, '2026-09-13'), true);
  assert.equal(currentReportPeriod(filters, '2026-09-14'), false);
  assert.equal(currentReportPeriod(filters, '2026-09-12'), false);
  assert.equal(currentReportPeriod({ ...filters, start_date: '2026-10-01' }, '2026-09-13'), false);
  assert.equal(currentReportPeriod(null, '2026-09-13'), false);
});
test('each user interaction and hidden surface pauses automatic SQL reads', () => {
  assert.equal(shouldAutoRefresh(ready), true);
  for (const key of ['enabled', 'visible', 'reportView']) assert.equal(shouldAutoRefresh({ ...ready, [key]: false }), false, key);
  for (const key of ['loading', 'hasDraft', 'modalOpen', 'editing']) assert.equal(shouldAutoRefresh({ ...ready, [key]: true }), false, key);
  assert.equal(shouldAutoRefresh({ ...ready, today: '2026-09-14' }), false);
});
test('auto refresh defaults off, persists per account and tolerates blocked storage', (t) => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const data = new Map();
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: (key) => data.get(key), setItem: (key, value) => data.set(key, value) } });
  t.after(() => { if (descriptor) Object.defineProperty(globalThis, 'localStorage', descriptor); else delete globalThis.localStorage; });
  assert.equal(restoreAutoRefresh({ id: 1 }), false);
  rememberAutoRefresh({ id: 1 }, true);
  assert.equal(restoreAutoRefresh({ id: 1 }), true);
  assert.equal(restoreAutoRefresh({ id: 2 }), false);
  rememberAutoRefresh({ id: 1 }, false);
  assert.equal(restoreAutoRefresh({ id: 1 }), false);
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('blocked'); } });
  rememberAutoRefresh({ id: 1 }, true);
  assert.equal(restoreAutoRefresh({ id: 1 }), false);
});
