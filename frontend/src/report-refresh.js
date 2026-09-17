import { todayInVietnam } from './dashboard-data.js';

export const AUTO_REFRESH_MS = 120000;
export function reportRequestRefresh(request, filterKey, handledRevision) {
  // A refresh action belongs to one selection and is consumed when it starts.
  // Returning to that selection later must use its cached report normally.
  return request.revision !== handledRevision && request.filterKey === filterKey && request.forceRefresh === true;
}
export function currentReportPeriod(filters, today = todayInVietnam()) {
  return Boolean(filters?.start_date && filters.start_date <= today && filters.end_date === today);
}
export function shouldAutoRefresh({ enabled, filters, today, visible, reportView, loading, hasDraft, modalOpen, editing }) {
  return Boolean(enabled && currentReportPeriod(filters, today) && visible && reportView && !loading && !hasDraft && !modalOpen && !editing);
}
export function restoreAutoRefresh(user) {
  try { return localStorage.getItem(`port-report-auto-refresh-${user.id}`) === 'true'; } catch { return false; }
}
export function rememberAutoRefresh(user, enabled) {
  try { localStorage.setItem(`port-report-auto-refresh-${user.id}`, String(enabled)); } catch { /* Optional preference. */ }
}
