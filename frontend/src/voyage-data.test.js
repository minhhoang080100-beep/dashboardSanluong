import assert from 'node:assert/strict';
import test from 'node:test';
import { fetchVoyageDetail, filterVoyages, paginateVoyages, validateVoyageDetail, validateVoyageList, voyageListError } from './voyage-data.js';

const filters = { start_date: '2026-09-01', end_date: '2026-09-09', terminal: 'all' };
const voyage = { terminal_id: 'cua_lo', terminal_name: 'Cửa Lò', voyage_id: '101', vessel_name: 'TÀU CỬA LÒ', voyage_code: 'CL-09', arrival_at: null, departure_at: null, first_operation_date: '2026-09-01', last_operation_date: '2026-09-09', tonnage: 1000.125, teu: 10, record_count: 2, cargo_names: ['Hàng kiểm thử'] };

function detail() {
  return {
    header: { ...voyage }, summary: { tonnage: 1000.125, teu: 10, record_count: 2 },
    cargo: [{ name: 'Hàng kiểm thử', tonnage: 1000.125, teu: 10 }], daily: [{ date: '2026-09-01', tonnage: 1000.125, teu: 10 }], native_units: [],
    operations: { page: 1, page_size: 25, total: 2, total_pages: 1, rows: [
      { id: '1', operation_date: '2026-09-01', cargo_name: 'Hàng kiểm thử', job_method: 'Tàu - Bãi', direction: 'Hàng dỡ', quantity: null, quantity_unit: null, weight: 1000.125, weight_unit: 'TAN', tonnage: 1000.125, teu: 10 },
      { id: '2', operation_date: '2026-09-01', cargo_name: 'Hàng kiểm thử', job_method: 'Tàu - Bãi', direction: 'Hàng dỡ', quantity: null, quantity_unit: null, weight: null, weight_unit: 'TAN', tonnage: null, teu: 0 },
    ] },
    meta: { filters: { ...filters, terminal: 'cua_lo', voyage_id: '101' }, generated_at: '2026-09-09T00:00:00Z' },
  };
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
  assert.equal(request.url, '/api/voyages/cua_lo/101?start_date=2026-09-01&end_date=2026-09-09&page=1&page_size=25');
  assert.equal(request.options.signal, controller.signal);
});

test('detail 404 and 503 fail visibly without substitute operations', async () => {
  await assert.rejects(fetchVoyageDetail(voyage, filters, 1, { fetcher: async () => ({ ok: false, status: 404 }) }), /Không tìm thấy/);
  await assert.rejects(fetchVoyageDetail(voyage, filters, 1, { fetcher: async () => ({ ok: false, status: 503 }) }), /không truy vấn/);
});
