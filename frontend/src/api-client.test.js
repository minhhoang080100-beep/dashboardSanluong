import assert from 'node:assert/strict';
import test from 'node:test';
import { getEventListeners } from 'node:events';
import { apiRequest, authHeaders, downloadFile, getSessionToken, sessionExpired, setSessionToken } from './api-client.js';

const SESSION_KEY = 'port-dashboard-session';

function environment(t, storage) {
  const data = new Map();
  const values = { sessionStorage: storage || { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: (key) => data.delete(key) }, window: new EventTarget() };
  for (const [key, value] of Object.entries(values)) {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, value });
    t.after(() => { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; });
  }
  setSessionToken('');
  return { data, window: values.window };
}

test('sessions hydrate from tab storage and bearer headers follow explicit login/logout', async (t) => {
  const { data } = environment(t);
  data.set(SESSION_KEY, 'synthetic-restored-token');
  const freshClient = await import('./api-client.js?initial-session-test');
  assert.equal(freshClient.getSessionToken(), 'synthetic-restored-token');
  setSessionToken('synthetic-active-token');
  assert.deepEqual(authHeaders(), { Authorization: 'Bearer synthetic-active-token' });
  assert.equal(data.get(SESSION_KEY), 'synthetic-active-token');
  setSessionToken('');
  assert.deepEqual(authHeaders(), {});
  assert.equal(data.has(SESSION_KEY), false);
});

test('JSON requests preserve method, identity, base URL and abort signal; multipart bodies retain browser headers', async (t) => {
  environment(t);
  setSessionToken('synthetic-json-token');
  const signal = new AbortController().signal;
  let captured;
  const fetcher = async (url, options) => { captured = { url, options }; return Response.json({ saved: true }); };
  assert.deepEqual(await apiRequest('/plans', { method: 'POST', body: { amount: 0 }, baseUrl: 'https://example.invalid/api///', signal, fetcher }), { saved: true });
  assert.equal(captured.url, 'https://example.invalid/api/plans');
  assert.equal(captured.options.method, 'POST');
  assert.ok(captured.options.signal instanceof AbortSignal);
  assert.equal(captured.options.signal.aborted, false);
  assert.equal(signal.aborted, false);
  assert.equal(captured.options.body, '{"amount":0}');
  assert.deepEqual(captured.options.headers, { Accept: 'application/json', Authorization: 'Bearer synthetic-json-token', 'Content-Type': 'application/json' });
  const form = new FormData();
  form.append('file', new Blob(['synthetic']), 'plan.xlsx');
  await apiRequest('plans/import/preview', { method: 'POST', body: form, fetcher });
  assert.equal(captured.url, '/api/plans/import/preview');
  assert.equal(captured.options.body, form);
  assert.equal(Object.hasOwn(captured.options.headers, 'Content-Type'), false);
});

test('401 on protected requests clears the session and notifies the auth shell even with a non-JSON error', async (t) => {
  const { window, data } = environment(t);
  let expired = 0;
  window.addEventListener('dashboard-session-expired', () => { expired += 1; });
  setSessionToken('synthetic-expired-token');
  await assert.rejects(apiRequest('/users', { fetcher: async () => new Response('unavailable', { status: 401 }) }), (error) => error.status === 401 && /hết hạn/.test(error.message));
  assert.equal(getSessionToken(), '');
  assert.equal(data.has(SESSION_KEY), false);
  assert.equal(expired, 1);
});

test('only the login endpoint bypasses session expiry, including normalized relative paths', async (t) => {
  const { window } = environment(t);
  let expired = 0;
  window.addEventListener('dashboard-session-expired', () => { expired += 1; });
  setSessionToken('synthetic-existing-token');
  const fetcher = async () => Response.json({ detail: { code: 'INVALID_LOGIN', message: 'Sai thông tin đăng nhập.' } }, { status: 401 });
  for (const path of ['/auth/login', 'auth/login', '/auth/login/']) {
    await assert.rejects(apiRequest(path, { method: 'POST', body: {}, fetcher }), (error) => error.status === 401 && error.detail.code === 'INVALID_LOGIN');
    assert.equal(getSessionToken(), 'synthetic-existing-token');
  }
  assert.equal(expired, 0);
  await assert.rejects(apiRequest('/plans?return=/auth/login', { fetcher }));
  assert.equal(expired, 1);
});

test('403 keeps the current session, preserves structured detail, and malformed errors remain safe', async (t) => {
  environment(t);
  setSessionToken('synthetic-scoped-token');
  await assert.rejects(apiRequest('/users', { fetcher: async () => Response.json({ detail: { code: 'FORBIDDEN', message: 'Phạm vi chưa được cấp.' } }, { status: 403 }) }), (error) => error.status === 403 && error.message === 'Phạm vi chưa được cấp.' && error.detail.code === 'FORBIDDEN');
  assert.equal(getSessionToken(), 'synthetic-scoped-token');
  await assert.rejects(apiRequest('/plans', { fetcher: async () => new Response('bad gateway html', { status: 502 }) }), (error) => error.status === 502 && error.message.includes('HTTP 502') && !error.message.includes('bad gateway'));
});

test('pre-cancelled requests do not fetch; network failures are localized without session expiry or retries', async (t) => {
  environment(t);
  setSessionToken('synthetic-cancel-token');
  const controller = new AbortController();
  const aborted = new DOMException('Cancelled', 'AbortError');
  controller.abort(aborted);
  let calls = 0;
  await assert.rejects(apiRequest('/plans', { signal: controller.signal, fetcher: async () => { calls += 1; } }), (error) => error === aborted);
  const unavailable = new TypeError('Failed to fetch');
  await assert.rejects(apiRequest('/plans', { fetcher: async () => { calls += 1; throw unavailable; } }), (error) => error.cause === unavailable && error.code === 'NETWORK_ERROR' && /Không kết nối/.test(error.message));
  await assert.rejects(apiRequest('/plans', { fetcher: async () => ({ ok: true, status: 200, json: async () => { throw unavailable; } }) }), (error) => error.cause === unavailable && error.code === 'NETWORK_ERROR');
  assert.equal(calls, 1);
  assert.equal(getSessionToken(), 'synthetic-cancel-token');
});

test('empty responses and authenticated raw downloads do not attempt JSON decoding', async (t) => {
  environment(t);
  setSessionToken('synthetic-download-token');
  assert.equal(await apiRequest('/auth/logout', { method: 'POST', fetcher: async () => new Response(null, { status: 204 }) }), null);
  const file = new Response('synthetic workbook bytes');
  const actual = await apiRequest('/closed-reports/1/export.xlsx', { raw: true, fetcher: async (_url, options) => { assert.equal(options.headers.Accept, '*/*'); assert.equal(options.headers.Authorization, 'Bearer synthetic-download-token'); assert.equal(Object.hasOwn(options, 'body'), false); return file; } });
  assert.equal(actual, file);
  assert.equal(await actual.text(), 'synthetic workbook bytes');
  await assert.rejects(apiRequest('/plans', { fetcher: async () => new Response('not json') }), SyntaxError);
});

test('blocked storage supports a tab-local login and explicit session expiry', (t) => {
  const blocked = () => { throw new DOMException('Storage unavailable', 'SecurityError'); };
  environment(t, { getItem: blocked, setItem: blocked, removeItem: blocked });
  setSessionToken('synthetic-memory-token');
  assert.deepEqual(authHeaders(), { Authorization: 'Bearer synthetic-memory-token' });
  sessionExpired();
  assert.equal(getSessionToken(), '');
});

test('failed storage writes cannot resurrect an old token after login or logout', (t) => {
  environment(t, { getItem: () => 'synthetic-old-token', setItem: () => { throw new Error('read-only storage'); }, removeItem: () => { throw new Error('read-only storage'); } });
  setSessionToken('synthetic-new-token');
  assert.equal(getSessionToken(), 'synthetic-new-token');
  sessionExpired();
  assert.deepEqual(authHeaders(), {});
});

function waitForAbort(signal) {
  return new Promise((_, reject) => {
    if (signal.aborted) reject(signal.reason);
    else signal.addEventListener('abort', () => reject(signal.reason), { once: true });
  });
}

test('GET and raw download headers have a deadline that aborts the request once', async (t) => {
  environment(t);
  for (const raw of [false, true]) {
    let calls = 0;
    let requestSignal;
    await assert.rejects(apiRequest('/admin/metrics', { raw, timeoutMs: 5, fetcher: (_url, options) => { calls += 1; requestSignal = options.signal; return waitForAbort(options.signal); } }), (error) => error.name === 'TimeoutError' && error.code === 'REQUEST_TIMEOUT' && /thử tải lại/.test(error.message));
    assert.equal(calls, 1);
    assert.equal(requestSignal.aborted, true);
  }
});

test('POST and PATCH timeout or network failure never retry and require checking saved results', async (t) => {
  environment(t);
  for (const method of ['POST', 'PATCH']) {
    let calls = 0;
    await assert.rejects(apiRequest('/plans', { method, body: { amount: 0 }, timeoutMs: 5, fetcher: (_url, options) => { calls += 1; return waitForAbort(options.signal); } }), (error) => error.code === 'REQUEST_TIMEOUT' && /kiểm tra kết quả/.test(error.message) && /có thể đã lưu/.test(error.message));
    assert.equal(calls, 1);
    await assert.rejects(apiRequest('/plans', { method, body: {}, fetcher: async () => { calls += 1; throw new TypeError('Failed to fetch'); } }), (error) => error.code === 'NETWORK_ERROR' && /kiểm tra kết quả/.test(error.message));
    assert.equal(calls, 2);
  }
});

test('deadline includes JSON body reading for both successful and failed HTTP responses', async (t) => {
  environment(t);
  for (const ok of [true, false]) {
    let requestSignal;
    await assert.rejects(apiRequest('/plans', { timeoutMs: 5, fetcher: async (_url, options) => {
      requestSignal = options.signal;
      return { ok, status: ok ? 200 : 503, json: () => waitForAbort(options.signal) };
    } }), (error) => error.code === 'REQUEST_TIMEOUT');
    assert.equal(requestSignal.aborted, true);
  }
});

test('download deadline covers stalled body even when fetch already returned headers', async (t) => {
  environment(t);
  let requestSignal;
  let calls = 0;
  await assert.rejects(downloadFile('/report.xlsx', 'report.xlsx', { timeoutMs: 5, fetcher: async (_url, options) => {
    calls += 1;
    requestSignal = options.signal;
    assert.equal(options.headers.Accept, '*/*');
    return { ok: true, status: 200, blob: () => new Promise(() => {}) };
  } }), (error) => error.code === 'REQUEST_TIMEOUT');
  assert.equal(calls, 1);
  assert.equal(requestSignal.aborted, true);
});

test('download external cancellation after headers aborts body and detaches listener', async (t) => {
  environment(t);
  const external = new AbortController();
  const reason = new DOMException('Dialog closed', 'AbortError');
  let bodyStarted;
  const started = new Promise((resolve) => { bodyStarted = resolve; });
  let requestSignal;
  const pending = downloadFile('/report.xlsx', 'report.xlsx', { signal: external.signal, fetcher: async (_url, options) => {
    requestSignal = options.signal;
    return { ok: true, status: 200, blob: () => { bodyStarted(); return new Promise(() => {}); } };
  } });
  await started;
  external.abort(reason);
  await assert.rejects(pending, (error) => error === reason);
  assert.equal(requestSignal.reason, reason);
  assert.equal(getEventListeners(external.signal, 'abort').length, 0);
});

test('download only creates a file after all bytes arrive and localizes body network errors', async (t) => {
  environment(t);
  let finish;
  const file = new Blob(['complete workbook bytes']);
  let clicked = 0;
  const link = { click: () => { clicked += 1; }, remove() {} };
  t.mock.method(URL, 'createObjectURL', (blob) => { assert.equal(blob, file); return 'blob:synthetic'; });
  t.mock.method(URL, 'revokeObjectURL', () => {});
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'document');
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { createElement: () => link, body: { appendChild() {} } } });
  t.after(() => { if (descriptor) Object.defineProperty(globalThis, 'document', descriptor); else delete globalThis.document; });
  const pending = downloadFile('/report.xlsx', 'report.xlsx', { fetcher: async () => ({ ok: true, status: 200, blob: () => new Promise((resolve) => { finish = resolve; }) }) });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(clicked, 0);
  finish(file);
  await pending;
  assert.equal(clicked, 1);
  assert.equal(link.download, 'report.xlsx');
  await assert.rejects(downloadFile('/report.xlsx', 'report.xlsx', { fetcher: async () => ({ ok: true, status: 200, blob: async () => { throw new TypeError('Stream reset'); } }) }), (error) => error.code === 'NETWORK_ERROR');
  assert.equal(clicked, 1);
});

test('external cancellation keeps its original reason and removes forwarding listeners', async (t) => {
  environment(t);
  const external = new AbortController();
  const reason = new DOMException('User changed filters', 'AbortError');
  let requestSignal;
  const pending = apiRequest('/reports/current/plan-progress', { signal: external.signal, fetcher: (_url, options) => { requestSignal = options.signal; return waitForAbort(options.signal); } });
  assert.notEqual(requestSignal, external.signal);
  assert.equal(getEventListeners(external.signal, 'abort').length, 1);
  external.abort(reason);
  await assert.rejects(pending, (error) => error === reason);
  assert.equal(requestSignal.reason, reason);
  assert.equal(getEventListeners(external.signal, 'abort').length, 0);
});

test('completed, failed and timed-out requests clear every deadline timer and external listener', async (t) => {
  environment(t);
  const realSet = globalThis.setTimeout;
  const realClear = globalThis.clearTimeout;
  const timers = new Set();
  const delays = [];
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => { const id = realSet(callback, delay); timers.add(id); delays.push(delay); return id; });
  t.mock.method(globalThis, 'clearTimeout', (id) => { timers.delete(id); return realClear(id); });
  t.after(() => { for (const id of timers) realClear(id); });
  const external = new AbortController();
  for (const fetcher of [async () => Response.json({ ok: true }), async () => new Response(null, { status: 204 }), async () => Response.json({ detail: 'Denied' }, { status: 403 }), async () => { throw new TypeError('Failed to fetch'); }]) {
    try { await apiRequest('/plans', { signal: external.signal, fetcher }); } catch { /* Failure cleanup is under test. */ }
    assert.equal(timers.size, 0);
    assert.equal(getEventListeners(external.signal, 'abort').length, 0);
  }
  assert.deepEqual(delays, [45000, 45000, 45000, 45000]);
  await assert.rejects(apiRequest('/plans', { signal: external.signal, timeoutMs: 5, fetcher: (_url, options) => waitForAbort(options.signal) }));
  assert.equal(timers.size, 0);
  assert.equal(getEventListeners(external.signal, 'abort').length, 0);
});

test('invalid JSON request bodies fail locally before fetching and are not reported as network errors', async (t) => {
  environment(t);
  const body = {};
  body.self = body;
  let called = false;
  await assert.rejects(apiRequest('/plans', { method: 'POST', body, fetcher: async () => { called = true; } }), (error) => error instanceof TypeError && error.code === undefined);
  assert.equal(called, false);
});
