import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildInspectorQuery, dateInput, dateQuery, initialInspectorValues, inspectorCell,
  inspectorColumns, inspectorHeader, inspectorPagination, inspectorRequestAddress,
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

test('copied URL uses configured API host and public path without credentials', () => {
  const value = inspectorRequestAddress(resource, query, { apiBase: 'https://backend.example/api', origin: 'https://dashboard.example' });
  assert.equal(value, 'https://backend.example/api/oprt/catalog/jobMethod?companyId=CNT&startDate=20260901&endDate=20260927&page=1&limit=20');
  assert.equal(inspectorRequestAddress(resource, {}, { apiBase: '/api', origin: 'https://dashboard.example' }), 'https://dashboard.example/api/oprt/catalog/jobMethod');
});

test('table preserves all returned columns and distinguishes null from a missing field', () => {
  assert.deepEqual(inspectorColumns([{ id: '1' }, { id: '2', value: null }]), ['id', 'value']);
  assert.equal(inspectorCell(null), 'null'); assert.equal(inspectorCell(undefined), '—');
  assert.equal(inspectorCell(false), 'false'); assert.equal(inspectorCell({ value: 0 }), '{"value":0}');
  assert.equal(inspectorCell('<img src=x>'), '<img src=x>');
});
