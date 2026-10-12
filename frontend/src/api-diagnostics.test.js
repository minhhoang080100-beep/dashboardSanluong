import assert from 'node:assert/strict';
import test from 'node:test';
import { createDiagnosticsRequestTracker, diagnosticMeasurement, diagnosticReason, diagnosticValue, diagnosticsRequest, validateDiagnostics } from './api-diagnostics.js';

const resource = { id: 's.cargoThroughput', filters: [{ name: 'startDate' }, { name: 'endDate' }] };
const query = { companyId: 'CNT', startDate: '20261001', endDate: '20261012', page: '1', limit: '20' };
const failed = { resource: resource.id, statusCode: 503, requestQuery: query };
const fixture = () => ({ resource: 's.cargoType', blockers: [{ code: 'CARGO_KIND_UNMAPPED', count: 3 }, { code: 'SOURCE_DATA_NOT_READY', count: null }],
  samples: [{ code: 'CARGO_KIND_UNMAPPED', terminal: 'cua_lo', sourceId: '123', cargoId: '0', date: '2026-10-01' }], samplesTruncated: true });

test('diagnostics repeats the failed request as a copy without introducing form values or credentials', () => {
  const request = diagnosticsRequest(resource, failed);
  assert.deepEqual(request, { resource: resource.id, query });
  assert.notEqual(request.query, query);
  request.query.startDate = '20260901';
  assert.equal(failed.requestQuery.startDate, '20261001');
  for (const key of ['accessToken', 'password', 'Authorization', 'unsubmittedFilter']) {
    assert.throws(() => diagnosticsRequest(resource, { ...failed, requestQuery: { ...query, [key]: 'never-copy' } }), /chưa hợp lệ/);
  }
  assert.throws(() => diagnosticsRequest({ ...resource, filters: [{ name: 'accessToken' }] }, { ...failed, requestQuery: { accessToken: 'never-copy' } }), /chưa hợp lệ/);
  assert.throws(() => diagnosticsRequest(resource, { ...failed, statusCode: 200 }), /Chưa có yêu cầu/);
  assert.throws(() => diagnosticsRequest(resource, { ...failed, resource: 'another-resource' }), /Chưa có yêu cầu/);
});

test('diagnostics accepts dependent resource and absent IDs without fabricating zero counts', () => {
  const result = validateDiagnostics(fixture());
  assert.equal(result.resource, 's.cargoType');
  assert.equal(result.blockers[1].count, null);
  assert.equal(result.samples[0].cargoId, '0');
  assert.equal(result.samples[0].methodId, undefined);
  assert.equal(diagnosticValue('sourceId', undefined), 'Chưa có thông tin');
  assert.equal(diagnosticValue('terminal', 'ben_thuy'), 'Bến Thủy');
  assert.equal(validateDiagnostics(undefined), null);
  assert.equal(diagnosticReason('FUTURE_UNKNOWN_CODE'), 'Cần kiểm tra dữ liệu nguồn');
});

test('diagnostics returns only explicitly approved fields and rejects malformed source identifiers', () => {
  const raw = fixture();
  raw.accessToken = 'never-copy'; raw.blockers[0].message = 'raw exception';
  raw.samples[0].password = 'never-copy'; raw.samples[0].unit = 'raw source';
  const serialized = JSON.stringify(validateDiagnostics(raw));
  assert.doesNotMatch(serialized, /never-copy|raw exception|raw source/);
  for (const invalid of [null, [], '123<script>', '<img src=x onerror=alert(1)>', 123]) {
    assert.throws(() => validateDiagnostics({ ...fixture(), samples: [{ ...fixture().samples[0], sourceId: invalid }] }), /chưa hợp lệ/);
  }
  assert.throws(() => validateDiagnostics({ ...fixture(), samples: [{ ...fixture().samples[0], date: '2026-02-30' }] }), /Ngày/);
  assert.throws(() => validateDiagnostics({ ...fixture(), samples: [{ ...fixture().samples[0], code: 'UNRELATED_CODE' }] }), /chưa hợp lệ/);
  assert.throws(() => validateDiagnostics({ ...fixture(), blockers: [{ code: 'CARGO_KIND_UNMAPPED', count: -1 }] }), /chưa hợp lệ/);
});

test('diagnostics caps row samples and conflict IDs while preserving full known counts', () => {
  const result = validateDiagnostics({ ...fixture(), samples: Array.from({ length: 60 }, () => fixture().samples[0]), samplesTruncated: false,
    conflictIds: Array.from({ length: 60 }, (_, index) => String(index + 1)), conflictCount: 60 });
  assert.equal(result.samples.length, 50);
  assert.equal(result.samplesTruncated, true);
  assert.equal(result.conflictIds.length, 50);
  assert.equal(result.conflictCount, 60);
});

test('native measurements preserve decimals and units without inventing a conversion', () => {
  const raw = fixture();
  raw.samples[0] = { ...raw.samples[0], nativeWeight: '0', weightUnit: 'TAN', quantity: '500.125', quantityUnit: 'GÀU' };
  const sample = validateDiagnostics(raw).samples[0];
  assert.equal(diagnosticMeasurement(sample.quantity, sample.quantityUnit), '500.125 GÀU');
  assert.equal(diagnosticMeasurement(sample.nativeWeight, sample.weightUnit), '0 TAN');
  assert.equal(diagnosticMeasurement(undefined, 'M3'), 'Chưa có số liệu M3');
  for (const value of ['-1', '1e3', '1000000000000001', '0.123456789012345678901234567890123456789', 500]) {
    assert.throws(() => validateDiagnostics({ ...raw, samples: [{ ...sample, quantity: value }] }), /Số liệu gốc/);
  }
  assert.throws(() => validateDiagnostics({ ...raw, samples: [{ ...sample, quantityUnit: 'RAW PRIVATE TEXT' }] }), /Đơn vị gốc/);
});

test('cancelled or superseded diagnostics cannot publish a stale response even if transport resolves later', async () => {
  const tracker = createDiagnosticsRequestTracker();
  const first = tracker.begin();
  const second = tracker.begin();
  assert.equal(first.signal.aborted, true);
  const publish = async (pending) => { await Promise.resolve(); return pending.current() ? 'publish' : 'ignore'; };
  assert.equal(await publish(first), 'ignore');
  assert.equal(await publish(second), 'publish');
  tracker.cancel();
  assert.equal(second.signal.aborted, true);
  assert.equal(await publish(second), 'ignore');
  assert.equal(tracker.begin().current(), true);
});
