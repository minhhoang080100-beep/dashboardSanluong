const TRANSIENT_STATUSES = new Set([502, 503, 504]);

function waitBeforeRetry(signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, 500);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

// Only report GETs retry. The caller's signal covers both attempts and the delay,
// so its deadline and cancellation on filter/page changes remain unchanged.
export async function fetchReportResponse(url, { signal, fetcher = fetch } = {}) {
  const options = { signal, headers: { Accept: 'application/json' } };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    signal?.throwIfAborted();
    let response;
    try {
      response = await fetcher(url, options);
    } catch (failure) {
      if (signal?.aborted || attempt === 1 || !(failure instanceof TypeError)) throw failure;
      await waitBeforeRetry(signal);
      continue;
    }
    signal?.throwIfAborted();
    if (attempt === 1 || response.ok || !TRANSIENT_STATUSES.has(response.status)) return response;
    try {
      // Release the failed response without letting cleanup delay cancellation.
      response.body?.cancel().catch(() => {});
    } catch { /* Best effort: a missing or already closed body needs no cleanup. */ }
    await waitBeforeRetry(signal);
  }
}
