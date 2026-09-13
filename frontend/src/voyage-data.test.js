import assert from 'node:assert/strict';
import test from 'node:test';
import { fetchVoyageDetail, filterVoyages, paginateVoyages, validateVoyageDetail, validateVoyageList, voyageListError } from './voyage-data.js';

const filters = { start_date: '2026-09-01', end_date: '2026-09-09', terminal: 'all' };
const voyage = { terminal_id: 'cua_lo', terminal_name: 'Cửa Lò', voyage_id: '101', vessel_name: 'TÀU CỬA LÒ', voyage_code: 'CL-09', arrival_at: null, departure_at: null, first_operation_date: '2026-09-01', last_operation_date: '2026-09-09', tonnage: 1000.125, teu: 10, record_count: 2, cargo_names: ['Hàng kiểm thử'] };

function detail(operationFilter = 'all') {
  const data = {
    header: { ...voyage }, summary: { tonnage: 1000.125, teu: 10, record_count: 2 },
    cargo: [{ name: 'Hàng kiểm thử', tonnage: 1000.125, teu: 10 }], daily: [{ date: '2026-09-01', tonnage: 1000.125, teu: 10 }], native_units: [],
    operations: { page: 1, page_size: 25, filter: operationFilter, counts: { all: 2, with_values: 1, missing_weight: 1 }, total_all: 2, total: 2, total_pages: 1, rows: [
      { id: '1', operation_date: '2026-09-01', shift_id: '1', shift_code: '1', cargo_name: 'Hàng kiểm thử', job_method: 'Tàu - Bãi', direction: 'Hàng dỡ', quantity: null, quantity_unit: null, weight: 1000.125, weight_unit: 'TAN', tonnage: 1000.125, teu: 10 },
      { id: '2', operation_date: '2026-09-01', shift_id: null, shift_code: null, cargo_name: 'Hàng kiểm thử', job_method: 'Tàu - Bãi', direction: 'Hàng dỡ', quantity: 0, quantity_unit: null, weight: null, weight_unit: 'TAN', tonnage: null, teu: 0 },
    ] },
    meta: { filters: { ...filters, terminal: 'cua_lo', voyage_id: '101', operation_filter: operationFilter }, generated_at: '2026-09-09T00:00:00Z' },
  };
  if (operationFilter === 'with_values') data.operations.rows = data.operations.rows.slice(0, 1);
  if (operationFilter === 'missing_weight') data.operations.rows = data.operations.rows.slice(1);
  data.operations.total = data.operations.counts[operationFilter];
  return data;
}

test('list remains consistent with KPI and selected terminal, with compound identity', () => {
  assert.equal(validateVoyageList([voyage], 1, filters).length, 1);
  assert.throws(() => validateVoyageList([voyage], 2, filters), /cấu trúc/);
  assert.throws(() => validateVoyageList([voyage, voyage], 2, filters), /cấu trúc/);
  assert.throws(() => validateVoyageList([voyage], 1, { ...filters, terminal: 'ben_thuy' }), /cấu trúc/);
  assert.equal(validateVoyageList([voyage, { ...voyage, terminal_id: 'ben_thuy' }], 2, filters).length, 2);
});

test('missing or mismatched voyage list yields an error, only explicit zero is empty', () => {
  for (const rows of [undefined, null, [], {}]) assert.match(voyageListError(rows, 85, filters), /chưa đầy đủ/);
  assert.match(voyageListError(undefined, 0, filters), /chưa đầy đủ/);
  assert.equal(voyageListError([], 0, filters), '');
  assert.equal(voyageListError([voyage], 1, filters), '');
});

test('search matches Vietnamese names without accents, codes and ID without changing totals', () => {
  const rows = [voyage, { ...voyage, voyage_id: '102', voyage_code: 'BT-09', vessel_name: 'BẾN THỦY' }];
  assert.equal(filterVoyages(rows, 'tau cua lo')[0].voyage_id, '101');
  assert.equal(filterVoyages(rows, 'bt-09')[0].voyage_id, '102');
  assert.equal(filterVoyages(rows, '101')[0].voyage_id, '101');
  assert.equal(filterVoyages(rows, 'absent').length, 0);
  assert.equal(filterVoyages(rows, ' ').length, 2);
  assert.equal(rows.length, 2);
});

test('pagination clamps to valid page after a search reduces results', () => {
  const rows = Array.from({ length: 21 }, (_, index) => ({ ...voyage, voyage_id: String(index) }));
  assert.equal(paginateVoyages(rows, 3).items.length, 1);
  assert.equal(paginateVoyages(rows.slice(0, 1), 3).page, 1);
  assert.equal(paginateVoyages([], 4).totalPages, 0);
});

test('detail preserves missing weights and absent arrival/departure timestamps', () => {
  const result = validateVoyageDetail(detail(), voyage, filters, 1, 25);
  assert.equal(result.header.arrival_at, null);
  assert.equal(result.header.departure_at, null);
  assert.equal(result.operations.rows[1].weight, null);
  assert.equal(result.operations.rows[1].tonnage, null);
});

test('detail rejects mismatched voyage, period, pagination or operation count', () => {
  for (const mutate of [
    (data) => { data.header.voyage_id = '999'; },
    (data) => { data.meta.filters.end_date = '2026-09-08'; },
    (data) => { data.meta.filters.terminal = 'ben_thuy'; },
    (data) => { data.operations.page = 2; },
    (data) => { data.operations.total = 999; },
    (data) => { data.operations.rows[0].weight = {}; },
    (data) => { data.header.arrival_at = {}; },
  ]) {
    const data = detail(); mutate(data);
    assert.throws(() => validateVoyageDetail(data, voyage, filters, 1, 25), /cấu trúc/);
  }
});

test('detail request carries same reporting period, pagination and abort signal', async () => {
  const controller = new AbortController();
  let request;
  const fetcher = async (url, options) => { request = { url, options }; return { ok: true, json: async () => detail() }; };
  await fetchVoyageDetail(voyage, filters, 1, { fetcher, signal: controller.signal, baseUrl: '/api/' });
  assert.equal(request.url, '/api/voyages/cua_lo/101?start_date=2026-09-01&end_date=2026-09-09&page=1&page_size=25&operation_filter=all');
  assert.equal(request.options.signal, controller.signal);
});

test('detail 404 and 503 fail visibly without substitute operations', async () => {
  await assert.rejects(fetchVoyageDetail(voyage, filters, 1, { fetcher: async () => ({ ok: false, status: 404 }) }), /Không tìm thấy/);
  await assert.rejects(fetchVoyageDetail(voyage, filters, 1, { fetcher: async () => ({ ok: false, status: 503 }) }), /không truy vấn/);
});

test('voyage detail recovers from one network failure with the requested page', async () => {
  let attempts = 0;
  const data = detail();
  const result = await fetchVoyageDetail(voyage, filters, 1, {
    fetcher: async () => {
      attempts += 1;
      if (attempts === 1) throw new TypeError('Failed to fetch');
      return { ok: true, json: async () => data };
    },
  });
  assert.equal(attempts, 2);
  assert.equal(result, data);
});

test('voyage detail does not retry malformed JSON or a different voyage payload', async () => {
  const wrongVoyage = detail();
  wrongVoyage.header.voyage_id = '999';
  for (const json of [async () => { throw new SyntaxError('Invalid JSON'); }, async () => wrongVoyage]) {
    let attempts = 0;
    await assert.rejects(fetchVoyageDetail(voyage, filters, 1, {
      fetcher: async () => { attempts += 1; return { ok: true, json }; },
    }));
    assert.equal(attempts, 1);
  }
});

test('operation filters preserve all-source summary while selecting the requested rows', async () => {
  for (const operationFilter of ['all', 'with_values', 'missing_weight']) {
    let request;
    const data = detail(operationFilter);
    const result = await fetchVoyageDetail(voyage, filters, 1, {
      operationFilter,
      fetcher: async (url) => { request = url; return { ok: true, json: async () => data }; },
    });
    assert.equal(new URL(request, 'http://localhost').searchParams.get('operation_filter'), operationFilter);
    assert.equal(result.summary.record_count, 2);
    assert.equal(result.summary.tonnage, 1000.125);
    assert.equal(result.operations.total_all, 2);
    assert.equal(result.operations.total, data.operations.counts[operationFilter]);
    assert.equal(result.operations.rows.length, data.operations.counts[operationFilter]);
    if (operationFilter === 'missing_weight') assert.equal(result.operations.rows[0].weight, null);
  }
});

test('nonzero native quantities and signed adjustments stay eligible with unavailable tonnes', () => {
  const data = detail('all');
  data.operations.filter = data.meta.filters.operation_filter = 'with_values';
  data.operations.rows[1].quantity = -2;
  data.operations.rows[1].quantity_unit = 'M3';
  data.operations.counts.with_values = 2;
  const result = validateVoyageDetail(data, voyage, filters, 1, 25, 'with_values');
  assert.equal(result.operations.rows[1].quantity, -2);
  assert.equal(result.operations.rows[1].tonnage, null);
});

test('an empty filtered first page retains source totals and rejects out-of-range pages', () => {
  const data = detail('with_values');
  data.operations.counts.with_values = 0;
  data.operations.total = data.operations.total_pages = 0;
  data.operations.rows = [];
  data.summary.tonnage = data.header.tonnage = 0;
  data.summary.teu = data.header.teu = 0;
  data.cargo[0].tonnage = data.daily[0].tonnage = 0;
  data.cargo[0].teu = data.daily[0].teu = 0;
  assert.equal(validateVoyageDetail(data, voyage, filters, 1, 25, 'with_values').operations.total_all, 2);
  data.operations.page = 2;
  assert.throws(() => validateVoyageDetail(data, voyage, filters, 2, 25, 'with_values'), /cấu trúc/);
});

test('filtered responses reject incorrect counts, filter, page, row predicates and dates', () => {
  for (const mutate of [
    (data) => { data.operations.filter = 'all'; },
    (data) => { data.meta.filters.operation_filter = 'all'; },
    (data) => { data.meta.filters.voyage_id = '999'; },
    (data) => { data.operations.total_all = 1; },
    (data) => { data.operations.counts.all = 3; },
    (data) => { data.operations.counts.with_values = 3; },
    (data) => { data.operations.counts.missing_weight = -1; },
    (data) => { data.operations.rows[0].quantity = 0; data.operations.rows[0].weight = 0; },
    (data) => { data.operations.rows[0].operation_date = '2026-08-31'; },
    (data) => { data.operations.rows[0].operation_date = '2026-09-31'; },
    (data) => { data.daily[0].date = '2026-10-01'; },
    (data) => { data.operations.rows[0].shift_code = {}; },
  ]) {
    const data = detail('with_values');
    mutate(data);
    assert.throws(() => validateVoyageDetail(data, voyage, filters, 1, 25, 'with_values'), /cấu trúc/);
  }
  const missing = detail('missing_weight');
  missing.operations.rows[0].weight = 0;
  assert.throws(() => validateVoyageDetail(missing, voyage, filters, 1, 25, 'missing_weight'), /cấu trúc/);
  const duplicate = detail();
  duplicate.operations.rows[1].id = duplicate.operations.rows[0].id;
  assert.throws(() => validateVoyageDetail(duplicate, voyage, filters, 1, 25), /cấu trúc/);
});

test('filtered pagination validates the requested last-page slice', () => {
  const data = detail();
  data.operations.page = 2;
  data.operations.page_size = 1;
  data.operations.total_pages = 2;
  data.operations.rows = data.operations.rows.slice(1);
  assert.equal(validateVoyageDetail(data, voyage, filters, 2, 1).operations.rows[0].id, '2');
  assert.throws(() => validateVoyageDetail(data, voyage, filters, 1, 1), /cấu trúc/);
});
