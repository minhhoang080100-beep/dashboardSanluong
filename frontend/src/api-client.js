const API_BASE = import.meta.env?.VITE_API_URL || '/api';
const SESSION_KEY = 'port-dashboard-session';
let memoryToken = null;

const FIELD_LABELS = { terminal: 'Xí nghiệp', period_type: 'Loại kế hoạch', month: 'Tháng kế hoạch', quarter: 'Quý kế hoạch', year: 'Năm kế hoạch', start_date: 'Ngày bắt đầu', end_date: 'Ngày kết thúc', voyage_id: 'Chuyến tàu', metric: 'Chỉ tiêu', amount: 'Giá trị kế hoạch', reference: 'Văn bản / nguồn phê duyệt', note: 'Ghi chú', expected_revision: 'Phiên bản dữ liệu' };

function validationFields(detail) {
  if (!Array.isArray(detail)) return {};
  const fields = {};
  for (const issue of detail) {
    const field = Array.isArray(issue?.loc) ? issue.loc.at(-1) : null;
    if (!Object.hasOwn(FIELD_LABELS, field)) continue;
    fields[field] = issue.type === 'missing' ? `Vui lòng nhập ${FIELD_LABELS[field].toLowerCase()}.`
      : field === 'amount' ? 'Giá trị kế hoạch phải từ 0 đến 1.000.000.000.000 và tối đa 6 chữ số thập phân.'
      : `${FIELD_LABELS[field]} chưa hợp lệ. Vui lòng kiểm tra lại.`;
  }
  return fields;
}

export function getSessionToken() {
  // An explicit login/logout is authoritative when storage can be read but not written.
  if (memoryToken !== null) return memoryToken;
  try { return globalThis.sessionStorage?.getItem(SESSION_KEY) || ''; } catch { return ''; }
}

export function setSessionToken(token) {
  memoryToken = token || '';
  try {
    if (token) globalThis.sessionStorage?.setItem(SESSION_KEY, token);
    else globalThis.sessionStorage?.removeItem(SESSION_KEY);
  } catch { /* Keep this tab usable until reload when browser storage is blocked. */ }
}

export function authHeaders() {
  const token = getSessionToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export function sessionExpired() {
  setSessionToken('');
  globalThis.window?.dispatchEvent(new Event('dashboard-session-expired'));
}

export async function apiRequest(path, { body, method = 'GET', signal, baseUrl = API_BASE, raw = false, responseType = 'json', fetcher = fetch, timeoutMs = 45000 } = {}) {
  if (signal?.aborted) throw signal.reason;
  const form = typeof FormData !== 'undefined' && body instanceof FormData;
  const request = {
    method,
    headers: { Accept: raw || responseType === 'blob' ? '*/*' : 'application/json', ...authHeaders(), ...(body !== undefined && !form ? { 'Content-Type': 'application/json' } : {}) },
    ...(body !== undefined ? { body: form ? body : JSON.stringify(body) } : {}),
  };
  const mutation = !['GET', 'HEAD', 'OPTIONS'].includes(method.toUpperCase());
  const checkOutcome = 'Hãy tải lại danh sách để kiểm tra kết quả trước khi thử lại; máy chủ có thể đã lưu thay đổi.';
  const networkError = (failure) => {
    const error = new Error(`Không kết nối được máy chủ báo cáo. ${mutation ? checkOutcome : 'Vui lòng kiểm tra kết nối và thử lại.'}`, { cause: failure });
    error.code = 'NETWORK_ERROR';
    return error;
  };
  const limit = Number.isFinite(timeoutMs) && timeoutMs >= 0 ? timeoutMs : 45000;
  const timeout = new Error(`Chưa nhận được phản hồi sau ${Math.ceil(limit / 1000)} giây. ${mutation ? checkOutcome : 'Vui lòng thử tải lại.'}`);
  timeout.name = 'TimeoutError';
  timeout.code = 'REQUEST_TIMEOUT';
  const controller = new AbortController();
  const abortExternal = () => controller.abort(signal.reason);
  let rejectAbort;
  const aborted = new Promise((_, reject) => { rejectAbort = () => reject(controller.signal.reason); });
  controller.signal.addEventListener('abort', rejectAbort, { once: true });
  signal?.addEventListener('abort', abortExternal, { once: true });
  const timer = setTimeout(() => controller.abort(timeout), limit);
  const wait = (pending) => Promise.race([pending, aborted]);
  try {
    let response;
    try {
      response = await wait(fetcher(`${baseUrl.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`, { ...request, signal: controller.signal }));
    } catch (failure) {
      if (controller.signal.aborted) throw controller.signal.reason;
      if (!(failure instanceof TypeError)) throw failure;
      throw networkError(failure);
    }
    if (controller.signal.aborted) throw controller.signal.reason;
    if (!response.ok) {
      const loginRequest = path.split(/[?#]/, 1)[0].replace(/^\/+|\/+$/g, '') === 'auth/login';
      if (response.status === 401 && !loginRequest) sessionExpired();
      let detail;
      try { detail = (await wait(response.json())).detail; } catch { /* Use the safe HTTP message unless cancelled. */ }
      if (controller.signal.aborted) throw controller.signal.reason;
      const fieldErrors = validationFields(detail);
      const message = typeof detail === 'string' ? detail : detail?.message || (Object.keys(fieldErrors).length ? Object.values(fieldErrors).join(' ') : null);
      const error = new Error(message || (response.status === 401 ? 'Phiên đăng nhập đã hết hạn.' : response.status === 403 ? 'Bạn không có quyền thực hiện thao tác này.' : `Yêu cầu chưa thành công (HTTP ${response.status}).`));
      error.status = response.status;
      error.detail = detail;
      error.fieldErrors = fieldErrors;
      throw error;
    }
    // Only low-level raw callers finish at headers. Downloads share the full deadline.
    if (raw) return response;
    if (response.status === 204) return null;
    try { return await wait(responseType === 'blob' ? response.blob() : response.json()); }
    catch (failure) {
      if (controller.signal.aborted) throw controller.signal.reason;
      if (failure instanceof TypeError) throw networkError(failure);
      throw failure;
    }
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abortExternal);
    controller.signal.removeEventListener('abort', rejectAbort);
  }
}

export async function downloadFile(path, filename, options = {}) {
  const blob = await apiRequest(path, { ...options, raw: false, responseType: 'blob' });
  if (options.signal?.aborted) throw options.signal.reason;
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = filename;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
