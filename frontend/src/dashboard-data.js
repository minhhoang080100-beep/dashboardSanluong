import { validateVoyageList } from './voyage-data.js';
import { fetchReportResponse } from './report-request.js';

export const TERMINALS = { all: 'Toàn công ty', cua_lo: 'Xí nghiệp Cửa Lò', ben_thuy: 'Xí nghiệp Bến Thủy' };
export const TIMEZONE = 'Asia/Ho_Chi_Minh';

export function todayInVietnam(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: TIMEZONE, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
  const get = (type) => parts.find((part) => part.type === type).value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}

export function presetDates(preset, today = todayInVietnam()) {
  if (preset === 'year') return { start_date: `${today.slice(0, 4)}-01-01`, end_date: today };
  if (preset === 'previous') {
    const lastDay = new Date(`${today.slice(0, 7)}-01T00:00:00Z`);
    lastDay.setUTCDate(0);
    const end_date = lastDay.toISOString().slice(0, 10);
    return { start_date: `${end_date.slice(0, 7)}-01`, end_date };
  }
  return { start_date: `${today.slice(0, 7)}-01`, end_date: today };
}

export function validateFilters(filters, today = todayInVietnam()) {
  for (const value of [filters.start_date, filters.end_date]) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '') || !Number.isFinite(Date.parse(`${value}T00:00:00Z`)) || new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) !== value) return 'Vui lòng chọn ngày bắt đầu và ngày kết thúc hợp lệ.';
  }
  if (filters.start_date > filters.end_date) return 'Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.';
  if (filters.end_date > today) return 'Ngày kết thúc không được lớn hơn ngày hiện tại.';
  if ((Date.parse(filters.end_date) - Date.parse(filters.start_date)) / 86400000 + 1 > 366) return 'Vui lòng chọn khoảng thời gian tối đa 366 ngày.';
  if (!Object.hasOwn(TERMINALS, filters.terminal)) return 'Vui lòng chọn xí nghiệp hợp lệ.';
  return '';
}

export const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
export const formatNumber = (value, digits = 3) => isNumber(value) ? value.toLocaleString('vi-VN', { maximumFractionDigits: digits }) : '—';
export function ratioAvailability(values, total, hasSignedInput = false) {
  if (hasSignedInput || values.some((value) => isNumber(value) && value < 0)) return { available: false, reason: 'Không tính tỷ trọng với dữ liệu điều chỉnh.' };
  if (!isNumber(total) || total <= 0 || values.some((value) => !isNumber(value) || value > total)) return { available: false, reason: 'Không tính tỷ trọng khi tổng hoặc số liệu thành phần chưa đủ cơ sở.' };
  return { available: true, reason: '' };
}
export function isCurrentDayPeriod(filters, today = todayInVietnam()) {
  return filters.start_date <= today && filters.end_date === today;
}
export const formatDate = (value) => /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? value.split('-').reverse().join('/') : 'Chưa có dữ liệu';
export function formatTimestamp(value) {
  if (!value) return 'Chưa có dữ liệu';
  if (typeof value !== 'string') return 'Chưa xác định';
  // SQL operation times without an offset are Vietnam local time.
  const withZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value.replace(' ', 'T')}+07:00`;
  const date = new Date(withZone);
  return Number.isNaN(date.getTime()) ? 'Chưa xác định' : date.toLocaleString('vi-VN', { timeZone: TIMEZONE, hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit', year: 'numeric', hour12: false });
}

export function validateDashboard(data, filters) {
  const fail = () => { throw new Error('Dữ liệu trả về chưa đúng cấu trúc báo cáo. Vui lòng thử lại hoặc liên hệ bộ phận CNTT.'); };
  if (!data || !data.overview || !data.meta || !['ok', 'empty'].includes(data.meta.status)) fail();
  for (const [key, statusKey] of [['total_tonnage', 'tonnage_status'], ['total_teu', 'teu_status']]) {
    if (!isNumber(data.overview[key]) && !(data.overview[key] === null && data.overview[statusKey] === 'unavailable')) fail();
    if (data.overview[statusKey] !== undefined && !['ready', 'partial', 'unavailable', 'empty'].includes(data.overview[statusKey])) fail();
  }
  for (const key of ['vessel_calls', 'record_count']) if (!isNumber(data.overview[key])) fail();
  validateVoyageList(data.voyages, data.overview.vessel_calls, filters);
  const arrays = { cargo: ['name', 'tonnage', 'value'], history: ['date', 'tonnage', 'teu'], terminals: ['name', 'tonnage', 'teu'], directions: ['name', 'tonnage', 'value'], customers: ['name', 'volume'] };
  if (data.daily_history !== undefined) arrays.daily_history = ['date', 'tonnage', 'teu'];
  for (const [key, fields] of Object.entries(arrays)) {
    if (!Array.isArray(data[key])) fail();
    for (const row of data[key]) {
      if (!row || fields.some((field) => ['name', 'date'].includes(field) ? typeof row[field] !== 'string' : row[field] !== null && !isNumber(row[field]))) fail();
    }
  }
  for (const key of ['start_date', 'end_date', 'terminal']) if (data.meta.filters?.[key] !== filters[key]) fail();
  if (!Array.isArray(data.meta.warnings) || data.meta.warnings.some((warning) => typeof warning !== 'string')) fail();
  if (data.meta.sources !== undefined && (!Array.isArray(data.meta.sources) || data.meta.sources.some((source) => !source || typeof source.name !== 'string'))) fail();
  for (const source of data.meta.sources || []) {
    for (const key of ['latest_operation_at', 'latest_selected_operation_at']) if (source[key] != null && typeof source[key] !== 'string') fail();
  }
  for (const row of data.customers) if (row.terminal_name != null && typeof row.terminal_name !== 'string') fail();
  if (data.native_units !== undefined) {
    if (!Array.isArray(data.native_units)) fail();
    for (const row of data.native_units) {
      if (!row || typeof row.terminal_name !== 'string' || typeof row.unit_name !== 'string' || typeof row.unit_code !== 'string' || (row.value !== null && !isNumber(row.value)) || !isNumber(row.record_count)) fail();
      if (row.status !== undefined && !['ready', 'partial', 'unavailable', 'empty'].includes(row.status)) fail();
    }
  }
  if (data.meta.generated_at != null && typeof data.meta.generated_at !== 'string') fail();
  if (data.meta.previous_period !== undefined) {
    const previous = data.meta.previous_period;
    if (!previous || typeof previous !== 'object') fail();
    for (const key of ['start_date', 'end_date', 'label']) if (previous[key] != null && typeof previous[key] !== 'string') fail();
  }
  for (const key of ['definitions', 'unavailable']) {
    if (data.meta[key] !== undefined && (!data.meta[key] || typeof data.meta[key] !== 'object' || Object.values(data.meta[key]).some((value) => typeof value !== 'string'))) fail();
  }
  return data;
}

export async function fetchDashboard(filters, { signal, fetcher = fetch, baseUrl = '/api' } = {}) {
  const response = await fetchReportResponse(`${baseUrl.replace(/\/+$/, '')}/dashboard?${new URLSearchParams(filters)}`, { signal, fetcher });
  if (!response.ok) {
    if (response.status === 503) throw new Error('Tạm thời không thể truy vấn dữ liệu sản xuất. Vui lòng tải lại hoặc liên hệ bộ phận CNTT.');
    if (response.status === 400 || response.status === 422) throw new Error('Bộ lọc chưa được máy chủ chấp nhận. Hãy kiểm tra khoảng ngày và xí nghiệp.');
    throw new Error(`Không tải được báo cáo (HTTP ${response.status}). Vui lòng thử lại.`);
  }
  return validateDashboard(await response.json(), filters);
}

// React Fast Refresh can retain a successful payload fetched before a schema change.
// Revalidate retained state as well as network responses before any panel or CSV uses it.
export function dashboardResourceView(resource, filters) {
  if (resource.key !== JSON.stringify(filters) || resource.status === 'loading') return { status: 'loading', data: null, error: '' };
  if (resource.status === 'success') {
    try {
      return { status: 'success', data: validateDashboard(resource.data, filters), error: '' };
    } catch (failure) {
      return { status: 'recovering', data: null, error: failure.message };
    }
  }
  return { status: 'error', data: null, error: resource.error };
}

export function csvCell(value) {
  const text = String(value ?? '');
  // Excel may interpret names as formulas even when the CSV field is quoted.
  const isFormulaText = typeof value !== 'number' && (/^[\s\uFEFF]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text));
  const safe = isFormulaText ? `'${text}` : text;
  return `"${safe.replaceAll('"', '""')}"`;
}

export function dashboardCsv(data) {
  const { overview, meta } = data;
  const hasSignedInput = (meta.data_quality?.negative_value_count || 0) > 0;
  const cargoRatios = ratioAvailability(data.cargo.map((row) => row.tonnage), overview.total_tonnage, hasSignedInput);
  const directionRatios = ratioAvailability(data.directions.map((row) => row.tonnage), overview.total_tonnage, hasSignedInput);
  const rows = [
    ['BÁO CÁO SẢN LƯỢNG — CẢNG NGHỆ TĨNH'],
    ['Từ ngày', meta.filters.start_date, 'Đến ngày', meta.filters.end_date, 'Phạm vi', TERMINALS[meta.filters.terminal]],
    ['Tổng hợp lúc (giờ Việt Nam)', formatTimestamp(meta.generated_at)],
    ['Chỉ tiêu', 'Giá trị', 'Đơn vị'],
    ['Sản lượng qua cảng', overview.total_tonnage, 'Tấn'],
    ['Container', overview.total_teu, 'TEU'],
    ['Chuyến tàu có phát sinh', overview.vessel_calls, 'Chuyến'],
    ...(meta.metric_coverage?.tonnage ? [['Chất lượng chỉ tiêu tấn', overview.tonnage_status], ['Bản ghi tấn có giá trị', meta.metric_coverage.tonnage.known_rows, 'Bản ghi đủ điều kiện đơn vị tấn', meta.metric_coverage.tonnage.eligible_rows], ['Bản ghi thiếu khối lượng', meta.metric_coverage.tonnage.missing_weight_rows, 'Bản ghi đơn vị khác', meta.metric_coverage.tonnage.excluded_native_rows]] : []),
    ...(meta.metric_coverage?.teu ? [['Chất lượng TEU', overview.teu_status], ['Bản ghi container có số lượng', meta.metric_coverage.teu.known_rows, 'Bản ghi container', meta.metric_coverage.teu.container_rows], ['Bản ghi thiếu số lượng', meta.metric_coverage.teu.missing_quantity_rows]] : []),
    ...(isCurrentDayPeriod(meta.filters) ? [['Lưu ý', 'Kỳ có hôm nay chưa kết thúc; sản lượng và tỷ lệ so sánh đang tạm tính.']] : []),
    ...(data.daily_history ? [[], ['SẢN LƯỢNG THEO NGÀY'], ['Ngày', 'Tấn', 'TEU'], ...data.daily_history.map((row) => [row.date, row.tonnage, row.teu])] : []),
    [], ['SẢN LƯỢNG THEO THÁNG'], ['Tháng', 'Tấn', 'TEU'],
    ...data.history.map((row) => [row.date, row.tonnage, row.teu]),
    [], ['XÍ NGHIỆP'], ['Tên', 'Tấn', 'TEU'],
    ...data.terminals.map((row) => [row.name, row.tonnage, row.teu]),
    [], ['NHÓM HÀNG'], ['Tên', 'Tấn', 'Tỷ trọng (%)'],
    ...(cargoRatios.available ? [] : [[cargoRatios.reason]]),
    ...data.cargo.map((row) => [row.name, row.tonnage, cargoRatios.available ? Math.round(row.tonnage / overview.total_tonnage * 1000) / 10 : null]),
    [], ['HƯỚNG HÀNG'], ['Tên', 'Tấn', 'Tỷ trọng (%)'],
    ...(directionRatios.available ? [] : [[directionRatios.reason]]),
    ...data.directions.map((row) => [row.name, row.tonnage, directionRatios.available ? Math.round(row.tonnage / overview.total_tonnage * 1000) / 10 : null]),
    [], ['KHÁCH HÀNG DẪN ĐẦU'], ['Tên', 'Xí nghiệp', 'Tấn'],
    ...data.customers.map((row) => [row.name, row.terminal_name, row.volume]),
    [], ['CHUYẾN TÀU TRONG KỲ'], ['Tàu', 'Mã chuyến', 'Xí nghiệp', 'Ngày làm hàng đầu kỳ', 'Ngày làm hàng cuối kỳ', 'Tấn', 'TEU', 'Số dòng tác nghiệp'],
    ...data.voyages.map((row) => [row.vessel_name, row.voyage_code || row.voyage_id, row.terminal_name, row.first_operation_date, row.last_operation_date, row.tonnage, row.teu, row.record_count]),
    [], ['NGUỒN DỮ LIỆU'], ['Xí nghiệp', 'Phát sinh mới nhất tại nguồn (giờ Việt Nam)', 'Số bản ghi trong kỳ'],
    ...(meta.sources || []).map((source) => [source.name, formatTimestamp(source.latest_operation_at), source.record_count]),
    ...(data.native_units?.length ? [[], ['SẢN LƯỢNG CHƯA CỘNG VÀO TẤN'], ['Xí nghiệp', 'Mã đơn vị', 'Tên đơn vị', 'Giá trị theo đơn vị nguồn', 'Số bản ghi', 'Số bản ghi có giá trị'], ...data.native_units.map((row) => [row.terminal_name, row.unit_code, row.unit_name, row.value, row.record_count, row.known_value_rows])] : []),
    [], ['ĐỊNH NGHĨA'], ...Object.entries(meta.definitions || {}),
    [], ['LƯU Ý DỮ LIỆU'], ...meta.warnings.map((warning) => [warning]),
    ...Object.values(meta.unavailable || {}).map((reason) => [reason]),
  ];
  return `\uFEFF${rows.map((row) => row.map(csvCell).join(',')).join('\r\n')}`;
}
