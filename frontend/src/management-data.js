export const MANAGEMENT_TERMINALS = { cua_lo: 'Cửa Lò', ben_thuy: 'Bến Thủy' };
export const MANAGEMENT_ROLES = { viewer: 'Xem báo cáo', manager: 'Quản lý', admin: 'Quản trị' };
export const ISSUE_LABELS = { missing_weight: 'Thiếu trọng lượng', unknown_unit: 'Chưa xác định đơn vị', negative: 'Giá trị âm' };
export const ISSUE_STATUS_LABELS = { open: 'Đang xử lý', resolved: 'Đã xử lý', ignored: 'Không xử lý' };
export const PLAN_STATUS_LABELS = { draft: 'Bản nháp', approved: 'Đã duyệt', superseded: 'Đã thay thế', cancelled: 'Đã hủy' };
export const METRIC_LABELS = { tonnage: 'Sản lượng (tấn)', teu: 'Container (TEU)' };

export function userTerminals(user) {
  if (!Array.isArray(user?.terminals)) return [];
  return Object.keys(MANAGEMENT_TERMINALS).filter((terminal) => user.terminals.includes(terminal));
}

export function canManage(user) {
  return user?.role === 'manager' || user?.role === 'admin';
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
  if (!allowedTerminals.includes(form.terminal)) throw new Error('Chọn xí nghiệp trong phạm vi được cấp.');
  if (!Object.hasOwn(METRIC_LABELS, form.metric)) throw new Error('Chọn chỉ tiêu kế hoạch.');
  const value = String(form.amount ?? '').trim();
  const amount = Number(value);
  if (!value || !Number.isFinite(amount) || amount < 0 || amount > 1000000000000) throw new Error('Kế hoạch phải là số từ 0 đến 1.000.000.000.000.');
  const reference = String(form.reference || '').trim();
  if (!reference) throw new Error('Nhập số văn bản hoặc nguồn phê duyệt kế hoạch.');
  if (reference.length > 500) throw new Error('Nguồn phê duyệt tối đa 500 ký tự.');
  const payload = { terminal: form.terminal, period_type: form.period_type, metric: form.metric, amount, reference, note: String(form.note || '').trim() };
  if (form.period_type === 'month') {
    if (!/^20\d{2}-(0[1-9]|1[0-2])$/.test(form.month || '')) throw new Error('Chọn tháng kế hoạch từ năm 2000 đến 2099.');
    payload.month = form.month;
  } else if (form.period_type === 'voyage') {
    const voyageId = Number(form.voyage_id);
    if (!Number.isSafeInteger(voyageId) || voyageId <= 0 || voyageId > 2147483647) throw new Error('Chọn chuyến tàu.');
    payload.voyage_id = voyageId;
  } else throw new Error('Chọn kỳ kế hoạch.');
  return payload;
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
