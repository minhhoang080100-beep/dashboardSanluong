export const PRODUCTION_SCOPES = {
  nghe_tinh: 'Cảng Nghệ Tĩnh',
  vietsun: 'Cầu 5',
  unclassified: 'Chưa xác định cầu',
};
export const REPORT_PRODUCTION_SCOPES = {
  nghe_tinh: PRODUCTION_SCOPES.nghe_tinh,
  vietsun: PRODUCTION_SCOPES.vietsun,
};
export const BERTH_RULE_VERSION = 'initial-berth-v1';
export const isProductionScope = (value) => Object.hasOwn(PRODUCTION_SCOPES, value);
export const isReportProductionScope = (value) => Object.hasOwn(REPORT_PRODUCTION_SCOPES, value);
export const productionScopeLabel = (value) => isProductionScope(value) ? PRODUCTION_SCOPES[value] : 'Chưa lưu phạm vi';
const scopeDescriptions = {
  nghe_tinh: 'Toàn chuyến được tính theo cầu cập đầu tiên; không gồm chuyến cập Cầu 5 trước tiên tại Cửa Lò.',
  vietsun: 'Các chuyến cập Cầu 5 trước tiên tại Cửa Lò; chuyển cầu sau không đổi phạm vi.',
  unclassified: 'Chưa đủ dữ liệu xác định cầu cập đầu tiên. Sản lượng được giữ riêng để đối soát, chưa cộng vào Cảng Nghệ Tĩnh hoặc Cầu 5.',
};
export const productionScopeDescription = (value) => isProductionScope(value) ? scopeDescriptions[value] : '';
export const berthAssignmentLabel = (value) => ({ assigned: 'Đã xác định', missing: 'Chưa có dữ liệu cầu đầu', ambiguous: 'Lịch sử cầu chưa rõ' }[value] || 'Chưa xác định');
export function initialBerthLabel(voyage) {
  if (typeof voyage?.initial_berth_code === 'string' && voyage.initial_berth_code) return voyage.initial_berth_code;
  return typeof voyage?.initial_berth_id === 'number' && Number.isFinite(voyage.initial_berth_id) ? `ID ${voyage.initial_berth_id}` : 'Chưa xác định';
}
