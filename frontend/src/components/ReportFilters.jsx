import { CalendarDays, Check, RefreshCw, SlidersHorizontal } from 'lucide-react';
import { comparisonMode, COMPARISONS, formatDate, isoWeekValue, presetDates, TERMINALS } from '../dashboard-data';
import { allowedTerminals } from '../filter-preferences';
import { REPORT_PERIOD_TYPES, reportPeriodDates, reportWeekOptions } from '../report-period';
import './ReportFilters.css';

const SHORTCUTS = {
  day: [['today', 'Hôm nay'], ['yesterday', 'Hôm qua']],
  week: [['week', 'Tuần này'], ['previous-week', 'Tuần trước']],
  month: [['month', 'Tháng này'], ['previous', 'Tháng trước']],
  year: [['year', 'Từ đầu năm']],
};

function FilterField({ id, label, className = '', children }) {
  return <div className={`report-field ${className}`}><label htmlFor={id}>{label}</label>{children}</div>;
}

export default function ReportFilters({ user, selection, draft, today, loading, hasDraft, error, onChange, onTypeChange, onTerminalChange, onComparisonChange, onPreset, onSubmit, onRefresh }) {
  const { type } = selection;
  const dates = reportPeriodDates(selection, today);
  const maxYear = type === 'week' ? Math.max(Number(today.slice(0, 4)), Number(isoWeekValue(today).slice(0, 4))) : Number(today.slice(0, 4));
  const years = Array.from({ length: maxYear - 1899 }, (_, index) => String(maxYear - index));
  const weeks = type === 'week' ? reportWeekOptions(selection.year, today) : [];
  const shortcuts = SHORTCUTS[type] || [];
  const change = (field, value) => {
    const next = { ...selection, [field]: value };
    if (field === 'year' && type === 'week') {
      const candidate = `${value}-${selection.week.slice(5)}`;
      next.week = reportWeekOptions(value, today).some((week) => week.value === candidate && !week.disabled) ? candidate : '';
    }
    onChange(next);
  };
  const yearField = <FilterField id="report-year" label="Năm"><select id="report-year" value={selection.year} onChange={(event) => change('year', event.target.value)}>{years.map((year) => <option key={year} value={year}>{year}</option>)}</select></FilterField>;
  const paired = ['week', 'month', 'quarter', 'custom'].includes(type);

  return <section className="filter-panel" aria-label="Bộ lọc báo cáo">
    <div className="filter-top"><h2><SlidersHorizontal size={17} aria-hidden="true" />Kỳ báo cáo</h2><p>Chọn loại kỳ, thời gian và bấm “Xem báo cáo”.</p></div>
    <form className="filter-form" onSubmit={onSubmit} noValidate>
      <FilterField className="report-kind-field" id="report-period-type" label="Loại kỳ"><select id="report-period-type" value={type} onChange={(event) => onTypeChange(event.target.value)}>{Object.entries(REPORT_PERIOD_TYPES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></FilterField>
      <div className="report-period-fields" data-paired={paired} data-period={type}>
        {type === 'day' && <FilterField id="report-day" label="Ngày"><input id="report-day" type="date" min="1900-01-01" max={today} value={selection.day} onChange={(event) => change('day', event.target.value)} required /></FilterField>}
        {['week', 'month', 'quarter', 'year'].includes(type) && yearField}
        {type === 'week' && <FilterField id="report-week" label="Tuần"><select id="report-week" value={selection.week} onChange={(event) => change('week', event.target.value)} required><option value="" disabled>Chọn tuần</option>{weeks.map((week) => <option key={week.value} value={week.value} disabled={week.disabled}>{week.label}{week.disabled ? ' · Chưa bắt đầu' : ''}</option>)}</select></FilterField>}
        {type === 'month' && <FilterField id="report-month" label="Tháng"><select id="report-month" value={selection.month} onChange={(event) => change('month', event.target.value)}>{Array.from({ length: 12 }, (_, index) => String(index + 1).padStart(2, '0')).map((month) => <option key={month} value={month} disabled={`${selection.year}-${month}-01` > today}>Tháng {Number(month)}</option>)}</select></FilterField>}
        {type === 'quarter' && <FilterField id="report-quarter" label="Quý"><select id="report-quarter" value={selection.quarter} onChange={(event) => change('quarter', event.target.value)}>{[1, 2, 3, 4].map((quarter) => <option key={quarter} value={String(quarter)} disabled={`${selection.year}-${String(quarter * 3 - 2).padStart(2, '0')}-01` > today}>Quý {quarter} · Tháng {quarter * 3 - 2}–{quarter * 3}</option>)}</select></FilterField>}
        {type === 'custom' && <>
          <FilterField id="start-date" label="Từ ngày"><input id="start-date" type="date" min="1900-01-01" max={today} value={selection.start_date} onChange={(event) => change('start_date', event.target.value)} required /></FilterField>
          <FilterField id="end-date" label="Đến ngày"><input id="end-date" type="date" min={selection.start_date || '1900-01-01'} max={today} value={selection.end_date} onChange={(event) => change('end_date', event.target.value)} required /></FilterField>
        </>}
      </div>
      <FilterField className="report-terminal-field" id="terminal" label="Phạm vi xí nghiệp"><select id="terminal" value={draft.terminal} onChange={(event) => onTerminalChange(event.target.value)}>{allowedTerminals(user).map((key) => <option key={key} value={key}>{TERMINALS[key]}</option>)}</select></FilterField>
      <FilterField className="report-comparison-field" id="report-comparison" label="So sánh"><select id="report-comparison" value={comparisonMode(draft)} onChange={(event) => onComparisonChange(event.target.value)}>{Object.entries(COMPARISONS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></FilterField>
      <div className="filter-actions"><button className="button primary" type="submit"><Check size={16} aria-hidden="true" />Xem báo cáo</button><button className="button icon-button" type="button" aria-label="Tải lại báo cáo đang chọn" title="Tải lại báo cáo đang chọn" disabled={loading} onClick={onRefresh}><RefreshCw size={17} aria-hidden="true" /></button></div>
      <div className="report-period-preview" aria-live="polite"><CalendarDays size={18} aria-hidden="true" /><div><span>Khoảng thời gian sẽ xem</span><strong>{dates ? `${formatDate(dates.start_date)} – ${formatDate(dates.end_date)}` : 'Chọn thời gian hợp lệ để xem báo cáo.'}</strong>{type === 'week' && <small>Tuần từ thứ Hai đến Chủ nhật.{dates?.end_date === today ? ' Tuần hiện tại lấy đến hôm nay.' : ''}</small>}{['month', 'quarter', 'year'].includes(type) && dates?.end_date === today && <small>Kỳ hiện tại lấy đến hôm nay.</small>}</div>
        {shortcuts.length > 0 && <div className="report-period-shortcuts" role="group" aria-label="Chọn nhanh kỳ báo cáo">{shortcuts.map(([key, label]) => {
          const period = presetDates(key, today);
          return <button type="button" key={key} aria-pressed={Boolean(dates && period && dates.start_date === period.start_date && dates.end_date === period.end_date)} onClick={() => onPreset(key)}>{label}</button>;
        })}</div>}
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      {hasDraft && !error && <p className="draft-note">Chưa áp dụng thay đổi. Bấm “Xem báo cáo” để cập nhật số liệu.</p>}
    </form>
  </section>;
}
