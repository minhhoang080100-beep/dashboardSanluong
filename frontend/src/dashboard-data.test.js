import assert from 'node:assert/strict';
import test from 'node:test';
import { csvCell, dashboardCsv, dashboardRequestTimeout, dashboardResourceView, fetchDashboard, formatNumber, formatTimestamp, isCurrentDayPeriod, presetDates, ratioAvailability, todayInVietnam, validateDashboard, validateFilters } from './dashboard-data.js';

const filters = { start_date: '2026-09-01', end_date: '2026-09-09', terminal: 'all', production_scope: 'nghe_tinh' };
function fixture() {
  return {
    overview: { total_tonnage: 100, total_measured_tonnage: 90, total_teu: 2, vessel_calls: 1, record_count: 3 },
    cargo: [{ name: 'Hàng tổng hợp', tonnage: 100, value: 100 }],
    history: [{ date: '2026-09', tonnage: 100, teu: 2 }],
    terminals: [{ name: 'Cửa Lò', tonnage: 100, teu: 2 }],
    directions: [{ name: 'Hàng xếp', tonnage: 100, value: 100 }],
    customers: [{ name: '=HYPERLINK("https://example.com")', terminal_name: 'Cửa Lò', volume: 100 }],
    voyages: [{ production_scope: 'nghe_tinh', initial_berth_id: 1, initial_berth_code: 'C1', initial_berth_at: '2026-09-01T07:00:00', berth_assignment_status: 'assigned', terminal_id: 'cua_lo', terminal_name: 'Cửa Lò', voyage_id: '1', vessel_name: 'Tàu kiểm thử', voyage_code: 'TEST-1', arrival_at: null, departure_at: null, first_operation_date: '2026-09-01', last_operation_date: '2026-09-01', tonnage: 100, teu: 2, record_count: 3, cargo_names: ['Hàng tổng hợp'] }],
    meta: { berth_rule_version: 'initial-berth-v1', status: 'ok', generated_at: '2026-09-09T00:00:00Z', filters: { ...filters }, warnings: ['Quy tắc chưa đối soát'], definitions: { tonnage: 'Tấn' } },
  };
}

test('calendar follows Vietnam midnight even if device is elsewhere', () => {
  assert.equal(todayInVietnam(new Date('2026-09-08T17:00:00Z')), '2026-09-09');
  assert.equal(todayInVietnam(new Date('2026-09-08T16:59:59Z')), '2026-09-08');
});

test('refresh keeps a validated same-period snapshot, surfaces failed refresh and clears on filter change', () => {
  const data = fixture();
  for (const status of ['refreshing', 'stale']) {
    const resource = { status, data, key: JSON.stringify(filters), error: 'Source unavailable' };
    const view = dashboardResourceView(resource, filters);
    assert.equal(view.data, data);
    assert.equal(view.status, status);
    assert.equal(view.error, status === 'stale' ? resource.error : '');
    assert.equal(dashboardResourceView(resource, { ...filters, terminal: 'cua_lo' }).data, null);
    assert.equal(dashboardResourceView({ ...resource, data: {} }, filters).status, 'recovering');
  }
});

test('previous month handles year boundary and leap February', () => {
  assert.deepEqual(presetDates('previous', '2026-01-09'), { start_date: '2025-12-01', end_date: '2025-12-31' });
  assert.deepEqual(presetDates('previous', '2024-03-05'), { start_date: '2024-02-01', end_date: '2024-02-29' });
  assert.deepEqual(presetDates('year', '2026-09-09'), { start_date: '2026-01-01', end_date: '2026-09-09' });
});

test('invalid, reversed, future and overlong ranges are rejected before fetch', () => {
  const validate = (overrides) => validateFilters({ ...filters, ...overrides }, '2026-09-09');
  assert.equal(validate({}), '');
  assert.ok(validate({ start_date: '2026-02-30' }));
  assert.ok(validate({ start_date: '' }));
  assert.ok(validate({ start_date: '2026-09-09', end_date: '2026-09-08' }));
  assert.ok(validate({ end_date: '2026-09-10' }));
  assert.ok(validate({ start_date: '2025-09-08' }));
  assert.equal(validate({ start_date: '2025-09-09' }), '');
  assert.ok(validate({ terminal: 'invalid' }));
});

test('zero and unknown numbers stay distinct; timestamps honor source timezone', () => {
  assert.equal(formatNumber(0), '0');
  assert.equal(formatNumber(null), '—');
  assert.equal(formatNumber(NaN), '—');
  assert.equal(formatTimestamp('2026-09-09T07:00:00'), formatTimestamp('2026-09-09T00:00:00Z'));
  assert.equal(formatTimestamp(null), 'Chưa có dữ liệu');
  assert.equal(formatTimestamp('broken'), 'Chưa xác định');
  assert.equal(formatTimestamp({ date: '2026-09-09' }), 'Chưa xác định');
  assert.equal(formatTimestamp(42), 'Chưa xác định');
});

test('quantity display preserves three decimals, small values and signed adjustments', () => {
  assert.equal(formatNumber(100.499), '100,499');
  assert.equal(formatNumber(0.04), '0,04');
  assert.equal(formatNumber(-0.04), '-0,04');
  assert.equal(formatNumber(21.1), '21,1');
  assert.equal(formatNumber(12, 0), '12');
});

test('ratio graphics are unavailable for signed, missing or inconsistent inputs', () => {
  assert.equal(ratioAvailability([120, -20], 100).available, false);
  assert.match(ratioAvailability([120, -20], 100).reason, /điều chỉnh/);
  assert.equal(ratioAvailability([80, 20], 100, true).available, false);
  for (const [values, total] of [[[0], 0], [[0], null], [[1], -1], [[120], 100], [[null], 100]]) assert.equal(ratioAvailability(values, total).available, false);
  assert.equal(ratioAvailability([80, 20], 100).available, true);
});

test('current day is provisional only when inside the selected period', () => {
  assert.equal(isCurrentDayPeriod(filters, '2026-09-09'), true);
  assert.equal(isCurrentDayPeriod(filters, '2026-09-10'), false);
  assert.equal(isCurrentDayPeriod({ start_date: '2026-09-10', end_date: '2026-09-09' }, '2026-09-09'), false);
});

test('valid and truly empty responses are accepted', () => {
  assert.equal(validateDashboard(fixture(), filters).overview.total_tonnage, 100);
  const empty = fixture();
  empty.overview = { total_tonnage: 0, total_teu: 0, vessel_calls: 0, record_count: 0 };
  empty.meta.status = 'empty';
  empty.voyages = [];
  for (const key of ['cargo', 'history', 'terminals', 'directions', 'customers']) empty[key] = [];
  assert.equal(validateDashboard(empty, filters).meta.status, 'empty');
});

test('daily history validates and exports with exact operation date', () => {
  const data = fixture();
  data.daily_history = [{ date: '2026-09-09', tonnage: 100, teu: 2 }];
  assert.equal(validateDashboard(data, filters).daily_history.length, 1);
  assert.ok(dashboardCsv(data).includes('SẢN LƯỢNG THEO NGÀY'));
  data.daily_history[0].tonnage = null;
  assert.equal(validateDashboard(data, filters).daily_history[0].tonnage, null);
  data.daily_history[0].tonnage = '100';
  assert.throws(() => validateDashboard(data, filters), /cấu trúc/);
  data.daily_history = {};
  assert.throws(() => validateDashboard(data, filters), /cấu trúc/);
});

test('unavailable metrics remain null and native-unit totals remain separate', () => {
  const data = fixture();
  data.overview.total_tonnage = null;
  data.overview.tonnage_status = 'unavailable';
  data.cargo[0].tonnage = null;
  data.cargo[0].value = null;
  data.native_units = [{ terminal_id: 'ben_thuy', terminal_name: 'Bến Thủy', unit_code: 'M3', unit_name: 'Mét khối', value: 42.123, record_count: 2, known_value_rows: 2, status: 'ready' }];
  assert.equal(validateDashboard(data, filters).overview.total_tonnage, null);
  const csv = dashboardCsv(data);
  assert.ok(csv.includes('SẢN LƯỢNG CHƯA CỘNG VÀO TẤN'));
  assert.ok(csv.includes('"M3","Mét khối","42.123"'));
  data.overview.tonnage_status = 'ready';
  assert.throws(() => validateDashboard(data, filters), /cấu trúc/);
});

test('reject a wrong filter response instead of displaying mismatched report', () => {
  const wrong = fixture();
  wrong.meta.filters.terminal = 'ben_thuy';
  assert.throws(() => validateDashboard(wrong, filters), /cấu trúc/);
});

test('dashboard timeout stays 45 seconds for 92 days and allows 90 seconds for valid 93–366 day periods', () => {
  const timeout = (start_date, end_date) => dashboardRequestTimeout({ ...filters, start_date, end_date });
  assert.equal(timeout('2026-07-01', '2026-09-30'), 45000);
  assert.equal(timeout('2026-07-01', '2026-10-01'), 90000);
  assert.equal(timeout('2024-01-01', '2024-12-31'), 90000);
  assert.equal(timeout('2026-01-01', '2026-12-31'), 90000);
  assert.equal(timeout('2026-09-17', '2026-09-17'), 45000);
  assert.equal(timeout('2024-01-01', '2025-01-01'), 45000);
  assert.equal(timeout('2026-09-17', '2026-09-16'), 45000);
  for (const value of [null, undefined, {}, { start_date: '2026-02-30', end_date: '2026-12-31' },
    { start_date: '2026-01-01', end_date: '2026-09-31' }, { start_date: '2026-01-01', end_date: 'invalid' },
    { start_date: '2026-01-01T00:00:00Z', end_date: '2026-12-31' }, { start_date: 20260101, end_date: '2026-12-31' }]) {
    assert.equal(dashboardRequestTimeout(value), 45000);
  }
});

test('quarter presets use full past quarters and clamp only the current quarter to Vietnam today', () => {
  const today = '2026-09-17';
  assert.deepEqual(presetDates('quarter-1', today), { start_date: '2026-01-01', end_date: '2026-03-31' });
  assert.deepEqual(presetDates('quarter-2', today), { start_date: '2026-04-01', end_date: '2026-06-30' });
  assert.deepEqual(presetDates('quarter-3', today), { start_date: '2026-07-01', end_date: today });
  assert.equal(presetDates('quarter-4', today), null);
  assert.deepEqual(presetDates('quarter-4', today, 2025), { start_date: '2025-10-01', end_date: '2025-12-31' });
  assert.deepEqual(presetDates('quarter-1', '2024-03-31'), { start_date: '2024-01-01', end_date: '2024-03-31' });
  const newYear = todayInVietnam(new Date('2025-12-31T17:00:00Z'));
  assert.deepEqual(presetDates('quarter-1', newYear), { start_date: '2026-01-01', end_date: '2026-01-01' });
  for (const [preset, year] of [['quarter-2', 2026], ['quarter-0', 2025], ['quarter-5', 2025], ['quarter-1', 1999], ['quarter-1', 2100], ['quarter-1', 'invalid']]) assert.equal(presetDates(preset, newYear, year), null);
  const scoped = { ...filters, ...presetDates('quarter-2', today), production_scope: 'vietsun', terminal: 'cua_lo' };
  assert.equal(validateFilters(scoped, today), '');
  assert.equal(scoped.production_scope, 'vietsun');
  assert.equal(scoped.terminal, 'cua_lo');
});

test('production scope and initial berth version must match even when dates and terminal agree', () => {
  for (const mutate of [
    (data) => { delete data.meta.filters.production_scope; },
    (data) => { data.meta.filters.production_scope = 'vietsun'; },
    (data) => { delete data.meta.berth_rule_version; },
    (data) => { data.meta.berth_rule_version = 'operation-berth-v0'; },
    (data) => { data.voyages[0].production_scope = 'vietsun'; },
    (data) => { delete data.voyages[0].production_scope; },
  ]) {
    const data = fixture(); mutate(data);
    assert.throws(() => validateDashboard(data, filters));
    assert.equal(dashboardResourceView({ status: 'stale', key: JSON.stringify(filters), data }, filters).data, null);
  }
  const old = { status: 'success', key: JSON.stringify(filters), data: fixture() };
  assert.equal(dashboardResourceView(old, { ...filters, production_scope: 'vietsun' }).status, 'loading');
});

test('Vietsun and unclassified scope requests accept only the selected scoped response', async () => {
  for (const scope of ['vietsun', 'unclassified']) {
    const selected = { ...filters, production_scope: scope };
    const data = fixture();
    data.meta.filters = selected;
    data.voyages[0].production_scope = scope;
    let request;
    await fetchDashboard(selected, { fetcher: async (url) => { request = url; return { ok: true, json: async () => data }; } });
    assert.equal(new URL(request, 'http://localhost').searchParams.get('production_scope'), scope);
  }
  assert.ok(validateFilters({ ...filters, production_scope: 'all' }, '2026-09-09'));
  assert.ok(validateFilters({ ...filters, production_scope: undefined }, '2026-09-09'));
});

test('CSV includes selected production scope and original berth evidence without altering totals', () => {
  const data = fixture();
  data.meta.filters.production_scope = data.voyages[0].production_scope = 'vietsun';
  Object.assign(data.voyages[0], { initial_berth_id: 13, initial_berth_code: 'C5', initial_berth_at: '2026-08-31T07:00:00' });
  const csv = dashboardCsv(data);
  assert.ok(csv.includes('"Cầu 5","Mã phạm vi","vietsun"'));
  assert.ok(csv.includes('"13","C5","2026-08-31T07:00:00","assigned"'));
  assert.ok(csv.includes('initial-berth-v1'));
  assert.equal(data.overview.total_tonnage, 100);
});

test('malformed numbers, collections and renderable metadata fail visibly', () => {
  for (const mutate of [
    (data) => { data.overview = null; },
    (data) => { data.overview.total_tonnage = '100'; },
    (data) => { data.cargo = null; },
    (data) => { data.history[0].teu = Infinity; },
    (data) => { data.meta.warnings = [null]; },
    (data) => { data.meta.sources = [null]; },
    (data) => { data.meta.definitions = { tonnage: {} }; },
    (data) => { data.meta.generated_at = {}; },
    (data) => { data.meta.sources = [{ name: 'Cửa Lò', latest_operation_at: {} }]; },
    (data) => { data.meta.previous_period = { label: {} }; },
    (data) => { data.customers[0].terminal_name = {}; },
  ]) {
    const data = fixture();
    mutate(data);
    assert.throws(() => validateDashboard(data, filters), /cấu trúc/);
  }
});

test('fetch sends one complete filter set and passes cancellation signal', async () => {
  const controller = new AbortController();
  let requested;
  const fetcher = async (url, options) => {
    requested = { url, options };
    return { ok: true, json: async () => fixture() };
  };
  await fetchDashboard(filters, { fetcher, signal: controller.signal, baseUrl: '/api/' });
  assert.equal(requested.url, '/api/dashboard?start_date=2026-09-01&end_date=2026-09-09&terminal=all&production_scope=nghe_tinh');
  assert.equal(requested.options.signal, controller.signal);
});

test('HTTP 503 cannot become empty or fabricated data', async () => {
  await assert.rejects(fetchDashboard(filters, { fetcher: async () => ({ ok: false, status: 503 }) }), /không thể truy vấn/);
});

test('dashboard recovers from a transient 503 with a fresh validated payload', async () => {
  let attempts = 0;
  const data = fixture();
  const result = await fetchDashboard(filters, {
    fetcher: async () => ++attempts === 1
      ? { ok: false, status: 503 }
      : { ok: true, json: async () => data },
  });
  assert.equal(attempts, 2);
  assert.equal(result, data);
});

test('dashboard does not retry malformed JSON or mismatched report data', async () => {
  const wrongPeriod = fixture();
  wrongPeriod.meta.filters.start_date = '2026-08-01';
  for (const json of [async () => { throw new SyntaxError('Invalid JSON'); }, async () => wrongPeriod]) {
    let attempts = 0;
    await assert.rejects(fetchDashboard(filters, {
      fetcher: async () => { attempts += 1; return { ok: true, json }; },
    }));
    assert.equal(attempts, 1);
  }
});

test('retained pre-voyage payload is hidden and requests recovery, never a false empty list', () => {
  const data = fixture();
  delete data.voyages;
  const resource = { key: JSON.stringify(filters), status: 'success', data, error: '' };
  const view = dashboardResourceView(resource, filters);
  assert.equal(view.status, 'recovering');
  assert.equal(view.data, null);
  assert.match(view.error, /chuyến tàu/);
  data.voyages = [];
  assert.equal(dashboardResourceView(resource, filters).status, 'recovering');
  const fresh = fixture();
  assert.equal(dashboardResourceView({ ...resource, data: fresh }, filters).data, fresh);
});

test('recovery ends at an explicit fresh-response error and ignores outdated filter state', async () => {
  const data = fixture();
  delete data.voyages;
  await assert.rejects(fetchDashboard(filters, { fetcher: async () => ({ ok: true, json: async () => data }) }), /chuyến tàu/);
  const failed = dashboardResourceView({ key: JSON.stringify(filters), status: 'error', data: null, error: 'Missing voyages' }, filters);
  assert.equal(failed.status, 'error');
  assert.equal(failed.error, 'Missing voyages');
  assert.equal(failed.data, null);
  assert.equal(dashboardResourceView({ key: 'old filters', status: 'success', data }, filters).status, 'loading');
  const empty = fixture();
  empty.voyages = [];
  empty.overview.vessel_calls = 0;
  assert.equal(dashboardResourceView({ key: JSON.stringify(filters), status: 'success', data: empty }, filters).status, 'success');
});

test('abort propagates to caller and never returns fallback numbers', async () => {
  const controller = new AbortController();
  controller.abort();
  const fetcher = async (_url, { signal }) => { signal.throwIfAborted(); };
  await assert.rejects(fetchDashboard(filters, { fetcher, signal: controller.signal }), { name: 'AbortError' });
});

test('CSV quotes names and neutralizes spreadsheet formulas and leading controls', () => {
  assert.equal(csvCell('Công ty "A", B'), '"Công ty ""A"", B"');
  for (const value of ['=SUM(1,2)', '+1', '-1+2', '@SUM(1)', '  =1+1', '\t=1+1', '\r=1+1']) {
    assert.ok(csvCell(value).startsWith('"\''), value);
  }
  assert.equal(csvCell('Cảng Nghệ Tĩnh'), '"Cảng Nghệ Tĩnh"');
  assert.equal(csvCell(-12.5), '"-12.5"');
});

test('export retains filter context, metric unit, provenance and warnings', () => {
  const csv = dashboardCsv(fixture());
  assert.ok(csv.startsWith('\uFEFF'));
  assert.ok(csv.includes('2026-09-01'));
  assert.ok(csv.includes('Tấn'));
  assert.ok(csv.includes('CHUYẾN TÀU TRONG KỲ'));
  assert.ok(csv.includes('Số dòng tác nghiệp'));
  assert.ok(!csv.includes('Số phiếu'));
  assert.ok(csv.includes('Quy tắc chưa đối soát'));
  assert.ok(csv.includes('"\'=HYPERLINK'));
  assert.ok(csv.includes('Cửa Lò'));
  assert.ok(csv.includes('\r\n'));
});

test('CSV preserves signed amounts and omits invalid share values', () => {
  const data = fixture();
  data.cargo = [{ name: 'positive', tonnage: 120, value: 120 }, { name: 'adjustment', tonnage: -20, value: -20 }];
  data.meta.data_quality = { negative_value_count: 1 };
  const csv = dashboardCsv(data);
  assert.ok(csv.includes('"positive","120",""'));
  assert.ok(csv.includes('"adjustment","-20",""'));
  assert.ok(csv.includes('Không tính tỷ trọng với dữ liệu điều chỉnh.'));
});
