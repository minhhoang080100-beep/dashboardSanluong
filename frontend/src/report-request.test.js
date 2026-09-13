import assert from 'node:assert/strict';
import test from 'node:test';
import { setImmediate as nextTurn } from 'node:timers/promises';
import { fetchReportResponse } from './report-request.js';

test('transient HTTP failures retry once after 500 ms with the same GET and signal', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const status of [502, 503, 504]) {
    const controller = new AbortController();
    const requests = [];
    let released = false;
    const success = { ok: true };
    const pending = fetchReportResponse('/api/report?terminal=all', {
      signal: controller.signal,
      fetcher: async (url, options) => {
        requests.push({ url, options });
        return requests.length === 1
          ? { ok: false, status, body: { cancel: async () => { released = true; } } }
          : success;
      },
    });
    await nextTurn();
    assert.equal(released, true);
    t.mock.timers.tick(499);
    await nextTurn();
    assert.equal(requests.length, 1);
    t.mock.timers.tick(1);
    assert.equal(await pending, success);
    assert.equal(requests.length, 2);
    assert.equal(requests[1].url, requests[0].url);
    assert.equal(requests[1].options.signal, controller.signal);
    assert.equal(requests[1].options.headers.Accept, 'application/json');
    assert.equal(requests[1].options.method, undefined); // fetch defaults to GET.
  }
});

test('network errors and persistent transient statuses stop after two attempts', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let attempts = 0;
  const networkFailure = new TypeError('Failed to fetch');
  const network = fetchReportResponse('/api/report', {
    fetcher: async () => { attempts += 1; throw networkFailure; },
  });
  const rejected = assert.rejects(network, (failure) => failure === networkFailure);
  await nextTurn();
  t.mock.timers.tick(500);
  await rejected;
  assert.equal(attempts, 2);

  attempts = 0;
  const unavailable = { ok: false, status: 503 };
  const http = fetchReportResponse('/api/report', {
    fetcher: async () => { attempts += 1; return unavailable; },
  });
  await nextTurn();
  t.mock.timers.tick(500);
  assert.equal(await http, unavailable);
  assert.equal(attempts, 2);
  t.mock.timers.tick(60000);
  await nextTurn();
  assert.equal(attempts, 2);
});

test('permanent HTTP errors and non-network exceptions are never retried', async () => {
  for (const status of [400, 401, 403, 404, 422, 429, 500]) {
    let attempts = 0;
    const response = { ok: false, status };
    assert.equal(await fetchReportResponse('/api/report', {
      fetcher: async () => { attempts += 1; return response; },
    }), response);
    assert.equal(attempts, 1);
  }
  for (const failure of [new DOMException('Aborted', 'AbortError'), new Error('Other failure')]) {
    let attempts = 0;
    await assert.rejects(fetchReportResponse('/api/report', {
      fetcher: async () => { attempts += 1; throw failure; },
    }), (actual) => actual === failure);
    assert.equal(attempts, 1);
  }
});

test('cancelling during backoff prevents another request and clears the retry timer', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const controller = new AbortController();
  let attempts = 0;
  const pending = fetchReportResponse('/api/report', {
    signal: controller.signal,
    fetcher: async () => { attempts += 1; throw new TypeError('Network failure'); },
  });
  const rejected = assert.rejects(pending, { name: 'AbortError' });
  await nextTurn();
  controller.abort();
  await rejected;
  t.mock.timers.tick(60000);
  await nextTurn();
  assert.equal(attempts, 1);
});

test('a late response after cancellation cannot start a retry or supply stale data', async () => {
  const controller = new AbortController();
  let finishRequest;
  let attempts = 0;
  const pending = fetchReportResponse('/api/report', {
    signal: controller.signal,
    fetcher: () => {
      attempts += 1;
      return new Promise((resolve) => { finishRequest = resolve; });
    },
  });
  const rejected = assert.rejects(pending, { name: 'AbortError' });
  controller.abort();
  finishRequest({ ok: true });
  await rejected;
  assert.equal(attempts, 1);
});

test('the original 45 second deadline includes backoff and the second attempt', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const controller = new AbortController();
  setTimeout(() => controller.abort(), 45000);
  let attempts = 0;
  const pending = fetchReportResponse('/api/report', {
    signal: controller.signal,
    fetcher: async (_url, { signal }) => {
      attempts += 1;
      if (attempts === 1) return { ok: false, status: 503 };
      return new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(signal.reason), { once: true });
      });
    },
  });
  const rejected = assert.rejects(pending, { name: 'AbortError' });
  await nextTurn();
  t.mock.timers.tick(500);
  await nextTurn();
  assert.equal(attempts, 2);
  t.mock.timers.tick(44500);
  await rejected;
  assert.equal(attempts, 2);
});

test('response body cleanup failure does not prevent the bounded retry', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let attempts = 0;
  const pending = fetchReportResponse('/api/report', {
    fetcher: async () => {
      attempts += 1;
      return attempts === 1
        ? { ok: false, status: 502, body: { cancel: async () => { throw new Error('Closed body'); } } }
        : { ok: true };
    },
  });
  await nextTurn();
  t.mock.timers.tick(500);
  assert.equal((await pending).ok, true);
  assert.equal(attempts, 2);
});
