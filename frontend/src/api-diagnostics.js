const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
export const DIAGNOSTIC_SAMPLE_LIMIT = 50;
export const DIAGNOSTIC_FIELDS = { terminal: 'Xí nghiệp', sourceId: 'Mã phiếu nguồn', date: 'Ngày', cargoId: 'Mã hàng', methodId: 'Mã phương án', referenceId: 'Mã tham chiếu' };
const REASONS = {
  CARGO_KIND_UNMAPPED: 'Mặt hàng chưa được phân loại', CARGO_TYPE_UNMAPPED: 'Chưa ánh xạ loại hàng',
  METHOD_ID_UNAVAILABLE: 'Thiếu mã phương án hợp lệ', WEIGHT_OR_UNIT_UNAVAILABLE: 'Thiếu khối lượng hoặc hệ số quy đổi sang tấn',
  SOURCE_REFERENCE_NOT_FOUND: 'Không tìm thấy mã danh mục trong nguồn xí nghiệp', SOURCE_ID_CONFLICT: 'Mã nguồn có bản ghi xung đột',
  SOURCE_DATE_INVALID: 'Ngày nguồn chưa hợp lệ', PHYSICAL_SHIP_OR_DIRECTION_UNAVAILABLE: 'Thiếu tàu hoặc chiều hàng hợp lệ',
  CONTAINER_SIZE_RELATION_UNCONFIRMED: 'Chưa xác minh quan hệ mặt hàng và kích cỡ container', CONTAINER_SIZE_UNMAPPED: 'Chưa có kích cỡ hoặc hệ số TEU hợp lệ',
  CONTAINER_QUANTITY_UNIT_UNCONFIRMED: 'Chưa xác minh đơn vị số lượng container', CONTAINER_QUANTITY_UNAVAILABLE: 'Thiếu số lượng container hợp lệ',
  WEIGHT_OUT_OF_RANGE: 'Khối lượng vượt giới hạn API', AGGREGATE_OUT_OF_RANGE: 'Tổng sản lượng vượt giới hạn API',
  DATE_BASIS_UNCONFIRMED: 'Chưa xác nhận cơ sở ngày', PRODUCTION_SCOPE_UNCONFIRMED: 'Chưa xác nhận phạm vi sản lượng',
  QUAY_METHODS_UNCONFIRMED: 'Chưa cấu hình phương án qua cầu', GATE_METHODS_UNCONFIRMED: 'Chưa cấu hình phương án qua cổng hoặc bãi',
  SOURCE_UNAVAILABLE: 'Chưa kết nối được nguồn', SOURCE_TIMEOUT: 'Nguồn phản hồi quá thời gian chờ', SOURCE_SCHEMA: 'Cấu trúc nguồn chưa phù hợp',
  SOURCE_ROW_LIMIT: 'Số dòng nguồn vượt giới hạn đọc', SOURCE_DATA: 'Dữ liệu nguồn chưa hợp lệ', SOURCE_DATA_NOT_READY: 'Dữ liệu nguồn chưa sẵn sàng',
  SOURCE_TERMINALS_INCOMPLETE: 'Chưa đủ nguồn xí nghiệp', SOURCE_PROFILE_UNAPPROVED: 'Cấu hình nguồn chưa được duyệt',
  SOURCE_CHANGE_DATE_MISSING: 'Thiếu ngày thay đổi trong nguồn', MAPPING_REQUIRED: 'Cần bổ sung ánh xạ danh mục',
};
export function diagnosticReason(code) { return REASONS[code] || 'Cần kiểm tra dữ liệu nguồn'; }
export function diagnosticValue(field, value) {
  if (value === undefined || value === null || value === '') return 'Chưa có thông tin';
  if (field === 'terminal') return { cua_lo: 'Cửa Lò', ben_thuy: 'Bến Thủy' }[value] || String(value);
  if (field === 'date') return String(value).split('-').reverse().join('/');
  return String(value);
}
export function diagnosticMeasurement(value, unit) {
  return `${value ?? 'Chưa có số liệu'} ${unit ?? '(chưa rõ đơn vị)'}`;
}

export function diagnosticsRequest(resource, response) {
  if (!resource || response?.resource !== resource.id || response.statusCode !== 503 || !object(response.requestQuery)) {
    throw new Error('Chưa có yêu cầu lỗi phù hợp để kiểm tra phiếu.');
  }
  const allowed = new Set(['companyId', 'page', 'limit', ...(resource.filters || []).map((field) => field.name)]);
  const query = {};
  for (const [key, value] of Object.entries(response.requestQuery)) {
    if (!allowed.has(key) || /token|password|authorization|secret/i.test(key)
      || !(typeof value === 'string' || (typeof value === 'number' && Number.isFinite(value)))) {
      throw new Error('Tham số của yêu cầu lỗi chưa hợp lệ. Hãy chạy kiểm tra API lại.');
    }
    query[key] = value;
  }
  return { resource: resource.id, query };
}

export function validateDiagnostics(value) {
  if (value === undefined || value === null) return null;
  const code = (item) => typeof item === 'string' && /^[A-Z][A-Z0-9_]{0,79}$/.test(item);
  const count = (item) => item === null || (Number.isSafeInteger(item) && item >= 0);
  if (!object(value) || typeof value.resource !== 'string' || !/^[A-Za-z0-9_.-]{1,100}$/.test(value.resource)
    || !Array.isArray(value.blockers) || !Array.isArray(value.samples) || typeof value.samplesTruncated !== 'boolean'
    || value.blockers.some((item) => !object(item) || !code(item.code) || !count(item.count))) {
    throw new Error('Chi tiết kiểm tra trả về chưa hợp lệ. Vui lòng thử lại.');
  }
  const reasons = new Set(value.blockers.map((item) => item.code));
  const samples = value.samples.slice(0, DIAGNOSTIC_SAMPLE_LIMIT).map((item) => {
    if (!object(item) || !reasons.has(item.code) || !['cua_lo', 'ben_thuy'].includes(item.terminal)) {
      throw new Error('Danh sách phiếu cần kiểm tra chưa hợp lệ.');
    }
    const sample = { code: item.code, terminal: item.terminal };
    for (const field of ['sourceId', 'cargoId', 'methodId', 'referenceId']) {
      if (item[field] === undefined) continue;
      if (typeof item[field] !== 'string' || !/^\d{1,19}$/.test(item[field])) throw new Error('Mã phiếu cần kiểm tra chưa hợp lệ.');
      sample[field] = item[field];
    }
    if (item.date !== undefined) {
      const day = typeof item.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(item.date) ? new Date(`${item.date}T00:00:00Z`) : null;
      if (!day || !Number.isFinite(day.valueOf()) || day.toISOString().slice(0, 10) !== item.date) throw new Error('Ngày phiếu cần kiểm tra chưa hợp lệ.');
      sample.date = item.date;
    }
    for (const field of ['nativeWeight', 'quantity']) {
      if (item[field] === undefined) continue;
      if (typeof item[field] !== 'string' || !/^(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,38})?$/.test(item[field])
        || !Number.isFinite(Number(item[field])) || Number(item[field]) > 1e15) throw new Error('Số liệu gốc của phiếu chưa hợp lệ.');
      sample[field] = item[field];
    }
    for (const field of ['weightUnit', 'quantityUnit']) {
      if (item[field] === undefined) continue;
      if (!['TAN', 'KG', 'M3', 'M2', 'CAI', 'GÀU', 'GIO', 'CONT', 'LÓNG', 'CỌC'].includes(item[field])) throw new Error('Đơn vị gốc của phiếu chưa hợp lệ.');
      sample[field] = item[field];
    }
    return sample;
  });
  const conflictIds = value.conflictIds === undefined ? [] : value.conflictIds;
  if (!Array.isArray(conflictIds) || conflictIds.some((item) => typeof item !== 'string' || !/^\d{1,19}$/.test(item))
    || (value.conflictCount !== undefined && !count(value.conflictCount))) throw new Error('Danh sách mã trùng chưa hợp lệ.');
  return { resource: value.resource, blockers: value.blockers.map(({ code: reason, count: total }) => ({ code: reason, count: total })),
    samples, samplesTruncated: value.samplesTruncated || value.samples.length > samples.length,
    conflictIds: conflictIds.slice(0, DIAGNOSTIC_SAMPLE_LIMIT), conflictCount: value.conflictCount ?? null };
}

export function createDiagnosticsRequestTracker() {
  let active = null;
  return {
    cancel() { active?.abort(); active = null; },
    begin() {
      active?.abort();
      const controller = new AbortController(); active = controller;
      return { signal: controller.signal, current: () => active === controller && !controller.signal.aborted };
    },
  };
}
