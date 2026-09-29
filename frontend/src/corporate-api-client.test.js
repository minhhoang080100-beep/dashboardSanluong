import assert from 'node:assert/strict';
import test from 'node:test';
import { getEventListeners } from 'node:events';
import { inspectCorporateApi, loginCorporateApi, logoutCorporateApi } from './corporate-api-client.js';
import { getSessionToken, setSessionToken } from './api-client.js';
import { inspectorPagination, validateInspection } from './api-inspector.js';

const token = 'M'.repeat(43);
const credentials = { username: ' api-test ', password: '  private-password\t' };
const resource = { id: 'cargoType', path: '/api/cargoType' };
const query = { companyId: 'CNT', page: '1', limit: '20' };
const envelope = { data: [], code: '1', message: 'OK' };
const loginBody = { accessToken: token, expiresIn: 3600, code: '1', message: 'OK' };
const login = (fetcher, options = {}) => loginCorporateApi(credentials, { fetcher, ...options });
const inspect = (fetcher, options = {}) => inspectCorporateApi(resource, query, { accessToken: token, fetcher, ...options });

function safeFailure(error, code, status) {
  assert.equal(error.code, code);
  if (status !== undefined) assert.equal(error.status, status);
  assert.equal(Object.hasOwn(error, 'cause'), false);
  assert.equal(Object.hasOwn(error, 'detail'), false);
  assert.doesNotMatch(`${error.message} ${JSON.stringify(error)}`, /private-password|raw-server-secret|M{43}/);
  return true;
}

test('login sends exact contract fields and untrimmed password without dashboard authentication', async (t) => {
  setSessionToken('dashboard-session');
  t.after(() => setSessionToken(''));
  const before = Date.now();
  let calls = 0;
  const result = await login(async (url, options) => {
    calls += 1;
    assert.equal(url, '/api/login');
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body), { Username: 'api-test', Password: credentials.password });
    assert.deepEqual(options.headers, { Accept: 'application/json', 'Content-Type': 'application/json' });
    assert.equal(options.credentials, 'omit');
    assert.equal(options.redirect, 'error');
    assert.equal(options.cache, 'no-store');
    return Response.json(loginBody);
  });
  assert.equal(calls, 1);
  assert.equal(result.accessToken, token);
  assert.equal(result.statusCode, 200);
  assert.equal(result.expiresIn, 3600);
  assert.ok(result.expiresAt >= before + 3600000 && result.expiresAt <= Date.now() + 3600000);
  assert.ok(result.durationMs >= 0);
  assert.equal(getSessionToken(), 'dashboard-session');
});

test('API failures neither read or write storage nor expire dashboard session', async (t) => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage');
  const oldWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
  let storageCalls = 0, events = 0;
  setSessionToken('dashboard-still-valid');
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, get() { storageCalls += 1; throw new Error('storage forbidden'); } });
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { dispatchEvent() { events += 1; } } });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'sessionStorage', previous); else delete globalThis.sessionStorage;
    if (oldWindow) Object.defineProperty(globalThis, 'window', oldWindow); else delete globalThis.window;
    setSessionToken('');
  });
  await assert.rejects(login(async () => Response.json({ data: [], code: '0', message: 'raw-server-secret' }, { status: 401 })), (error) => safeFailure(error, 'MACHINE_CREDENTIALS_INVALID', 401));
  const result = await inspect(async () => Response.json({ data: [], code: '0', message: 'Expired' }, { status: 401 }));
  assert.equal(result.statusCode, 401);
  assert.equal(getSessionToken(), 'dashboard-still-valid');
  assert.equal(storageCalls, 0);
  assert.equal(events, 0);
});

test('GET uses the independent bearer, preserves proxy prefix and pins pagination snapshot', async () => {
  const snapshotId = 'a'.repeat(32);
  const next = { ...query, page: '2', snapshotId };
  const result = await inspectCorporateApi(resource, next, {
    accessToken: token, baseUrl: 'https://api.example.invalid/gateway/api///',
    fetcher: async (url, options) => {
      assert.equal(url, `https://api.example.invalid/gateway/api/cargoType?companyId=CNT&page=2&limit=20&snapshotId=${snapshotId}`);
      assert.equal(options.method, 'GET');
      assert.equal(options.credentials, 'omit');
      assert.equal(options.redirect, 'error');
      assert.equal(options.cache, 'no-store');
      assert.deepEqual(options.headers, { Accept: 'application/json', Authorization: `Bearer ${token}` });
      assert.equal(Object.hasOwn(options, 'body'), false);
      return Response.json({ ...envelope, data: [{ id: 'source-id' }] }, { headers: {
        'X-Page': '2', 'X-Limit': '20', 'X-Total-Count': '21', 'X-Has-Next': 'false', 'X-Snapshot-Id': snapshotId,
        Authorization: 'must-not-be-copied', 'Set-Cookie': 'secret', 'X-Unrecognized': 'private',
      } });
    },
  });
  assert.equal(validateInspection(result, resource, next), result);
  assert.deepEqual(result.requestQuery, next);
  assert.notEqual(result.requestQuery, next);
  assert.equal(inspectorPagination(result).valid, true);
  assert.equal(inspectorPagination(result).hasNext, false);
  assert.deepEqual(Object.keys(result.headers).sort(), ['X-Has-Next', 'X-Limit', 'X-Page', 'X-Snapshot-Id', 'X-Total-Count'].sort());
});

test('GET preserves normal JSON HTTP error bodies, error codes and Retry-After for inspection', async () => {
  for (const status of [401, 403, 429, 503]) {
    const body = { data: [], code: '0', message: `Synthetic API error ${status}` };
    const result = await inspect(async () => Response.json(body, { status, headers: { 'X-Error-Code': 'DATASET_NOT_READY', 'Retry-After': '30' } }));
    assert.equal(result.statusCode, status);
    assert.deepEqual(result.body, body);
    assert.equal(result.headers['X-Error-Code'], 'DATASET_NOT_READY');
    assert.equal(result.headers['Retry-After'], '30');
    assert.equal(validateInspection(result, resource, query), result);
  }
});

test('login recognizes disabled API and safe status errors without displaying response text', async () => {
  for (const [status, header, code] of [
    [503, 'CORPORATE_API_DISABLED', 'CORPORATE_API_DISABLED'],
    [401, 'MACHINE_CREDENTIALS_INVALID', 'MACHINE_CREDENTIALS_INVALID'],
    [403, 'unknown-raw-server-secret', 'API_FORBIDDEN'],
    [429, null, 'MACHINE_LOGIN_THROTTLED'],
    [503, null, 'API_UNAVAILABLE'],
    [422, 'INVALID_REQUEST', 'INVALID_REQUEST'],
    [500, null, 'API_LOGIN_FAILED'],
  ]) {
    await assert.rejects(login(async () => Response.json({ detail: credentials, accessToken: token, message: 'raw-server-secret' }, {
      status, headers: header ? { 'X-Error-Code': header } : {},
    })), (error) => safeFailure(error, code, status));
  }
});

test('malformed and HTML responses use explicit safe local errors', async () => {
  for (const status of [200, 502]) {
    const fetcher = async () => new Response(`<html>raw-server-secret ${credentials.password} ${token}</html>`, { status });
    await assert.rejects(login(fetcher), (error) => safeFailure(error, status === 200 ? 'INVALID_API_RESPONSE' : 'API_LOGIN_FAILED', status));
    await assert.rejects(inspect(fetcher), (error) => safeFailure(error, 'INVALID_API_RESPONSE', status));
  }
});

test('login validates success code, opaque token, and bounded integer expiry', async () => {
  for (const changed of [
    { code: '0' }, { code: 1 }, { accessToken: '' }, { accessToken: 'short' }, { accessToken: `${'M'.repeat(42)}!` },
    { accessToken: ` ${token} ` }, { expiresIn: '3600' }, { expiresIn: 0 }, { expiresIn: -1 },
    { expiresIn: 1.5 }, { expiresIn: 28801 }, { expiresIn: null },
  ]) {
    await assert.rejects(login(async () => Response.json({ ...loginBody, ...changed })), (error) => safeFailure(error, 'INVALID_LOGIN_RESPONSE', 200));
  }
  assert.equal((await login(async () => Response.json({ ...loginBody, expiresIn: 28800 }))).expiresIn, 28800);
});

test('login conservatively rejects a token that expired while response body was arriving', async (t) => {
  const now = Date.now();
  let clock = now;
  t.mock.method(Date, 'now', () => clock);
  await assert.rejects(login(async () => ({ status: 200, ok: true, json: async () => { clock += 2000; return { ...loginBody, expiresIn: 1 }; } })), (error) => safeFailure(error, 'MACHINE_TOKEN_INVALID', 401));
});

test('invalid credentials and bearer tokens are rejected before any request', async () => {
  const fetcher = () => { assert.fail('must not fetch invalid input'); };
  for (const value of [{ username: '', password: 'secret' }, { username: 'u', password: '' }, { username: 'u', password: null }, { username: 'a'.repeat(81), password: 'p' }]) {
    await assert.rejects(loginCorporateApi(value, { fetcher }), (error) => safeFailure(error, 'INVALID_CREDENTIALS'));
  }
  for (const accessToken of [undefined, 'dashboard-token', `${token}\n`]) {
    await assert.rejects(inspect(fetcher, { accessToken }), (error) => safeFailure(error, 'MACHINE_TOKEN_INVALID', 401));
  }
});

test('requests stay below configured relative or absolute API base', async () => {
  for (const baseUrl of ['/proxy/api', '/api/', 'https://example.invalid/backend/api/']) {
    const base = baseUrl.replace(/\/+$/, '');
    await login(async (url) => { assert.equal(url, `${base}/login`); return Response.json(loginBody); }, { baseUrl });
    await inspect(async (url) => { assert.equal(url, `${base}/cargoType?companyId=CNT&page=1&limit=20`); return Response.json(envelope); }, { baseUrl });
  }
});

test('resource paths cannot escape configured host, prefix or GET route', async () => {
  const fetcher = () => { assert.fail('must not fetch unsafe path'); };
  for (const path of ['https://other.invalid/api/cargoType', '//other.invalid/api/cargoType', '/api/../login', '/api/%2e%2e/login', '/api/cargoType?x=1', '/api/cargoType#x', '/api/\\other', '/api//other']) {
    await assert.rejects(inspectCorporateApi({ ...resource, path }, query, { accessToken: token, fetcher }), (error) => safeFailure(error, 'INVALID_API_RESOURCE'));
  }
  for (const baseUrl of ['//other.invalid/api', 'javascript:evil', 'api', '/api/../other', '/api?secret=1', '/api#fragment', 'https://user:password@example.invalid/api', 'https://example.invalid/a/../api']) {
    await assert.rejects(login(fetcher, { baseUrl }), (error) => safeFailure(error, 'INVALID_API_BASE'));
  }
});

test('query values are encoded and invalid structured parameters never fetch', async () => {
  const injected = { ...query, cargoTypeId: 'x&companyId=OTHER#fragment' };
  await inspectCorporateApi(resource, injected, { accessToken: token, fetcher: async (url) => {
    const parsed = new URL(url, 'https://example.invalid');
    assert.equal(parsed.searchParams.get('cargoTypeId'), injected.cargoTypeId);
    assert.equal(parsed.searchParams.get('companyId'), 'CNT');
    assert.equal(parsed.hash, '');
    return Response.json(envelope);
  } });
  for (const value of [null, [], { nested: {} }, { invalid: NaN }, { 'x=y': 'z' }]) {
    await assert.rejects(inspectCorporateApi(resource, value, { accessToken: token, fetcher: () => assert.fail('invalid query') }), (error) => safeFailure(error, 'INVALID_API_QUERY'));
  }
});

test('network and redirect failures are safe and login never retries', async () => {
  let calls = 0;
  const fetcher = async () => { calls += 1; throw new TypeError(`raw-server-secret ${credentials.password} ${token}`); };
  await assert.rejects(login(fetcher), (error) => safeFailure(error, 'NETWORK_ERROR'));
  assert.equal(calls, 1);
  await assert.rejects(inspect(fetcher), (error) => safeFailure(error, 'NETWORK_ERROR'));
  assert.equal(calls, 2);
});

test('deadline aborts both fetch and stalled JSON bodies, including unsuccessful login', async () => {
  for (const request of [login, inspect]) for (const stage of ['fetch', 'body', 'error-body']) {
    let calls = 0, requestSignal;
    const fetcher = async (_url, options) => {
      calls += 1; requestSignal = options.signal;
      if (stage === 'fetch') return new Promise(() => {});
      return { status: stage === 'error-body' ? 503 : 200, ok: stage !== 'error-body', json: () => new Promise(() => {}) };
    };
    await assert.rejects(request(fetcher, { timeoutMs: 5 }), (error) => error.name === 'TimeoutError' && safeFailure(error, 'REQUEST_TIMEOUT'));
    assert.equal(requestSignal.aborted, true);
    assert.equal(calls, 1);
  }
});

test('pre-cancelled requests never fetch or expose external abort reasons', async () => {
  const controller = new AbortController();
  controller.abort(new Error(`raw-server-secret ${credentials.password}`));
  for (const request of [login, inspect]) {
    await assert.rejects(request(() => assert.fail('cancelled'), { signal: controller.signal }), (error) => error.name === 'AbortError' && safeFailure(error, 'REQUEST_ABORTED'));
  }
});

test('external cancellation covers body reading and detaches listeners', async () => {
  for (const request of [login, inspect]) {
    const controller = new AbortController();
    let started, requestSignal;
    const bodyStarted = new Promise((resolve) => { started = resolve; });
    const pending = request(async (_url, options) => {
      requestSignal = options.signal;
      return { ok: true, status: 200, json: () => { started(); return new Promise(() => {}); } };
    }, { signal: controller.signal });
    await bodyStarted;
    controller.abort(new Error('raw-server-secret'));
    await assert.rejects(pending, (error) => error.name === 'AbortError' && safeFailure(error, 'REQUEST_ABORTED'));
    assert.equal(requestSignal.aborted, true);
    assert.equal(getEventListeners(controller.signal, 'abort').length, 0);
  }
});

test('logout revokes only independent API bearer with no dashboard token or storage', async (t) => {
  setSessionToken('dashboard-valid');
  t.after(() => setSessionToken(''));
  const result = await logoutCorporateApi({ accessToken: token, baseUrl: '/proxy/api', fetcher: async (url, options) => {
    assert.equal(url, '/proxy/api/logout');
    assert.equal(options.method, 'POST');
    assert.deepEqual(options.headers, { Accept: 'application/json', Authorization: `Bearer ${token}` });
    assert.equal(Object.hasOwn(options, 'body'), false);
    assert.equal(options.credentials, 'omit');
    assert.equal(options.redirect, 'error');
    return Response.json(envelope);
  } });
  assert.equal(result.statusCode, 200);
  assert.ok(result.durationMs >= 0);
  assert.equal(getSessionToken(), 'dashboard-valid');
});

test('logout requires confirmed success and safely rejects expired or malformed responses', async () => {
  for (const [status, body, code] of [
    [401, { code: '0', message: 'raw-server-secret' }, 'MACHINE_TOKEN_INVALID'],
    [503, { code: '0', message: 'raw-server-secret' }, 'API_LOGOUT_FAILED'],
    [200, { code: '0', data: [], message: 'raw-server-secret' }, 'INVALID_LOGOUT_RESPONSE'],
    [200, { code: '1', data: {}, message: 'OK' }, 'INVALID_LOGOUT_RESPONSE'],
  ]) {
    await assert.rejects(logoutCorporateApi({ accessToken: token, fetcher: async () => Response.json(body, { status }) }), (error) => safeFailure(error, code, status));
  }
});
