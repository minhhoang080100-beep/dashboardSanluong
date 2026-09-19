import { weekDates } from './dashboard-data.js';

export function milestoneBounds(form) {
  if (form.period_type === 'week') return weekDates(form.week, '2100-01-10');
  if (form.period_type === 'custom') return { start_date: form.start_date, end_date: form.end_date };
  let year, first, last;
  if (form.period_type === 'month' && /^20\d{2}-\d{2}$/.test(form.month || '')) {
    year = Number(form.month.slice(0, 4)); first = last = Number(form.month.slice(5));
  } else if (form.period_type === 'quarter' && /^20\d{2}-Q[1-4]$/.test(form.quarter || '')) {
    year = Number(form.quarter.slice(0, 4)); last = Number(form.quarter.slice(-1)) * 3; first = last - 2;
  } else if (form.period_type === 'year' && /^20\d{2}$/.test(String(form.year || ''))) {
    year = Number(form.year); first = 1; last = 12;
  } else return null;
  if (first < 1 || last > 12) return null;
  return { start_date: `${year}-${String(first).padStart(2, '0')}-01`, end_date: new Date(Date.UTC(year, last, 0)).toISOString().slice(0, 10) };
}

const units = (value) => { const [whole, part = ''] = value.split('.'); return BigInt(whole) * 1000000n + BigInt(part.padEnd(6, '0')); };

export function validateMilestones(rows, form, amount, parseAmount) {
  if (!Array.isArray(rows) || rows.length > 366) throw new Error('Tối đa 366 mốc kế hoạch.');
  if (!rows.length) return [];
  const bounds = milestoneBounds(form);
  if (!bounds || form.metric !== 'tonnage') throw new Error('Mốc tiến độ áp dụng cho kế hoạch tấn theo kỳ.');
  let previousDate = '', previousAmount = 0n;
  return rows.map((row) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(row.date || '') || !Number.isFinite(Date.parse(row.date))
      || new Date(`${row.date}T00:00:00Z`).toISOString().slice(0, 10) !== row.date
      || row.date < bounds.start_date || row.date > bounds.end_date || row.date <= previousDate) {
      throw new Error('Mốc phải nằm trong kỳ, theo thứ tự tăng dần và không trùng ngày.');
    }
    const value = parseAmount(row.amount);
    const exact = units(value);
    if (exact < previousAmount || exact > units(amount)) throw new Error('Chỉ tiêu lũy kế phải tăng hoặc giữ nguyên và không vượt kế hoạch cả kỳ.');
    previousDate = row.date; previousAmount = exact;
    return { date: row.date, amount: value };
  });
}

export function validMilestonePace(pace, item, report) {
  if (pace === undefined) return true;
  if (!pace || !['unknown', 'ready'].includes(pace.status)) return false;
  if (pace.status === 'unknown') return typeof pace.reason === 'string' && pace.actual === null && pace.difference === null && pace.completion_percent === null;
  const finite = (value) => typeof value === 'number' && Number.isFinite(value);
  const close = (a, b) => Math.abs(a - b) <= Math.max(0.000001, Math.abs(b) * Number.EPSILON * 1000);
  const day = pace.milestone_date;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day || '') || day < item.start_date || day > report.meta.filters.end_date
    || !finite(pace.target) || pace.target < 0 || !finite(pace.actual) || pace.actual < 0
    || !finite(pace.difference) || !close(pace.difference, pace.actual - pace.target)
    || (pace.target > 0 ? !finite(pace.completion_percent) || !close(pace.completion_percent, pace.actual / pace.target * 100) : pace.completion_percent !== null)
    || !Array.isArray(report.daily_history)) return false;
  const seen = new Set(); let actual = 0;
  for (const row of report.daily_history) {
    if (row.date < item.start_date || row.date > day) continue;
    if (seen.has(row.date) || !['ready', 'empty'].includes(row.tonnage_status) || !finite(row.tonnage) || row.tonnage < 0) return false;
    seen.add(row.date); actual += row.tonnage;
  }
  return seen.size === (Date.parse(day) - Date.parse(item.start_date)) / 86400000 + 1 && close(actual, pace.actual);
}
