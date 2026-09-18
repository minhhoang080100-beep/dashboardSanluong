import { BERTH_RULE_VERSION, isProductionScope } from './production-scope.js';
import { isoWeekValue, weekDates } from './dashboard-data.js';

export const MANAGEMENT_TERMINALS = { cua_lo: 'Cửa Lò', ben_thuy: 'Bến Thủy' };
export const PLAN_TERMINALS = { all: 'Toàn công ty', ...MANAGEMENT_TERMINALS };
export const PLAN_PERIOD_LABELS = { week: 'Theo tuần', month: 'Theo tháng', quarter: 'Theo quý', year: 'Theo năm', custom: 'Khoảng ngày tùy chọn', voyage: 'Theo chuyến tàu' };
export const MANAGEMENT_ROLES = { viewer: 'Xem báo cáo', manager: 'Quản lý', admin: 'Quản trị' };
export const ISSUE_LABELS = { missing_weight: 'Thiếu trọng lượng', unknown_unit: 'Chưa xác định đơn vị', negative: 'Giá trị âm' };
export const ISSUE_STATUS_LABELS = { open: 'Đang xử lý', resolved: 'Đã xử lý', ignored: 'Không xử lý' };
export const PLAN_STATUS_LABELS = { draft: 'Bản nháp', approved: 'Đã duyệt', superseded: 'Đã thay thế', cancelled: 'Đã hủy' };
export const METRIC_LABELS = { tonnage: 'Sản lượng thông qua (tấn)', teu: 'Container (TEU)' };

export function userTerminals(user) {
  if (!Array.isArray(user?.terminals)) return [];
  return Object.keys(MANAGEMENT_TERMINALS).filter((terminal) => user.terminals.includes(terminal));
}

export function canManage(user) {
  return user?.role === 'manager' || user?.role === 'admin';
}

export function planTerminals(allowedTerminals, periodType = 'month') {
  const terminals = Object.keys(MANAGEMENT_TERMINALS).filter((terminal) => allowedTerminals.includes(terminal));
  return periodType !== 'voyage' && terminals.length === 2 ? ['all', ...terminals] : terminals;
}

export function planDateDefaults(startDate, endDate = startDate) {
  const year = startDate.slice(0, 4);
  return { week: isoWeekValue(startDate), month: startDate.slice(0, 7), quarter: `${year}-Q${Math.ceil(Number(startDate.slice(5, 7)) / 3)}`, year, start_date: startDate, end_date: endDate };
}

export function planEntryPeriod(filters, preferredPeriodType) {
  if (['week', 'month', 'quarter', 'year', 'custom'].includes(preferredPeriodType)) return preferredPeriodType;
  const { start_date: start = '', end_date: end = '' } = filters || {};
  if (start.endsWith('-01') && start.slice(0, 7) === end.slice(0, 7)) return 'month';
  if (/^\d{4}-(01|04|07|10)-01$/.test(start)) {
    const quarterEnd = new Date(Date.UTC(Number(start.slice(0, 4)), Number(start.slice(5, 7)) + 2, 0)).toISOString().slice(0, 10);
    if (end >= start && end <= quarterEnd) return 'quarter';
  }
  if (start.endsWith('-01-01') && end >= start && end <= `${start.slice(0, 4)}-12-31`) return 'year';
  return 'custom';
}

export function parseVietnamesePlanAmount(value) {
  const input = String(value ?? '').trim();
  if (!input) throw new Error('Nhập giá trị kế hoạch.');
  if (!/^(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?$/.test(input)) throw new Error('Dùng dấu chấm tách nghìn, dấu phẩy cho phần lẻ. Ví dụ: 150.000,5 hoặc 150000.');
  const [whole, rawFraction = ''] = input.replaceAll('.', '').split(',');
  const integer = whole.replace(/^0+(?=\d)/, '');
  const fraction = rawFraction.replace(/0+$/, '');
  if (fraction.length > 6) throw new Error('Kế hoạch được có tối đa 6 chữ số sau dấu phẩy.');
  if (integer.length > 13 || BigInt(integer) * 1000000n + BigInt(fraction.padEnd(6, '0') || '0') > 1000000000000000000n) throw new Error('Kế hoạch tối đa 1.000.000.000.000.');
  return `${integer}${fraction ? `.${fraction}` : ''}`;
}

export function planAmountInput(value) {
  const source = String(value ?? '');
  if (!/^\d+(?:\.\d+)?$/.test(source)) return '';
  const [whole, rawFraction = ''] = source.split('.');
  const fraction = rawFraction.replace(/0+$/, '');
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, '.')}${fraction ? `,${fraction}` : ''}`;
}

export function validatePlanEntry(form, allowedTerminals) {
  const errors = {};
  if (!planTerminals(allowedTerminals, form.period_type).includes(form.terminal)) errors.terminal = 'Chọn xí nghiệp trong phạm vi được cấp.';
  if (!Object.hasOwn(METRIC_LABELS, form.metric)) errors.metric = 'Chọn chỉ tiêu kế hoạch.';
  let amount;
  try { amount = parseVietnamesePlanAmount(form.amount); } catch (error) { errors.amount = error.message; }
  let period;
  try { period = planPeriodFields(form); } catch (error) { errors.period = error.message; }
  const reference = String(form.reference || '').trim();
  if (!reference) errors.reference = 'Nhập số văn bản hoặc nguồn phê duyệt kế hoạch.';
  else if (reference.length > 500) errors.reference = 'Nguồn phê duyệt tối đa 500 ký tự.';
  const note = String(form.note || '').trim();
  if (note.length > 4000) errors.note = 'Ghi chú tối đa 4.000 ký tự.';
  return { errors, payload: Object.keys(errors).length ? null : { terminal: form.terminal, period_type: form.period_type, metric: form.metric, amount, reference, note, ...period } };
}

function validPlanDate(value) {
  return /^20\d{2}-(0[1-9]|1[0-2])-\d{2}$/.test(value || '') && Number.isFinite(Date.parse(`${value}T00:00:00Z`)) && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

export function planWeekDates(week) {
  // Plans cover the whole ISO week, including future days and dates in the
  // next calendar year. Report weekDates instead clips its end to today.
  return /^20\d{2}-W\d{2}$/.test(week || '') ? weekDates(week, '2100-01-10') : null;
}

export function planPeriodFields(form) {
  if (form.period_type === 'week') {
    if (!planWeekDates(form.week)) throw new Error('Chọn tuần kế hoạch hợp lệ từ năm 2000 đến 2099.');
    return { week: form.week };
  }
  if (form.period_type === 'month') {
    if (!/^20\d{2}-(0[1-9]|1[0-2])$/.test(form.month || '')) throw new Error('Chọn tháng kế hoạch từ năm 2000 đến 2099.');
    return { month: form.month };
  }
  if (form.period_type === 'quarter') {
    if (!/^20\d{2}-Q[1-4]$/.test(form.quarter || '')) throw new Error('Chọn quý kế hoạch từ năm 2000 đến 2099.');
    return { quarter: form.quarter };
  }
  if (form.period_type === 'year') {
    if (!/^20\d{2}$/.test(String(form.year || ''))) throw new Error('Chọn năm kế hoạch từ 2000 đến 2099.');
    return { year: Number(form.year) };
  }
  if (form.period_type === 'custom') {
    if (!validPlanDate(form.start_date) || !validPlanDate(form.end_date)) throw new Error('Chọn ngày kế hoạch hợp lệ từ năm 2000 đến 2099.');
    if (form.start_date > form.end_date) throw new Error('Ngày bắt đầu kế hoạch phải trước hoặc bằng ngày kết thúc.');
    if ((Date.parse(form.end_date) - Date.parse(form.start_date)) / 86400000 + 1 > 366) throw new Error('Khoảng ngày kế hoạch tối đa 366 ngày.');
    return { start_date: form.start_date, end_date: form.end_date };
  }
  if (form.period_type === 'voyage') {
    const voyageId = Number(form.voyage_id);
    if (!Number.isSafeInteger(voyageId) || voyageId <= 0 || voyageId > 2147483647) throw new Error('Chọn chuyến tàu.');
    return { voyage_id: voyageId };
  }
  throw new Error('Chọn kỳ kế hoạch.');
}

export function planPeriodLabel(plan) {
  const date = (value) => /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? value.split('-').reverse().join('/') : '—';
  if (plan.period_type === 'week') {
    const dates = planWeekDates(plan.week);
    return dates ? `Tuần ${plan.week.slice(-2)}/${plan.week.slice(0, 4)} · ${date(dates.start_date)} – ${date(dates.end_date)}` : '—';
  }
  if (plan.period_type === 'month') return plan.month || '—';
  if (plan.period_type === 'quarter') return /^20\d{2}-Q[1-4]$/.test(plan.quarter || '') ? `Quý ${plan.quarter.slice(-1)}/${plan.quarter.slice(0, 4)}` : '—';
  if (plan.period_type === 'year') return `Năm ${plan.year || '—'}`;
  if (plan.period_type === 'custom') return `${date(plan.start_date)} – ${date(plan.end_date)}`;
  return plan.period_type === 'voyage' ? `Chuyến ${plan.voyage_id}` : '—';
}

export function sameClosedReportScope(item, report) {
  const filters = report?.meta?.filters;
  return Boolean(filters && isProductionScope(filters.production_scope)
    && item.production_scope === filters.production_scope
    && item.berth_rule_version === BERTH_RULE_VERSION && report.meta.berth_rule_version === BERTH_RULE_VERSION
    && item.terminal === filters.terminal && item.start_date === filters.start_date && item.end_date === filters.end_date);
}

export function planPeriodEligibility(filters) {
  const month = filters?.start_date?.slice(0, 7);
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month || '')) return { eligible: false, month: '' };
  const [year, number] = month.split('-').map(Number);
  const lastDay = `${month}-${String(new Date(Date.UTC(year, number, 0)).getUTCDate()).padStart(2, '0')}`;
  const endDate = filters?.end_date;
  const eligible = filters.start_date === `${month}-01`
    && /^\d{4}-\d{2}-\d{2}$/.test(endDate || '')
    && endDate >= filters.start_date && endDate <= lastDay;
  return { eligible, month, complete: filters.end_date === lastDay };
}

export function buildPlanPayload(form, allowedTerminals) {
  if (!planTerminals(allowedTerminals, form.period_type).includes(form.terminal)) throw new Error('Chọn xí nghiệp trong phạm vi được cấp. Kế hoạch toàn công ty cần quyền cả hai xí nghiệp và không áp dụng cho một chuyến.');
  if (!Object.hasOwn(METRIC_LABELS, form.metric)) throw new Error('Chọn chỉ tiêu kế hoạch.');
  const value = String(form.amount ?? '').trim();
  const amount = Number(value);
  if (!value || !Number.isFinite(amount) || amount < 0 || amount > 1000000000000) throw new Error('Kế hoạch phải là số từ 0 đến 1.000.000.000.000.');
  const reference = String(form.reference || '').trim();
  if (!reference) throw new Error('Nhập số văn bản hoặc nguồn phê duyệt kế hoạch.');
  if (reference.length > 500) throw new Error('Nguồn phê duyệt tối đa 500 ký tự.');
  const payload = { terminal: form.terminal, period_type: form.period_type, metric: form.metric, amount, reference, note: String(form.note || '').trim() };
  return { ...payload, ...planPeriodFields(form) };
}

export function buildPlanUpdatePayload(form, allowedTerminals) {
  return { week: null, month: null, quarter: null, year: null, start_date: null, end_date: null, voyage_id: null, ...buildPlanPayload(form, allowedTerminals) };
}

export function planDeletePayload(plan) {
  if (!Number.isSafeInteger(plan?.revision) || plan.revision < 1 || plan.is_deleted) {
    throw new Error('Kế hoạch chưa có phiên bản hợp lệ để xóa. Hãy tải lại danh sách.');
  }
  return { revision: plan.revision };
}

export function buildUserPayload(form) {
  const username = String(form.username || '').trim().toLowerCase();
  const displayName = String(form.display_name || '').trim();
  if (!/^[a-z0-9][a-z0-9._-]{2,79}$/.test(username)) throw new Error('Tên đăng nhập gồm 3–80 chữ không dấu, số, dấu chấm, gạch dưới hoặc gạch ngang; bắt đầu bằng chữ hoặc số.');
  if (!displayName) throw new Error('Nhập tên người dùng.');
  if (displayName.length > 160) throw new Error('Tên người dùng tối đa 160 ký tự.');
  if (!Object.hasOwn(MANAGEMENT_ROLES, form.role)) throw new Error('Chọn quyền tài khoản.');
  const terminals = Object.keys(MANAGEMENT_TERMINALS).filter((terminal) => form.terminals?.includes(terminal));
  if (!terminals.length) throw new Error('Chọn ít nhất một xí nghiệp.');
  return { username, display_name: displayName, role: form.role, terminals };
}

export function validatedItems(data) {
  if (!data || !Array.isArray(data.items)) throw new Error('Danh sách trả về chưa đúng cấu trúc. Vui lòng tải lại.');
  return data.items;
}

export function validatedPlanPreview(data, allowedTerminals) {
  if (!data || typeof data.valid !== 'boolean' || !Array.isArray(data.rows) || !Array.isArray(data.errors)) throw new Error('Kết quả kiểm tra Excel chưa đúng cấu trúc. Vui lòng thử lại.');
  if (data.valid && (data.errors.length > 0 || !data.rows.length || data.rows.length > 500)) throw new Error('Danh sách kế hoạch chưa đủ điều kiện nhập.');
  if (data.errors.some((error) => !error || typeof error.message !== 'string' || !Number.isInteger(error.row))) throw new Error('Thông tin lỗi Excel chưa đúng cấu trúc.');
  return { ...data, rows: data.rows.map((row) => buildPlanPayload(row, allowedTerminals)) };
}

export function queryPath(path, values) {
  const query = new URLSearchParams(Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== '').map(([key, value]) => [key, String(value)]));
  return `${path}${query.size ? `?${query}` : ''}`;
}
