import assert from 'node:assert/strict';
import test from 'node:test';
import { BERTH_RULE_VERSION, berthAssignmentLabel, initialBerthLabel, productionScopeDescription, productionScopeLabel } from './production-scope.js';
import { sameClosedReportScope } from './management-data.js';

test('missing berth and legacy scope are explicit rather than relabelled as Nghệ Tĩnh', () => {
  assert.equal(initialBerthLabel({ initial_berth_id: null, initial_berth_code: null }), 'Chưa xác định');
  assert.equal(initialBerthLabel({ initial_berth_id: 13, initial_berth_code: null }), 'ID 13');
  assert.equal(initialBerthLabel({ initial_berth_id: 13, initial_berth_code: 'C5' }), 'C5');
  assert.equal(productionScopeLabel(null), 'Chưa lưu phạm vi');
  assert.equal(productionScopeLabel('toString'), 'Chưa lưu phạm vi');
  assert.equal(initialBerthLabel({ initial_berth_code: {}, initial_berth_id: {} }), 'Chưa xác định');
  assert.equal(productionScopeLabel('nghe_tinh'), 'Cảng Nghệ Tĩnh');
  assert.equal(productionScopeLabel('vietsun'), 'Cầu 5');
  assert.equal(productionScopeLabel('unclassified'), 'Chưa xác định cầu');
  assert.equal(berthAssignmentLabel('missing'), 'Chưa có dữ liệu cầu đầu');
  assert.match(productionScopeDescription('unclassified'), /giữ riêng để đối soát/);
  assert.match(productionScopeDescription('unclassified'), /chưa cộng vào Cảng Nghệ Tĩnh hoặc Cầu 5/);
  assert.equal(productionScopeDescription(null), '');
  assert.equal(productionScopeDescription('toString'), '');
});

test('closed-report comparison requires identical period, terminal, scope and berth rule', () => {
  const filters = { start_date: '2026-09-01', end_date: '2026-09-13', terminal: 'all', production_scope: 'nghe_tinh' };
  const report = { meta: { filters, berth_rule_version: BERTH_RULE_VERSION } };
  const item = { ...filters, berth_rule_version: BERTH_RULE_VERSION };
  assert.equal(sameClosedReportScope(item, report), true);
  for (const changed of [
    { production_scope: 'vietsun' }, { production_scope: null }, { production_scope: undefined },
    { berth_rule_version: null }, { berth_rule_version: 'other' }, { terminal: 'cua_lo' }, { end_date: '2026-09-12' },
  ]) assert.equal(sameClosedReportScope({ ...item, ...changed }, report), false);
  assert.equal(sameClosedReportScope(item, { meta: { filters } }), false);
});
