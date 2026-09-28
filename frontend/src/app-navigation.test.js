import test from 'node:test';
import assert from 'node:assert/strict';
import { canInspectApis, permittedHash } from './app-navigation.js';

test('API inspector navigation requires an admin with both terminal scopes', () => {
  assert.equal(canInspectApis({ role: 'admin', terminals: ['ben_thuy', 'cua_lo'] }), true);
  for (const user of [null, {}, { role: 'admin' }, { role: 'admin', terminals: ['cua_lo'] },
    { role: 'admin', terminals: ['ben_thuy'] }, { role: 'admin', terminals: 'cua_lo,ben_thuy' },
    { role: 'manager', terminals: ['cua_lo', 'ben_thuy'] }, { role: 'viewer', terminals: ['cua_lo', 'ben_thuy'] }]) {
    assert.equal(canInspectApis(user), false);
    assert.equal(permittedHash('#api-inspector', user), '#overview');
  }
});

test('API deep links survive only while the required access remains available', () => {
  const user = { role: 'admin', terminals: ['cua_lo', 'ben_thuy'] };
  assert.equal(permittedHash('#api-inspector', user), '#api-inspector');
  assert.equal(permittedHash('#api-inspector', { ...user, terminals: ['cua_lo'] }), '#overview');
  assert.equal(permittedHash('#admin', { ...user, terminals: ['cua_lo'] }), '#admin');
  assert.equal(permittedHash('#voyages', { role: 'viewer' }), '#voyages');
  assert.equal(permittedHash('#unknown', user), '#overview');
});
