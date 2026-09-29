import { INSPECTOR_HEADERS } from './api-inspector.js';

const API_BASE = import.meta.env?.VITE_API_URL || '/api';
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
const RESOURCE_PATH = /^\/api\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*$/;
const LOGIN_ERRORS = {
  CORPORATE_API_DISABLED: 'API Tổng công ty chưa được kích hoạt trên máy chủ.',
  MACHINE_CREDENTIALS_INVALID: 'Sai tài khoản hoặc mật khẩu API.',
  MACHINE_LOGIN_THROTTLED: 'Có quá nhiều lần đăng nhập API thất bại. Vui lòng thử lại sau.',
  INVALID_REQUEST: 'Thông tin đăng nhập API chưa hợp lệ.',
  STORAGE_UNAVAILABLE: 'Kho tài khoản API tạm thời không truy cập được. Vui lòng thử lại sau.',
};

function failure(message, code, status) {
  const error = new Error(message);
  error.code = code;
  if (Number.isInteger(status)) error.status = status;
  return error;
}

function cancelled() {
  const error = failure('Đã hủy yêu cầu API.', 'REQUEST_ABORTED');
  error.name = 'AbortError';
  return error;
}

function endpoint(baseUrl, suffix) {
  // Resource paths can never replace the configured host or its proxy prefix.
  if (typeof baseUrl !== 'string' || /[\\?#\s]/.test(baseUrl)) {
    throw failure('Địa chỉ máy chủ API chưa hợp lệ.', 'INVALID_API_BASE');
  }
  const base = baseUrl.replace(/\/+$/, '');
  if (!base || baseUrl.startsWith('//')) throw failure('Địa chỉ máy chủ API chưa hợp lệ.', 'INVALID_API_BASE');
  if (base.startsWith('/')) {
    if (!/^\/[A-Za-z0-9_/-]+$/.test(base)) throw failure('Địa chỉ máy chủ API chưa hợp lệ.', 'INVALID_API_BASE');
  } else {
    let parsed;
    try { parsed = new URL(base); } catch { throw failure('Địa chỉ máy chủ API chưa hợp lệ.', 'INVALID_API_BASE'); }
    if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password
      || (parsed.pathname !== '/' && !/^\/[A-Za-z0-9_/-]+$/.test(parsed.pathname))
      || /(?:^|\/)\.{1,2}(?:\/|$)/.test(base)) {
      throw failure('Địa chỉ máy chủ API chưa hợp lệ.', 'INVALID_API_BASE');
    }
  }
  return `${base}/${suffix}`;
}

function loginFailure(response) {
  const suppliedCode = response.headers?.get('X-Error-Code');
  if (Object.hasOwn(LOGIN_ERRORS, suppliedCode)) return failure(LOGIN_ERRORS[suppliedCode], suppliedCode, response.status);
  const byStatus = {
    401: ['Sai tài khoản hoặc mật khẩu API.', 'MACHINE_CREDENTIALS_INVALID'],
    403: ['Tài khoản API không được phép đăng nhập.', 'API_FORBIDDEN'],
    429: ['Có quá nhiều yêu cầu đăng nhập API. Vui lòng thử lại sau.', 'MACHINE_LOGIN_THROTTLED'],
    503: ['Dịch vụ API tạm thời chưa sẵn sàng. Vui lòng thử lại sau.', 'API_UNAVAILABLE'],
  };
  const [message, code] = byStatus[response.status] || [`Đăng nhập API chưa thành công (HTTP ${response.status}).`, 'API_LOGIN_FAILED'];
  return failure(message, code, response.status);
}

async function requestJson(url, request, { signal, fetcher = fetch, timeoutMs = 45000 } = {}, login = false) {
  if (signal?.aborted) throw cancelled();
  const startedAt = Date.now();
  const limit = Number.isFinite(timeoutMs) && timeoutMs >= 0 ? timeoutMs : 45000;
  const controller = new AbortController();
  const abortExternal = () => controller.abort(cancelled());
  let rejectAbort;
  const aborted = new Promise((_, reject) => { rejectAbort = () => reject(controller.signal.reason); });
  controller.signal.addEventListener('abort', rejectAbort, { once: true });
  signal?.addEventListener('abort', abortExternal, { once: true });
  const timer = setTimeout(() => {
    const error = failure('Máy chủ API phản hồi quá lâu. Vui lòng thử lại.', 'REQUEST_TIMEOUT');
    error.name = 'TimeoutError';
    controller.abort(error);
  }, limit);
  const wait = (pending) => Promise.race([pending, aborted]);
  try {
    let response;
    try {
      response = await wait(fetcher(url, { ...request, credentials: 'omit', cache: 'no-store', redirect: 'error', signal: controller.signal }));
    } catch {
      if (controller.signal.aborted) throw controller.signal.reason;
      throw failure('Không kết nối được máy chủ API. Vui lòng kiểm tra kết nối và thử lại.', 'NETWORK_ERROR');
    }
    if (controller.signal.aborted) throw controller.signal.reason;
    let body;
    try { body = await wait(response.json()); } catch {
      if (controller.signal.aborted) throw controller.signal.reason;
      if (login && !response.ok) throw loginFailure(response);
      throw failure('Máy chủ API trả về nội dung không phải JSON hợp lệ.', 'INVALID_API_RESPONSE', response.status);
    }
    if (controller.signal.aborted) throw controller.signal.reason;
    if (login && !response.ok) throw loginFailure(response);
    return { response, body, startedAt, durationMs: Math.max(0, Date.now() - startedAt) };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abortExternal);
    controller.signal.removeEventListener('abort', rejectAbort);
  }
}

export async function loginCorporateApi({ username, password } = {}, { baseUrl = API_BASE, ...options } = {}) {
  if (typeof username !== 'string' || !username.trim() || username.trim().length > 80
      || typeof password !== 'string' || !password || password.length > 1024) {
    throw failure('Vui lòng nhập tài khoản và mật khẩu API hợp lệ.', 'INVALID_CREDENTIALS');
  }
  const { response, body, startedAt, durationMs } = await requestJson(endpoint(baseUrl, 'login'), {
    method: 'POST', headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify({ Username: username.trim(), Password: password }),
  }, options, true);
  if (!body || body.code !== '1' || typeof body.accessToken !== 'string' || !TOKEN.test(body.accessToken)
      || !Number.isInteger(body.expiresIn) || body.expiresIn <= 0 || body.expiresIn > 28800) {
    throw failure('Phản hồi đăng nhập API chưa hợp lệ. Vui lòng thử lại.', 'INVALID_LOGIN_RESPONSE', response.status);
  }
  const expiresAt = startedAt + body.expiresIn * 1000;
  if (expiresAt <= Date.now()) throw failure('Phiên API đã hết hạn. Vui lòng đăng nhập lại.', 'MACHINE_TOKEN_INVALID', 401);
  return { accessToken: body.accessToken, expiresIn: body.expiresIn, expiresAt, statusCode: response.status, durationMs };
}

export async function inspectCorporateApi(resource, query, { accessToken, baseUrl = API_BASE, ...options } = {}) {
  if (!resource || typeof resource.id !== 'string' || !resource.id || !RESOURCE_PATH.test(resource.path)) {
    throw failure('Đường dẫn API chưa hợp lệ.', 'INVALID_API_RESOURCE');
  }
  if (typeof accessToken !== 'string' || !TOKEN.test(accessToken)) {
    throw failure('Vui lòng đăng nhập bằng tài khoản API trước khi kiểm tra.', 'MACHINE_TOKEN_INVALID', 401);
  }
  if (!query || typeof query !== 'object' || Array.isArray(query)
      || Object.entries(query).some(([key, value]) => !/^[A-Za-z][A-Za-z0-9]*$/.test(key)
        || !(typeof value === 'string' || (typeof value === 'number' && Number.isFinite(value))))) {
    throw failure('Tham số kiểm tra API chưa hợp lệ.', 'INVALID_API_QUERY');
  }
  const requestQuery = { ...query };
  const suffix = new URLSearchParams(requestQuery).toString();
  const url = `${endpoint(baseUrl, resource.path.slice('/api/'.length))}${suffix ? `?${suffix}` : ''}`;
  const { response, body, durationMs } = await requestJson(url, {
    method: 'GET', headers: { Accept: 'application/json', Authorization: `Bearer ${accessToken}` },
  }, options);
  const headers = {};
  for (const name of INSPECTOR_HEADERS) {
    const value = response.headers?.get(name);
    if (value !== null && value !== undefined) headers[name] = value;
  }
  return { resource: resource.id, path: resource.path, method: 'GET', requestQuery,
    statusCode: response.status, durationMs, headers, body };
}

export async function logoutCorporateApi({ accessToken, baseUrl = API_BASE, ...options } = {}) {
  if (typeof accessToken !== 'string' || !TOKEN.test(accessToken)) {
    throw failure('Phiên API không hợp lệ hoặc đã hết hạn.', 'MACHINE_TOKEN_INVALID', 401);
  }
  const { response, body, durationMs } = await requestJson(endpoint(baseUrl, 'logout'), {
    method: 'POST', headers: { Accept: 'application/json', Authorization: `Bearer ${accessToken}` },
  }, options);
  if (!response.ok) {
    throw failure(response.status === 401 ? 'Phiên API không hợp lệ hoặc đã hết hạn.' : 'Chưa xác nhận được việc đăng xuất API trên máy chủ.',
      response.status === 401 ? 'MACHINE_TOKEN_INVALID' : 'API_LOGOUT_FAILED', response.status);
  }
  if (!body || body.code !== '1' || !Array.isArray(body.data) || typeof body.message !== 'string') {
    throw failure('Phản hồi đăng xuất API chưa hợp lệ.', 'INVALID_LOGOUT_RESPONSE', response.status);
  }
  return { statusCode: response.status, durationMs };
}
