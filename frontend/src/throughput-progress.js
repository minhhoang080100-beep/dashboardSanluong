import { BERTH_RULE_VERSION } from './production-scope.js';
import { isoWeekValue, weekDates } from './dashboard-data.js';

export const PROGRESS_BANDS = [
  { upper: 20, label: 'Dưới 20%', color: '#aa6c60', name: 'red' },
  { upper: 40, label: '20–<40%', color: '#bc8a59', name: 'orange' },
  { upper: 60, label: '40–<60%', color: '#b4a054', name: 'yellow' },
  { upper: 80, label: '60–<80%', color: '#91ab82', name: 'light-green' },
  { upper: Infinity, label: 'Từ 80%', color: '#536f67', name: 'dark-green' },
];

const number = (value) => typeof value === 'number' && Number.isFinite(value);
const periodTypes = ['week', 'month', 'quarter', 'year', 'custom'];
const validDate = (value) => /^\d{4}-\d{2}-\d{2}$/.test(value || '') && value >= '1900-01-01' && Number.isFinite(Date.parse(value))
  && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;

function validWeekBounds(type, start, end) {
  if (type !== 'week') return true;
  const week = isoWeekValue(start);
  const bounds = /^20\d{2}-W\d{2}$/.test(week) && weekDates(week, '2100-01-10');
  return Boolean(bounds && bounds.start_date === start && bounds.end_date === end);
}

function validNavigationPeriod(candidate) {
  const { period_type: type, period_key: key, start_date: start, end_date: end } = candidate;
  if (typeof key !== 'string' || candidate.key !== `${type}:${key}`) return false;
  const year = start.slice(0, 4);
  const month = Number(start.slice(5, 7));
  if (type === 'week') return key === isoWeekValue(start) && validWeekBounds(type, start, end);
  if (type === 'custom') return key === `${start}/${end}`;
  if (type === 'year') return key === year && start === `${year}-01-01` && end === `${year}-12-31`;
  if (type === 'month') {
    const last = new Date(Date.UTC(Number(year), month, 0)).toISOString().slice(0, 10);
    return key === start.slice(0, 7) && start.endsWith('-01') && end === last;
  }
  if (type === 'quarter') {
    const quarter = Math.ceil(month / 3);
    const last = new Date(Date.UTC(Number(year), quarter * 3, 0)).toISOString().slice(0, 10);
    return key === `${year}-Q${quarter}` && [1, 4, 7, 10].includes(month) && start.endsWith('-01') && end === last;
  }
  return false;
}

export function reportSelectionForPlan(plan, terminal, today) {
  const start = plan?.period_start || plan?.start_date;
  const end = plan?.period_end || plan?.end_date;
  if (!periodTypes.includes(plan?.period_type) || (plan.metric && plan.metric !== 'tonnage')
    || (plan.status && !['approved', 'ready', 'missing_plan'].includes(plan.status)) || plan.is_current === false
    || !validDate(start) || !validDate(end) || !validDate(today) || end < start
    || !validWeekBounds(plan.period_type, start, end)
    || (Date.parse(end) - Date.parse(start)) / 86400000 >= 366) {
    throw new Error('Kế hoạch chưa đủ điều kiện mở tiến độ sản lượng thông qua.');
  }
  if (start > today) throw new Error('Kỳ kế hoạch chưa bắt đầu; chưa có sản lượng thực tế để đối chiếu.');
  const selectedTerminal = plan.terminal || terminal;
  if (!['all', 'cua_lo', 'ben_thuy'].includes(selectedTerminal)) throw new Error('Phạm vi kế hoạch không hợp lệ.');
  return { filters: { start_date: start, end_date: end < today ? end : today, terminal: selectedTerminal, production_scope: 'nghe_tinh' },
    periodType: plan.period_type, periodKey: plan.key || `${plan.period_type}:${plan.period_key}` };
}

export function progressPeriodOptions(value) {
  return value?.available_periods || value?.items || [];
}

export function completionView(item) {
  const calculable = ['ready', 'empty', 'partial'].includes(item?.actual_status)
    && number(item.actual) && item.actual >= 0 && number(item.target) && item.target > 0;
  const provisional = calculable && item?.actual_status === 'partial';
  if (!calculable) return { percent: null, width: 0, band: null, provisional, achieved: false };
  // Keep the exact percentage for threshold selection: 19.96 must stay below
  // 20, and 99.96 must not be announced as achieved after display rounding.
  const rawPercent = item.actual / item.target * 100;
  // Decimal source values can land a few binary ULPs below an exact threshold
  // (0.565 / 2.825 yields 19.999999999999996 in JS). Snap only floating-point
  // noise, never a business rounding step such as 19.96 -> 20.
  const threshold = [20, 40, 60, 80, 100].find((boundary) =>
    Math.abs(rawPercent - boundary) <= Number.EPSILON * Math.max(1, rawPercent) * 8);
  const percent = threshold ?? rawPercent;
  return { percent, width: Math.min(100, percent), band: PROGRESS_BANDS.find((band) => percent < band.upper),
    provisional, achieved: !provisional && percent >= 100 };
}

export function progressPeriodLabel(item) {
  const date = item.start_date || '';
  if (item.period_type === 'week') {
    const week = isoWeekValue(date);
    return week ? `Tuần ${Number(week.slice(-2))}/${week.slice(0, 4)}` : 'Tuần';
  }
  if (item.period_type === 'month') return `Tháng ${Number(date.slice(5, 7))}/${date.slice(0, 4)}`;
  if (item.period_type === 'quarter') return `Quý ${Math.ceil(Number(date.slice(5, 7)) / 3)}/${date.slice(0, 4)}`;
  if (item.period_type === 'year') return `Năm ${date.slice(0, 4)}`;
  return 'Kỳ tùy chọn';
}

export function planProvenance(item) {
  const references = [...new Set((Array.isArray(item?.plans) ? item.plans : [])
    .map((plan) => typeof plan?.reference === 'string' ? plan.reference.trim() : '').filter(Boolean))];
  return { references, isTest: references.some((reference) => /^TEST(?:[-\s]|$)/i.test(reference)) };
}

export function selectProgressItem(items, selectedKey, preferredPeriodType) {
  return items.find((item) => item.key === selectedKey)
    || items.find((item) => item.period_type === preferredPeriodType)
    || periodTypes.map((kind) => items.find((item) => item.period_type === kind)).find(Boolean) || null;
}

export function validateThroughputProgress(value, report, permittedTerminals = null) {
  const filters = report?.meta?.filters;
  const fail = () => { throw new Error('Tiến độ kế hoạch chưa khớp kỳ và phạm vi báo cáo. Vui lòng tải lại.'); };
  if (!filters || !value || value.report_id !== report.meta.report_id
    || value.production_scope !== filters.production_scope || value.berth_rule_version !== BERTH_RULE_VERSION
    || typeof value.eligible !== 'boolean' || !Array.isArray(value.items)) fail();
  if (value.eligible && filters.production_scope !== 'nghe_tinh') fail();
  for (const field of ['start_date', 'end_date', 'terminal', 'production_scope']) {
    if (value.period?.[field] !== filters[field]) fail();
  }
  const keys = new Set();
  for (const item of value.items) {
    if (!item || typeof item.key !== 'string' || !item.key || keys.has(item.key)
      || !periodTypes.includes(item.period_type)
      || !validWeekBounds(item.period_type, item.start_date, item.end_date)
      || item.start_date !== filters.start_date || !/^\d{4}-\d{2}-\d{2}$/.test(item.end_date || '')
      || item.end_date < filters.end_date || (item.target !== null && (!number(item.target) || item.target < 0))
      || (item.actual !== null && !number(item.actual))
      || !['ready', 'empty', 'partial', 'unavailable'].includes(item.actual_status)) fail();
    const endTime = Date.parse(`${item.end_date}T00:00:00Z`);
    if (!Number.isFinite(endTime) || new Date(endTime).toISOString().slice(0, 10) !== item.end_date
      || (endTime - Date.parse(item.start_date)) / 86400000 >= 366) fail();
    // The numerator must be from the displayed immutable report snapshot.
    if (item.actual !== report.overview.total_tonnage || item.actual_status !== report.overview.tonnage_status) fail();
    keys.add(item.key);
  }
  if (!value.eligible && value.items.length) fail();
  if (value.available_periods !== undefined) {
    if (!Array.isArray(value.available_periods) || (!value.eligible && value.available_periods.length)) fail();
    const options = new Map();
    for (const candidate of value.available_periods) {
      if (!candidate || typeof candidate.key !== 'string' || !candidate.key || options.has(candidate.key)
        || !periodTypes.includes(candidate.period_type) || !validDate(candidate.start_date) || !validDate(candidate.end_date)
        || !validWeekBounds(candidate.period_type, candidate.start_date, candidate.end_date)
        || candidate.end_date < candidate.start_date || (Date.parse(candidate.end_date) - Date.parse(candidate.start_date)) / 86400000 >= 366
        || (candidate.terminal && candidate.terminal !== filters.terminal)
        || (candidate.target !== null && (!number(candidate.target) || candidate.target < 0))
        || Object.hasOwn(candidate, 'actual') || Object.hasOwn(candidate, 'completion_percent')) fail();
      options.set(candidate.key, candidate);
    }
    for (const item of value.items) {
      const candidate = options.get(item.key);
      if (!candidate || ['period_type', 'start_date', 'end_date', 'target'].some((field) => candidate[field] !== item[field])) fail();
    }
  }
  if (value.other_scope_periods !== undefined) {
    if (!Array.isArray(value.other_scope_periods)
      || ((!value.eligible || value.items.length > 0) && value.other_scope_periods.length)
      || (permittedTerminals !== null && !Array.isArray(permittedTerminals))) fail();
    const metadataFields = new Set(['key', 'period_type', 'period_key', 'start_date', 'end_date', 'terminal', 'target']);
    const alternatives = new Set();
    for (const candidate of value.other_scope_periods) {
      if (!candidate || Object.keys(candidate).some((field) => !metadataFields.has(field))
        || !periodTypes.includes(candidate.period_type) || !validDate(candidate.start_date) || !validDate(candidate.end_date)
        || candidate.end_date < candidate.start_date || (Date.parse(candidate.end_date) - Date.parse(candidate.start_date)) / 86400000 >= 366
        || !validNavigationPeriod(candidate)
        || !['all', 'cua_lo', 'ben_thuy'].includes(candidate.terminal) || candidate.terminal === filters.terminal
        || (permittedTerminals !== null && !permittedTerminals.includes(candidate.terminal))
        || !number(candidate.target) || candidate.target < 0) fail();
      const identity = `${candidate.terminal}/${candidate.key}`;
      if (alternatives.has(identity)) fail();
      alternatives.add(identity);
    }
  }
  return value;
}
