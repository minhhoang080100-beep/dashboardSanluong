import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildInspectorQuery, dateInput, dateQuery, initialInspectorValues, inspectorCell,
  inspectorColumns, inspectorHeader, inspectorPagination, inspectorRequestAddress, inspectorStatusLabel,
  validateInspection, validateInspectorCatalog,
} from './api-inspector.js';

const snapshot = 'a'.repeat(32);
const fields = [{ name: 'startDate', label: 'Từ ngày', type: 'date', required: true }, { name: 'endDate', label: 'Đến ngày', type: 'date', required: true }];
const resource = { id: 'oprt.jobMethod', label: 'Phương án', group: 'operations', method: 'GET', path: '/api/oprt/catalog/jobMethod', filters: fields, status: 'published', coverage: [] };
const values = { startDate: '2026-09-01', endDate: '2026-09-27' };
const query = { companyId: 'CNT', startDate: '20260901', endDate: '20260927', page: '1', limit: '20' };
function response(overrides = {}) {
  return { resource: resource.id, path: resource.path, method: 'GET', requestQuery: query,
    statusCode: 200, durationMs: 2, headers: { 'x-page': '1', 'x-limit': '20', 'x-total-count': '21', 'x-has-next': 'true', 'x-snapshot-id': snapshot },
    body: { data: [{ id: '1' }], code: '1', message: 'OK' }, ...overrides };
}

test('date conversion rejects impossible days and retains leap days', () => {
  assert.equal(dateInput('20240229'), '2024-02-29');
  assert.equal(dateQuery('2024-02-29'), '20240229');
  for (const value of ['20260229', '20260931', '20261301', '202609', '2026-09-01']) assert.equal(dateInput(value), '');
  assert.equal(dateQuery('2026-9-1'), '');
});

test('date defaults use latest valid coverage or the current month in Vietnam', () => {
  const covered = { ...resource, coverage: [['20260101', '20260131'], ['20260901', '20260918'], ['20260999', '20261001'], ['20261201', '20260101']] };
  assert.deepEqual(initialInspectorValues(covered), { startDate: '2026-09-01', endDate: '2026-09-18' });
  assert.deepEqual(initialInspectorValues(resource, new Date('2026-08-31T18:00:00Z')), { startDate: '2026-09-01', endDate: '2026-09-01' });
});

test('live resources start in the current Vietnam month instead of stale published coverage', () => {
  const live = { ...resource, status: 'live', coverage: [['20260101', '20261231']] };
  assert.deepEqual(initialInspectorValues(live, new Date('2026-09-29T08:00:00Z')), { startDate: '2026-09-01', endDate: '2026-09-29' });
  assert.deepEqual(initialInspectorValues(live, new Date('2026-09-30T18:00:00Z')), { startDate: '2026-10-01', endDate: '2026-10-01' });
});

test('query uses only declared filters and a fixed company without leaking unrelated fields', () => {
  assert.deepEqual(buildInspectorQuery(resource, { ...values, password: 'never-copy', companyId: 'OTHER', unknown: 'ignore' }), query);
  const withCompany = { ...resource, filters: [{ name: 'companyId', label: 'Company', type: 'text', required: true }, ...fields] };
  assert.deepEqual(buildInspectorQuery(withCompany, values), query);
  assert.throws(() => buildInspectorQuery(resource, {}), /Vui lòng/);
  assert.throws(() => buildInspectorQuery(resource, { startDate: '2026-09-02', endDate: '2026-09-01' }), /Ngày bắt đầu/);
  assert.throws(() => buildInspectorQuery(resource, { ...values, endDate: '2026-09-31' }), /chưa hợp lệ/);
});

test('pagination requires the previously returned snapshot and approved page sizes', () => {
  assert.throws(() => buildInspectorQuery(resource, values, { page: 2 }), /mã phiên/);
  assert.throws(() => buildInspectorQuery(resource, values, { page: 2, snapshotId: 'invalid' }), /mã phiên/);
  assert.throws(() => buildInspectorQuery(resource, values, { limit: 200 }), /phân trang/);
  assert.deepEqual(buildInspectorQuery(resource, values, { page: 2, snapshotId: snapshot }), { ...query, page: '2', snapshotId: snapshot });
  assert.equal(buildInspectorQuery(resource, values).snapshotId, undefined);
});

test('live page requests use page and limit only, discarding any snapshot from an earlier read', () => {
  const live = { ...resource, status: 'live' };
  assert.deepEqual(buildInspectorQuery(live, values, { page: 2 }), { ...query, page: '2' });
  for (const oldSnapshot of [snapshot, 'expired-or-invalid']) {
    assert.deepEqual(buildInspectorQuery(live, values, { page: 2, snapshotId: oldSnapshot }), { ...query, page: '2' });
    assert.equal(buildInspectorQuery(live, { ...values, snapshotId: oldSnapshot }, { snapshotId: oldSnapshot }).snapshotId, undefined);
  }
  assert.throws(() => buildInspectorQuery(live, values, { page: 0 }), /phân trang/);
  assert.throws(() => buildInspectorQuery(live, values, { limit: 200 }), /phân trang/);
});

test('live production limits each inclusive date range to 31 days without changing input filters', () => {
  const live = { ...resource, status: 'live', group: 'production' };
  const month = { startDate: '2026-01-01', endDate: '2026-01-31' };
  assert.equal(buildInspectorQuery(live, month).endDate, '20260131');
  assert.equal(buildInspectorQuery(live, { startDate: '2024-02-01', endDate: '2024-03-02' }).endDate, '20240302');
  const year = { startDate: '2026-01-01', endDate: '2026-09-29' };
  assert.throws(() => buildInspectorQuery(live, year), /tối đa 31 ngày/);
  assert.deepEqual(year, { startDate: '2026-01-01', endDate: '2026-09-29' });
  assert.throws(() => buildInspectorQuery(live, { ...month, endDate: '2026-02-01' }), /tối đa 31 ngày/);
  assert.throws(() => buildInspectorQuery(live, { startDate: '2024-02-01', endDate: '2024-03-03' }), /tối đa 31 ngày/);
  for (const unrestricted of [{ ...resource, status: 'live' }, { ...resource, group: 'production' }, { ...live, group: 'catalog_s' }]) {
    assert.equal(buildInspectorQuery(unrestricted, year).endDate, '20260929');
  }
});

test('pagination is case insensitive and stops on inconsistent or changed snapshots', () => {
  const page = inspectorPagination(response());
  assert.equal(page.valid, true); assert.equal(page.hasNext, true); assert.equal(page.pages, 2);
  assert.equal(inspectorHeader({ 'X-Error-Code': 'NOT_READY' }, 'x-error-code'), 'NOT_READY');
  const next = response({ requestQuery: { ...query, page: '2', snapshotId: snapshot }, headers: { 'X-Page': '2', 'X-Limit': '20', 'X-Total-Count': '21', 'X-Has-Next': 'false', 'X-Snapshot-Id': snapshot } });
  assert.equal(inspectorPagination(next).valid, true);
  assert.equal(inspectorPagination({ ...next, headers: { ...next.headers, 'X-Snapshot-Id': 'b'.repeat(32) } }).valid, false);
  assert.equal(inspectorPagination({ ...next, headers: { ...next.headers, 'X-Snapshot-Id': 'short' } }).valid, false);
  assert.equal(inspectorPagination({ ...next, headers: { ...next.headers, 'X-Has-Next': 'true' } }).valid, false);
  assert.equal(inspectorPagination({ ...next, statusCode: 503 }).hasNext, false);
});

test('live pagination works without snapshots only when the selected resource uses the live reader', () => {
  const live = { ...resource, status: 'live' };
  const first = response({ headers: { 'X-Page': '1', 'X-Limit': '20', 'X-Total-Count': '21', 'X-Has-Next': 'true', 'X-Source-Read-At': '2026-09-29T01:00:00Z' } });
  assert.equal(inspectorPagination(first, live).valid, true);
  assert.equal(inspectorPagination(first, live).hasNext, true);
  assert.equal(inspectorPagination(first, live).snapshotId, null);
  assert.equal(inspectorPagination(first, resource).valid, false);
  assert.equal(inspectorPagination(first).valid, false);
  const next = response({ requestQuery: { ...query, page: '2' }, headers: { 'X-Page': '2', 'X-Limit': '20', 'X-Total-Count': '22', 'X-Has-Next': 'false', 'X-Source-Read-At': '2026-09-29T01:01:00Z' } });
  assert.equal(inspectorPagination(next, live).valid, true);
  assert.equal(inspectorPagination(next, live).hasNext, false);
  assert.equal(inspectorPagination(next, live).total, 22);
  for (const changed of [
    { ...next, headers: { ...next.headers, 'X-Page': '1' } },
    { ...next, headers: { ...next.headers, 'X-Limit': '50' } },
    { ...next, headers: { ...next.headers, 'X-Has-Next': 'true' } },
    { ...next, headers: { ...next.headers, 'X-Total-Count': undefined } },
    { ...next, statusCode: 503 },
  ]) assert.equal(inspectorPagination(changed, live).valid, false);
});

test('empty success remains distinct from blocked or malformed API results', () => {
  const empty = response({ body: { data: [], code: '1', message: 'OK' }, headers: { 'X-Page': '1', 'X-Limit': '20', 'X-Total-Count': '0', 'X-Has-Next': 'false', 'X-Snapshot-Id': snapshot } });
  assert.equal(validateInspection(empty, resource, query), empty);
  assert.equal(inspectorPagination(empty).valid, true);
  assert.equal(inspectorPagination(empty).total, 0);
  const blocked = response({ statusCode: 422, requestQuery: { companyId: 'CNT' }, body: { data: [], code: '0', message: 'Invalid request' } });
  assert.equal(validateInspection(blocked, resource, query), blocked);
  assert.equal(inspectorPagination(blocked).valid, false);
  assert.throws(() => validateInspection(response({ requestQuery: { ...query, endDate: '20260101' } }), resource, query), /tham số/);
  assert.throws(() => validateInspection(response({ body: { data: {}, code: '1', message: 'OK' } }), resource, query), /chưa hợp lệ/);
  assert.throws(() => validateInspection(response({ resource: 'other' }), resource, query), /chưa hợp lệ/);
});

test('catalog permits only GET API paths and known groups without duplicate IDs or fields', () => {
  const catalog = { enabled: false, companyId: 'CNT', mode: 'internal', resources: [resource] };
  assert.equal(validateInspectorCatalog(catalog), catalog);
  for (const changed of [{ ...resource, method: 'POST' }, { ...resource, path: 'https://external.invalid' }, { ...resource, group: 'unknown' }, { ...resource, filters: [fields[0], fields[0]] }]) {
    assert.throws(() => validateInspectorCatalog({ ...catalog, resources: [changed] }), /chưa hợp lệ/);
  }
  assert.throws(() => validateInspectorCatalog({ ...catalog, resources: [resource, resource] }), /chưa hợp lệ/);
});

test('catalog recognizes live reads while preserving the existing published contract and labels', () => {
  const live = { ...resource, status: 'live', coverage: undefined };
  const catalog = { enabled: true, companyId: 'CNT', mode: 'internal', readMode: 'live', resources: [live] };
  assert.equal(validateInspectorCatalog(catalog), catalog);
  assert.equal(inspectorStatusLabel(live), 'Truy vấn SmartTOS');
  assert.equal(inspectorStatusLabel(resource), 'Có bản công bố');
  assert.equal(inspectorStatusLabel({ ...resource, status: 'not_published' }), 'Chưa công bố');
  assert.equal(validateInspectorCatalog({ ...catalog, readMode: 'published', resources: [resource] }).resources[0], resource);
  for (const changed of [{ ...catalog, readMode: 'unknown' }, { ...catalog, readMode: undefined }, { ...catalog, resources: [resource] }]) {
    assert.throws(() => validateInspectorCatalog(changed), /chưa hợp lệ/);
  }
});

test('copied URL uses configured API host and public path without credentials', () => {
  const value = inspectorRequestAddress(resource, query, { apiBase: 'https://backend.example/api', origin: 'https://dashboard.example' });
  assert.equal(value, 'https://backend.example/api/oprt/catalog/jobMethod?companyId=CNT&startDate=20260901&endDate=20260927&page=1&limit=20');
  assert.equal(inspectorRequestAddress(resource, {}, { apiBase: '/api', origin: 'https://dashboard.example' }), 'https://dashboard.example/api/oprt/catalog/jobMethod');
  assert.equal(inspectorRequestAddress(resource, {}, { apiBase: '/proxy/api/', origin: 'https://dashboard.example' }), 'https://dashboard.example/proxy/api/oprt/catalog/jobMethod');
  assert.equal(inspectorRequestAddress(resource, {}, { apiBase: 'https://backend.example/proxy/api/' }), 'https://backend.example/proxy/api/oprt/catalog/jobMethod');
});

test('table preserves all returned columns and distinguishes null from a missing field', () => {
  assert.deepEqual(inspectorColumns([{ id: '1' }, { id: '2', value: null }]), ['id', 'value']);
  assert.equal(inspectorCell(null), 'null'); assert.equal(inspectorCell(undefined), '—');
  assert.equal(inspectorCell(false), 'false'); assert.equal(inspectorCell({ value: 0 }), '{"value":0}');
  assert.equal(inspectorCell('<img src=x>'), '<img src=x>');
});
