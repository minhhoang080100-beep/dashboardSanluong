import assert from 'node:assert/strict';
import test from 'node:test';
import { canApprovePlan, canCreatePlan, canDeletePlan } from './plan-permissions.js';

test('older administrator and manager responses retain their existing plan rights', () => {
  for (const role of ['admin', 'manager']) {
    assert.equal(canCreatePlan({ role }), true);
    assert.equal(canApprovePlan({ role }), true);
  }
});

test('explicit plan permissions separate creating and approving, including administrators', () => {
  for (const role of ['admin', 'manager']) {
    for (const [plan_permissions, create, approve] of [[[], false, false], [['create'], true, false], [['approve'], false, true], [['create', 'approve'], true, true]]) {
      assert.equal(canCreatePlan({ role, plan_permissions }), create);
      assert.equal(canApprovePlan({ role, plan_permissions }), approve);
    }
  }
});

test('viewer and unknown roles cannot gain plan rights from a permissions array', () => {
  for (const user of [null, undefined, {}, { role: 'viewer' }, { role: 'unknown' }, { role: 'viewer', plan_permissions: ['create', 'approve'] }]) {
    assert.equal(canCreatePlan(user), false);
    assert.equal(canApprovePlan(user), false);
  }
});

test('malformed explicit permission data fails closed rather than activating legacy defaults', () => {
  for (const plan_permissions of [undefined, null, 'create', {}, ['delete'], ['create', 'create'], ['approve', 'unknown']]) {
    assert.equal(canCreatePlan({ role: 'admin', plan_permissions }), false);
    assert.equal(canApprovePlan({ role: 'admin', plan_permissions }), false);
  }
});

test('inactive accounts and accounts awaiting a password change cannot use plan actions', () => {
  for (const changes of [{ is_active: false }, { must_change_password: true }]) {
    assert.equal(canCreatePlan({ role: 'admin', ...changes }), false);
    assert.equal(canApprovePlan({ role: 'manager', plan_permissions: ['approve'], ...changes }), false);
  }
});

test('deleting an approved target requires approval rights while drafts and cancellations require creation rights', () => {
  const creator = { role: 'manager', plan_permissions: ['create'] };
  const approver = { role: 'manager', plan_permissions: ['approve'] };
  for (const status of ['draft', 'cancelled']) {
    assert.equal(canDeletePlan(creator, { status }), true);
    assert.equal(canDeletePlan(approver, { status }), false);
  }
  assert.equal(canDeletePlan(creator, { status: 'approved' }), false);
  assert.equal(canDeletePlan(approver, { status: 'approved' }), true);
  for (const role of ['admin', 'manager']) {
    assert.equal(canDeletePlan({ role }, { status: 'approved' }), true);
    assert.equal(canDeletePlan({ role }, { status: 'draft' }), true);
  }
});

test('server deletion restrictions fail closed and cannot grant rights to a viewer', () => {
  const user = { role: 'admin' };
  for (const can_delete of [false, undefined, null, 1, 'true']) {
    assert.equal(canDeletePlan(user, { status: 'approved', can_delete }), false);
  }
  assert.equal(canDeletePlan(user, { status: 'approved', can_delete: true }), true);
  for (const plan of [null, {}, { status: 'other' }, { status: 'approved', is_deleted: true, can_delete: true }]) {
    assert.equal(canDeletePlan(user, plan), false);
  }
  assert.equal(canDeletePlan({ role: 'viewer' }, { status: 'approved', can_delete: true }), false);
});
