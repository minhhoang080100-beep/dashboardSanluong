export const INSPECTOR_GROUPS = { production: 'Sản lượng', catalog_s: 'Danh mục sản lượng', operations: 'Danh mục vận hành' };
export const INSPECTOR_LIMITS = [20, 50, 100];
export const INSPECTOR_LIVE_MAX_DAYS = 31;
export const INSPECTOR_HEADERS = ['X-Page', 'X-Limit', 'X-Total-Count', 'X-Total-Pages', 'X-Has-Next', 'X-Snapshot-Id', 'X-Source-Read-At', 'X-Error-Code', 'Retry-After'];
const RESERVED_FILTERS = new Set(['companyId', 'page', 'limit', 'snapshotId']);
const SNAPSHOT_ID = /^[a-f0-9]{32}$/i;
const DEFAULT_API_BASE = import.meta.env?.VITE_API_URL || '/api';
const own = (value) => value && typeof value === 'object' && !Array.isArray(value);

export function dateInput(value) {
  if (typeof value !== 'string' || !/^\d{8}$/.test(value)) return '';
  const iso = `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`;
  const date = new Date(`${iso}T00:00:00Z`);
  return Number.isFinite(date.valueOf()) && date.toISOString().slice(0, 10) === iso ? iso : '';
}

export function dateQuery(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return '';
  const compact = value.replaceAll('-', '');
  return dateInput(compact) === value ? compact : '';
}

export function inspectorFields(resource) {
  return (resource?.filters || []).filter((field) => !RESERVED_FILTERS.has(field.name));
}

export function validateInspectorCatalog(value) {
  if (!own(value) || typeof value.enabled !== 'boolean' || value.companyId !== 'CNT' || value.mode !== 'internal' || !Array.isArray(value.resources)
      || ![undefined, 'published', 'live'].includes(value.readMode)) {
    throw new Error('Danh sách API trả về chưa hợp lệ. Vui lòng tải lại.');
  }
  const ids = new Set();
  for (const resource of value.resources) {
    if (!own(resource) || typeof resource.id !== 'string' || !resource.id || ids.has(resource.id)
        || typeof resource.label !== 'string' || !Object.hasOwn(INSPECTOR_GROUPS, resource.group)
        || resource.method !== 'GET' || !/^\/api\/[A-Za-z0-9/_-]+$/.test(resource.path)
        || !(value.readMode === 'live' ? ['live'] : ['published', 'not_published']).includes(resource.status) || !Array.isArray(resource.filters)) {
      throw new Error('Danh sách API trả về chưa hợp lệ. Vui lòng tải lại.');
    }
    const fields = new Set();
    for (const field of resource.filters) {
      if (!own(field) || !/^[A-Za-z][A-Za-z0-9]*$/.test(field.name) || fields.has(field.name)
          || typeof field.label !== 'string' || !['date', 'text'].includes(field.type) || typeof field.required !== 'boolean') {
        throw new Error('Bộ lọc API trả về chưa hợp lệ. Vui lòng tải lại.');
      }
      fields.add(field.name);
    }
    ids.add(resource.id);
  }
  return value;
}

export function inspectorStatusLabel(resource) {
  return resource?.status === 'live' ? 'Truy vấn SmartTOS' : resource?.status === 'published' ? 'Có bản công bố' : 'Chưa công bố';
}

export function initialInspectorValues(resource, now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Ho_Chi_Minh', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
  const part = (type) => parts.find((item) => item.type === type)?.value;
  const today = `${part('year')}-${part('month')}-${part('day')}`;
  const ranges = (resource?.status !== 'live' && Array.isArray(resource?.coverage) ? resource.coverage : [])
    .filter((item) => Array.isArray(item) && item.length === 2 && dateInput(item[0]) && dateInput(item[1]) && item[0] <= item[1])
    .sort((a, b) => b[1].localeCompare(a[1]) || b[0].localeCompare(a[0]));
  const range = ranges[0];
  return Object.fromEntries(inspectorFields(resource).map((field) => [field.name,
    field.type === 'date' && field.name === 'startDate' ? (range ? dateInput(range[0]) : `${today.slice(0, 8)}01`)
      : field.type === 'date' && field.name === 'endDate' ? (range ? dateInput(range[1]) : today) : '']));
}

export function buildInspectorQuery(resource, values, { limit = 20, page = 1, snapshotId } = {}) {
  if (!resource || !INSPECTOR_LIMITS.includes(Number(limit)) || !Number.isSafeInteger(page) || page < 1) throw new Error('Tham số phân trang chưa hợp lệ.');
  const query = { companyId: 'CNT' };
  for (const field of inspectorFields(resource)) {
    const value = String(values[field.name] ?? '').trim();
    if (!value) {
      if (field.required) throw new Error(`Vui lòng nhập ${field.label.toLowerCase()}.`);
      continue;
    }
    const normalized = field.type === 'date' ? dateQuery(value) : value;
    if (!normalized) throw new Error(`${field.label} chưa hợp lệ.`);
    query[field.name] = normalized;
  }
  if (query.startDate && query.endDate && query.startDate > query.endDate) throw new Error('Ngày bắt đầu không được sau ngày kết thúc.');
  if (resource.status === 'live' && resource.group === 'production' && query.startDate && query.endDate) {
    const duration = (Date.parse(`${dateInput(query.endDate)}T00:00:00Z`) - Date.parse(`${dateInput(query.startDate)}T00:00:00Z`)) / 86400000 + 1;
    if (duration > INSPECTOR_LIVE_MAX_DAYS) throw new Error(`Mỗi lần truy vấn sản lượng tối đa ${INSPECTOR_LIVE_MAX_DAYS} ngày. Vui lòng chọn khoảng ngày ngắn hơn.`);
  }
  query.page = String(page); query.limit = String(limit);
  if (resource.status !== 'live') {
    if ((page > 1 || snapshotId) && (typeof snapshotId !== 'string' || !SNAPSHOT_ID.test(snapshotId))) throw new Error('Chưa có mã phiên dữ liệu hợp lệ. Hãy chạy lại từ trang đầu.');
    if (snapshotId) query.snapshotId = snapshotId;
  }
  return query;
}

export function inspectorHeader(headers, name) {
  if (!own(headers)) return null;
  const key = Object.keys(headers).find((item) => item.toLowerCase() === name.toLowerCase());
  return key === undefined || headers[key] == null ? null : String(headers[key]);
}

export function validateInspection(value, resource, query) {
  if (!own(value) || value.resource !== resource.id || value.path !== resource.path || value.method !== 'GET'
      || !Number.isInteger(value.statusCode) || value.statusCode < 100 || value.statusCode > 599
      || !own(value.body) || !Array.isArray(value.body.data) || typeof value.body.code !== 'string'
      || !value.body.code || typeof value.body.message !== 'string' || !own(value.headers) || !own(value.requestQuery)) {
    throw new Error('Kết quả kiểm tra trả về chưa hợp lệ. Vui lòng thử lại.');
  }
  if (value.statusCode >= 200 && value.statusCode < 300 && value.body.code === '1') {
    for (const [name, expected] of Object.entries(query)) {
      if (String(value.requestQuery[name] ?? '') !== String(expected)) throw new Error('Kết quả chưa khớp tham số đã gửi. Vui lòng chạy lại.');
    }
  }
  return value;
}

export function inspectorPagination(response, resource) {
  const live = resource?.status === 'live';
  const rows = Array.isArray(response?.body?.data) ? response.body.data.length : 0;
  const integer = (name, min = 0) => {
    const raw = inspectorHeader(response?.headers, name);
    const number = raw !== null && /^\d+$/.test(raw) ? Number(raw) : NaN;
    return Number.isSafeInteger(number) && number >= min ? number : null;
  };
  const page = integer('X-Page', 1), limit = integer('X-Limit', 1), total = integer('X-Total-Count');
  const hasNext = inspectorHeader(response?.headers, 'X-Has-Next');
  const snapshotId = live ? null : inspectorHeader(response?.headers, 'X-Snapshot-Id');
  const valid = page !== null && limit !== null && total !== null && total >= rows && rows <= limit
    && String(page) === String(response?.requestQuery?.page) && String(limit) === String(response?.requestQuery?.limit)
    && ['true', 'false'].includes(hasNext)
    && (live || (SNAPSHOT_ID.test(snapshotId || '')
      && (!response?.requestQuery?.snapshotId || response.requestQuery.snapshotId === snapshotId)))
    && (hasNext === 'true') === (page * limit < total)
    && response?.statusCode >= 200 && response.statusCode < 300 && String(response?.body?.code) === '1';
  return { valid, page, limit, total, snapshotId, hasNext: valid && hasNext === 'true', pages: total !== null && limit ? Math.max(1, Math.ceil(total / limit)) : null };
}

export function inspectorRequestAddress(resource, query, { apiBase = DEFAULT_API_BASE, origin = 'http://localhost' } = {}) {
  const suffix = new URLSearchParams(query).toString();
  const base = (apiBase || DEFAULT_API_BASE).replace(/\/+$/, '');
  const path = `${base}/${resource.path.replace(/^\/api\//, '')}${suffix ? `?${suffix}` : ''}`;
  try { return new URL(path, origin).href; } catch { return path; }
}

export function inspectorRows(response) {
  return Array.isArray(response?.body?.data) ? response.body.data : [];
}

export function inspectorColumns(rows) {
  const columns = new Set();
  for (const row of rows) if (own(row)) for (const key of Object.keys(row)) columns.add(key);
  return [...columns];
}

export function inspectorCell(value) {
  if (value === undefined) return '—';
  if (value === null) return 'null';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
