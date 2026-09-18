import { presetDates, validateFilters } from './dashboard-data.js';
import { isReportProductionScope } from './production-scope.js';

export function allowedTerminals(user) {
  const scopes = ['cua_lo', 'ben_thuy'].filter((value) => Array.isArray(user?.terminals) && user.terminals.includes(value));
  return scopes.length === 2 ? ['all', ...scopes] : scopes;
}

export function restoreFilters(user) {
  const scopes = allowedTerminals(user);
  const defaults = { ...presetDates('month'), terminal: scopes[0] || 'cua_lo', production_scope: 'nghe_tinh' };
  try {
    const saved = JSON.parse(localStorage.getItem(`port-report-filters-${user.id}`));
    if (!saved || !scopes.includes(saved.terminal) || validateFilters(saved)) return defaults;
    return isReportProductionScope(saved.production_scope) ? saved : { ...saved, production_scope: 'nghe_tinh' };
  } catch { return defaults; }
}

export function rememberFilters(user, filters) {
  try { localStorage.setItem(`port-report-filters-${user.id}`, JSON.stringify(filters)); } catch { /* Optional browser preference. */ }
}
