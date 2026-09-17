import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ArrowDownRight, ArrowUpRight, ArrowUpRight as OpenArrow, BarChart3, Boxes, CalendarDays, Check, CircleAlert, Clock3, Database, Download, Info, MapPin, Package, RefreshCw, Ship, SlidersHorizontal, Users, Warehouse } from 'lucide-react';
import { dashboardCsv, dashboardRequestTimeout, dashboardResourceView, fetchDashboard, formatDate, formatNumber, formatTimestamp, isNumber, presetDates, ratioAvailability, TERMINALS, todayInVietnam, validateFilters } from '../dashboard-data';
import Voyages from './Voyages';
import CargoBreakdown from './CargoBreakdown';
import Management from './Management';
import ThroughputProgress from './ThroughputProgress';
import OperationsExplorer from './OperationsExplorer';
import { allowedTerminals, rememberFilters, restoreFilters } from '../filter-preferences';
import { AUTO_REFRESH_MS, currentReportPeriod, rememberAutoRefresh, reportRequestRefresh, restoreAutoRefresh, shouldAutoRefresh } from '../report-refresh';
import { PRODUCTION_SCOPES, isProductionScope, productionScopeDescription, productionScopeLabel } from '../production-scope';
import { reportSelectionForPlan } from '../throughput-progress';
import './Dashboard.css';

const COLORS = ['#64877b', '#8094a0', '#ac966e', '#8c91a1', '#8b9b7d', '#a08a96'];
const API_BASE = import.meta.env.VITE_API_URL || '/api';
const History = lazy(() => import('./History'));

function EmptyPanel({ message = 'Không có phát sinh trong kỳ đã chọn.' }) {
  return <div className="empty-panel"><BarChart3 size={27} aria-hidden="true" /><p>{message}</p></div>;
}

function Trend({ value }) {
  if (!isNumber(value)) return null;
  if (value === 0) return <span className="trend neutral">0% so với kỳ trước</span>;
  const Icon = value > 0 ? ArrowUpRight : ArrowDownRight;
  return <span className={`trend ${value > 0 ? 'up' : 'down'}`}><Icon size={14} aria-hidden="true" />{value > 0 ? 'Tăng' : 'Giảm'} {formatNumber(Math.abs(value), 1)}%<span>so với kỳ trước</span></span>;
}

function Kpi({ title, value, unit, trend, status, icon: Icon, accent, digits = 3, listLink, onInspect }) {
  return (
    <article className={`kpi-card ${accent ? 'kpi-primary' : ''}`}>
      <div className="kpi-top"><span>{title}</span><span className="kpi-icon"><Icon size={19} aria-hidden="true" /></span></div>
      <p className="kpi-value">{onInspect ? <button type="button" className="inspect-value" onClick={onInspect} aria-label={`Xem chi tiết ${title}`}>{formatNumber(value, digits)}</button> : formatNumber(value, digits)}<span>{unit}</span></p>
      <div className="kpi-bottom"><Trend value={['partial', 'unavailable'].includes(status) ? null : trend} />
      {listLink && <a className="kpi-link" href={listLink}>Xem danh sách <OpenArrow size={13} aria-hidden="true" /></a>}</div>
    </article>
  );
}

function NativeUnits({ rows }) {
  if (!rows?.length) return null;
  return <section className="panel native-units-panel" aria-labelledby="native-units-title">
    <div className="panel-heading"><div><h2 id="native-units-title">Sản lượng chưa cộng vào tấn</h2><p>Các đơn vị dưới đây được trình bày riêng, không cộng với nhau hoặc với chỉ tiêu tấn.</p></div><Package size={22} className="heading-icon" aria-hidden="true" /></div>
    <div className="table-scroll"><table className="native-units-table"><caption className="sr-only">Số liệu ngoài chỉ tiêu tấn theo xí nghiệp và đơn vị gốc</caption><thead><tr><th scope="col">Xí nghiệp</th><th scope="col">Đơn vị nguồn</th><th scope="col">Sản lượng ghi nhận</th><th scope="col">Số bản ghi</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.terminal_id}-${row.unit_code}-${index}`}><th scope="row">{row.terminal_name}</th><td>{row.unit_name || 'Chưa xác định'}{row.unit_code && <small className="native-unit-code">{row.unit_code}</small>}</td><td>{formatNumber(row.value)}</td><td>{formatNumber(row.record_count, 0)}</td></tr>)}</tbody></table></div>
  </section>;
}

function Breakdown({ title, subtitle, rows, total, hasSignedInput, icon: Icon }) {
  const ratios = ratioAvailability(rows.map((row) => row.tonnage), total, hasSignedInput);
  return (
    <article className="panel breakdown-panel">
      <div className="panel-heading"><div><h3><Icon size={17} aria-hidden="true" />{title}</h3><p>{subtitle}</p></div></div>
      {!rows.length ? <EmptyPanel /> : <div className="breakdown-list">
        {rows.map((row, index) => (
          <div className="breakdown-row" key={`${row.name}-${index}`}>
            <div className="breakdown-label"><span><i style={{ backgroundColor: COLORS[index % COLORS.length] }} />{row.name}</span>{ratios.available && <strong>{formatNumber(row.tonnage / total * 100, 1)}%</strong>}</div>
            {ratios.available && <div className="bar-track" aria-hidden="true"><span style={{ width: `${row.tonnage / total * 100}%`, backgroundColor: COLORS[index % COLORS.length] }} /></div>}
            <p>{formatNumber(row.tonnage)} <span>tấn</span></p>
          </div>
        ))}
      </div>}
    </article>
  );
}

function Terminals({ rows, total, hasSignedInput, onInspect }) {
  const ratios = ratioAvailability(rows.map((row) => row.tonnage), total, hasSignedInput);
  return <article className="panel terminal-panel">
    <div className="panel-heading"><div><h3><MapPin size={17} aria-hidden="true" />Sản lượng theo xí nghiệp</h3><p>Tỷ trọng theo sản lượng qua cảng</p></div></div>
    {!rows.length ? <EmptyPanel /> : <div className="terminal-list">{rows.map((row, index) => {
      const share = ratios.available ? row.tonnage / total * 100 : null;
      return <div className="terminal-item" key={row.name}>
        <div className="terminal-name"><span className="terminal-number">0{index + 1}</span><h4>{onInspect ? <button className="inspect-label" type="button" onClick={(event) => onInspect(row, event)}>{row.name}</button> : row.name}</h4>{ratios.available && <strong>{formatNumber(share, 1)}%</strong>}</div>
        <div className="terminal-value">{formatNumber(row.tonnage)} <span>tấn</span></div>
        {ratios.available && <div className="bar-track" aria-hidden="true"><span style={{ width: `${share}%`, backgroundColor: COLORS[index % COLORS.length] }} /></div>}
        <p className="terminal-teu"><Boxes size={13} aria-hidden="true" />{formatNumber(row.teu)} TEU</p>
      </div>;
    })}</div>}
  </article>;
}

function Customers({ rows, total, hasSignedInput, onInspect }) {
  const ratios = ratioAvailability(rows.map((row) => row.volume), total, hasSignedInput);
  return <section className="panel customer-panel" id="customers" aria-labelledby="customers-title">
    <div className="panel-heading"><div><h2 id="customers-title">Sản lượng theo khách hàng</h2><p>Xếp hạng theo tấn trong kỳ báo cáo</p></div><Users size={22} className="heading-icon" aria-hidden="true" /></div>
    {!rows.length ? <EmptyPanel /> : <div className="table-scroll"><table className="customer-table">
      <caption className="sr-only">Khách hàng có sản lượng qua cảng lớn nhất trong phạm vi đã chọn</caption>
      <thead><tr><th scope="col">Hạng</th><th scope="col">Khách hàng</th><th scope="col">Sản lượng <span>(tấn)</span></th><th scope="col">Tỷ trọng toàn kỳ</th></tr></thead>
      <tbody>{rows.map((row, index) => {
        const share = ratios.available ? row.volume / total * 100 : null;
        return <tr key={`${row.name}-${index}`}><td><span className={`rank ${index === 0 ? 'rank-first' : ''}`}>{String(index + 1).padStart(2, '0')}</span></td><th scope="row">{onInspect ? <button className="inspect-label" type="button" onClick={(event) => onInspect(row, event)}>{row.name}</button> : row.name}{row.terminal_name && <small className="customer-terminal">{row.terminal_name}</small>}</th><td>{formatNumber(row.volume)}</td><td>{ratios.available ? <div className="customer-share"><span>{formatNumber(share, 1)}%</span><div className="bar-track" aria-hidden="true"><span style={{ width: `${share}%` }} /></div></div> : '—'}</td></tr>;
      })}</tbody>
    </table></div>}
  </section>;
}

const definitionNames = { tonnage: 'Sản lượng thông qua', measured_tonnage: 'Khối lượng ghi nhận', native_units: 'Các đơn vị nguồn khác', teu: 'Container (TEU)', throughput: 'Phạm vi sản lượng', vessel_calls: 'Chuyến tàu có phát sinh', cargo: 'Nhóm hàng', customers: 'Khách hàng', history: 'Chuỗi thời gian theo tháng', daily_history: 'Chuỗi thời gian theo ngày', sources: 'Thời điểm dữ liệu nguồn', comparison: 'Kỳ so sánh', consistency: 'Tính nhất quán số liệu' };
function DataQuality({ meta }) {
  const sources = Array.isArray(meta.sources) ? meta.sources : [];
  return <details className="panel quality-panel" id="data-quality">
    <summary className="quality-summary"><Database size={19} aria-hidden="true" /><span>Nguồn dữ liệu & định nghĩa</span></summary>
    <div className="source-grid">
      {sources.length ? sources.map((source) => <article className="source-card" key={source.id || source.name}>
        <h3><Database size={16} aria-hidden="true" />{source.name}</h3>
        <dl><div><dt>Phát sinh mới nhất tại nguồn</dt><dd>{formatTimestamp(source.latest_operation_at)}</dd></div><div><dt>Số bản ghi trong kỳ chọn</dt><dd>{formatNumber(source.record_count)}</dd></div></dl>
        {source.latest_selected_operation_at && <p>Mới nhất trong kỳ: {formatTimestamp(source.latest_selected_operation_at)}</p>}
      </article>) : <p className="muted">Chưa có thông tin thời điểm phát sinh tại nguồn.</p>}
    </div>
    {meta.metric_coverage && <div className="quality-coverage"><h3>Độ đầy đủ số liệu</h3><dl>{[['tonnage', 'Tấn'], ['teu', 'TEU']].map(([key, label]) => {
      const coverage = meta.metric_coverage[key];
      if (!coverage) return null;
      return <div key={key}><dt>{label}</dt><dd>{formatNumber(coverage.known_rows, 0)} bản ghi đủ giá trị / {formatNumber(key === 'teu' ? coverage.container_rows : coverage.eligible_rows, 0)} bản ghi thuộc phạm vi{coverage.excluded_native_rows > 0 ? `; ${formatNumber(coverage.excluded_native_rows, 0)} bản ghi đơn vị khác` : ''}</dd></div>;
    })}</dl></div>}
    {isNumber(meta.data_quality?.empty_unweighed_count) && <div className="quality-coverage"><h3>Dòng chưa ghi trọng lượng</h3><dl>{[['empty_unweighed_count', 'Số lượng bằng 0'], ['missing_weight_with_quantity_count', 'Số lượng khác 0'], ['unweighed_unknown_quantity_count', 'Số lượng cũng chưa ghi']].map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{formatNumber(meta.data_quality[key], 0)} dòng</dd></div>)}</dl><p>Giữ nguyên giá trị chưa ghi trong nguồn; cần đối soát nghiệp vụ trước khi loại khỏi chỉ tiêu độ đầy đủ.</p></div>}
    {meta.warnings.length > 0 && <div className="quality-warnings"><h3><CircleAlert size={16} aria-hidden="true" />Lưu ý về dữ liệu</h3><ul>{meta.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></div>}
    <div className="readiness-grid">
      <article><Clock3 size={19} aria-hidden="true" /><div><h3>Hiệu suất khai thác</h3><span className="unavailable-tag">Chưa đủ cơ sở tính</span><p>{meta.unavailable?.efficiency || 'Chưa có dữ liệu được xác nhận để tính thời gian quay vòng tàu và năng suất xếp dỡ.'}</p></div></article>
      <article><Warehouse size={19} aria-hidden="true" /><div><h3>Sức chứa kho bãi</h3><span className="unavailable-tag">Chưa đủ cơ sở tính</span><p>{meta.unavailable?.yard || 'Chưa có dữ liệu tồn kho và sức chứa được xác nhận để tính tỷ lệ lấp đầy.'}</p></div></article>
    </div>
    <details className="definitions"><summary><Info size={16} aria-hidden="true" />Định nghĩa chỉ tiêu và cách so sánh</summary>
      <dl>{Object.entries(meta.definitions || {}).map(([key, definition]) => <div key={key}><dt>{definitionNames[key] || key}</dt><dd>{definition}</dd></div>)}</dl>
      <p>Kỳ so sánh: {formatDate(meta.previous_period?.start_date)} – {formatDate(meta.previous_period?.end_date)}. {meta.previous_period?.label} Tỷ lệ thay đổi chỉ hiển thị khi hai kỳ đủ cơ sở so sánh và mẫu số kỳ trước dương.</p>
    </details>
    <p className="quality-footnote">Giờ hiển thị: Việt Nam (UTC+7). Tổng hợp lúc: {formatTimestamp(meta.generated_at)}. Bộ lọc áp dụng cho toàn bộ báo cáo.</p>
  </details>;
}

function Dashboard({ user, activeView = 'reports', anchor = '#overview' }) {
  const isReportView = activeView === 'reports';
  const [managementTab, setManagementTab] = useState('plans');
  const usesReportContext = isReportView || (activeView === 'management' && managementTab !== 'plans');
  const [reportRequested, setReportRequested] = useState(isReportView);
  const [filters, setFilters] = useState(() => restoreFilters(user));
  const [draft, setDraft] = useState(filters);
  const [quarterYear, setQuarterYear] = useState(() => Math.max(2000, Number(filters.start_date.slice(0, 4))));
  const [preferredPeriodType, setPreferredPeriodType] = useState(null);
  const [preferredPeriodKey, setPreferredPeriodKey] = useState('');
  const [reportRequest, setReportRequest] = useState({ revision: 0, filterKey: '', forceRefresh: false });
  const [formError, setFormError] = useState('');
  const [exportMessage, setExportMessage] = useState('');
  const [resource, setResource] = useState({ status: 'loading', data: null, error: '', key: '' });
  const [inspection, setInspection] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(() => restoreAutoRefresh(user));
  const [clock, setClock] = useState(Date.now);
  const handledRequest = useRef(0);
  const lastScrolledAnchor = useRef('');
  const scopeButtons = useRef([]);
  const filterKey = JSON.stringify(filters);
  const requestReport = useCallback((forceRefresh) => {
    setReportRequest((previous) => ({ revision: previous.revision + 1, filterKey, forceRefresh }));
  }, [filterKey]);
  const view = useMemo(() => dashboardResourceView(resource, filters), [resource, filters]);
  const loading = ['loading', 'recovering', 'refreshing'].includes(view.status);
  const reportTimeout = dashboardRequestTimeout(filters);
  const data = view.data;
  const error = view.status === 'error' ? view.error : '';
  const hasDraft = JSON.stringify(draft) !== filterKey;
  const hasSignedInput = (data?.meta.data_quality?.negative_value_count || 0) > 0;
  const currentPeriod = currentReportPeriod(filters, todayInVietnam(new Date(clock)));
  const incomplete = ['partial', 'unavailable'].includes(data?.overview.tonnage_status) || ['partial', 'unavailable'].includes(data?.overview.teu_status);
  const sourceTime = Date.parse(data?.meta.source_read_at || data?.meta.generated_at || '');
  const oldSource = currentPeriod && Number.isFinite(sourceTime) && clock - sourceTime > 180000;

  useEffect(() => {
    // Fast Refresh may retain pre-scope filters. Migrate the selection, then
    // fetch a new scoped report; never relabel a retained legacy payload.
    if (!isProductionScope(filters.production_scope)) {
      setFilters((current) => ({ ...current, production_scope: 'nghe_tinh' }));
      setDraft((current) => ({ ...current, production_scope: 'nghe_tinh' }));
    }
  }, [filters.production_scope]);
  useEffect(() => { rememberAutoRefresh(user, autoRefresh); }, [user, autoRefresh]);
  useEffect(() => { const timer = setInterval(() => setClock(Date.now()), 60000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    if (!autoRefresh || !isReportView || !currentPeriod) return;
    const timer = setInterval(() => {
      if (shouldAutoRefresh({ enabled: autoRefresh, filters, visible: !document.hidden, reportView: isReportView, loading, hasDraft,
        modalOpen: Boolean(document.querySelector('dialog[open]')), editing: Boolean(document.activeElement?.matches('input, textarea, select, [contenteditable="true"]')) })) {
        requestReport(true);
      }
    }, AUTO_REFRESH_MS);
    return () => clearInterval(timer);
  }, [autoRefresh, isReportView, currentPeriod, filters, loading, hasDraft, requestReport]);

  useEffect(() => { rememberFilters(user, filters); setInspection(null); }, [user, filters]);
  useEffect(() => {
    if (activeView !== 'management') setManagementTab('plans');
    setInspection(null);
  }, [activeView]);
  useEffect(() => {
    if (usesReportContext) setReportRequested(true);
  }, [usesReportContext]);
  useEffect(() => {
    if (!isReportView || !['#production', '#voyages', '#customers', '#data-quality'].includes(anchor)) {
      lastScrolledAnchor.current = '';
      return;
    }
    if (!data || lastScrolledAnchor.current === anchor) return;
    const frame = requestAnimationFrame(() => {
      const target = document.getElementById(anchor.slice(1));
      if (target) {
        if (anchor === '#data-quality') target.open = true;
        target.scrollIntoView({ block: 'start', behavior: 'instant' });
        lastScrolledAnchor.current = anchor;
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [anchor, isReportView, data]);
  function inspect(selection, label, event) {
    setInspection({ selection, label, trigger: event.currentTarget });
  }

  useEffect(() => {
    // Recover retained state once. A malformed fresh response becomes an explicit
    // fetch error below, so it cannot trigger an automatic retry loop.
    if (view.status === 'recovering') requestReport(false);
  }, [view.status, requestReport]);

  useEffect(() => {
    if (!reportRequested || !isProductionScope(filters.production_scope)) return;
    const controller = new AbortController();
    let active = true;
    let timedOut = false;
    setResource((previous) => {
      // Revalidate retained HMR state before preserving it through a refresh.
      const retained = dashboardResourceView(previous, filters);
      return retained.data ? { ...previous, data: retained.data, status: 'refreshing', error: '' }
        : { status: 'loading', data: null, error: '', key: filterKey };
    });
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, reportTimeout);
    const refresh = reportRequestRefresh(reportRequest, filterKey, handledRequest.current);
    handledRequest.current = reportRequest.revision;
    fetchDashboard(filters, { signal: controller.signal, baseUrl: API_BASE, refresh })
      .then((result) => { if (active) { setResource({ status: 'success', data: result, error: '', key: filterKey }); setClock(Date.now()); } })
      .catch((failure) => {
        if (!active) return;
        if (controller.signal.aborted && !timedOut) return;
        const message = timedOut ? `Truy vấn mất quá ${reportTimeout / 1000} giây. Vui lòng thử lại; nếu tiếp tục lỗi, liên hệ bộ phận CNTT.` : failure instanceof SyntaxError ? 'Máy chủ trả về dữ liệu không hợp lệ. Vui lòng thử lại hoặc liên hệ bộ phận CNTT.' : failure instanceof TypeError ? 'Không kết nối được máy chủ báo cáo. Vui lòng kiểm tra kết nối và thử lại.' : failure.message;
        setResource((previous) => previous.key === filterKey && previous.data
          ? { ...previous, status: 'stale', error: message }
          : { status: 'error', data: null, error: message, key: filterKey });
      })
      .finally(() => clearTimeout(timer));
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [filters, filterKey, reportRequest, reportRequested, reportTimeout]);

  function applyFilters(event) {
    event.preventDefault();
    const validation = validateFilters(draft);
    setFormError(validation);
    if (validation) return;
    setExportMessage('');
    setQuarterYear(Number(draft.start_date.slice(0, 4)));
    setPreferredPeriodType('custom');
    setPreferredPeriodKey('');
    setFilters({ ...draft });
  }

  function applyPreset(preset) {
    const dates = presetDates(preset, todayInVietnam(), quarterYear);
    if (!dates) return;
    const next = { ...draft, ...dates };
    setQuarterYear(Number(dates.start_date.slice(0, 4)));
    setPreferredPeriodType(preset.startsWith('quarter-') ? 'quarter' : preset === 'year' ? 'year' : ['month', 'previous'].includes(preset) ? 'month' : 'custom');
    setPreferredPeriodKey('');
    setDraft(next);
    setFilters(next);
    setFormError('');
    setExportMessage('');
  }

  function selectScope(production_scope) {
    if (production_scope === filters.production_scope) return;
    setFilters((current) => ({ ...current, production_scope }));
    setDraft((current) => ({ ...current, production_scope }));
    setInspection(null);
    setExportMessage('');
  }

  function selectPlan(plan) {
    try {
      const next = reportSelectionForPlan(plan, filters.terminal, todayInVietnam());
      if (!allowedTerminals(user).includes(next.filters.terminal)) throw new Error('Kế hoạch nằm ngoài phạm vi xí nghiệp được cấp.');
      setPreferredPeriodType(next.periodType);
      setPreferredPeriodKey(next.periodKey);
      setQuarterYear(Number(next.filters.start_date.slice(0, 4)));
      setDraft(next.filters);
      if (JSON.stringify(filters) === JSON.stringify(next.filters) && (!data || view.status === 'stale')) requestReport(view.status === 'stale');
      setFilters((current) => JSON.stringify(current) === JSON.stringify(next.filters) ? current : next.filters);
      setInspection(null);
      setFormError('');
      setExportMessage('');
      window.location.hash = '#overview';
    } catch (error) {
      setFormError(error.message);
    }
  }

  function navigateScopes(event, index) {
    const scopes = Object.keys(PRODUCTION_SCOPES);
    const next = event.key === 'ArrowRight' ? (index + 1) % scopes.length : event.key === 'ArrowLeft' ? (index + scopes.length - 1) % scopes.length : event.key === 'Home' ? 0 : event.key === 'End' ? scopes.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    selectScope(scopes[next]);
    scopeButtons.current[next]?.focus();
  }

  function exportCsv() {
    if (!data) return;
    const blob = new Blob([dashboardCsv(data)], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `san-luong-${filters.production_scope}-${filters.terminal}-${filters.start_date}-${filters.end_date}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setExportMessage('Đã tạo tệp CSV theo kỳ báo cáo đang hiển thị.');
  }

  return <div className="dashboard">
    <section className="dashboard-intro" id={isReportView ? 'overview' : undefined} aria-labelledby="dashboard-title">
      <div className="dashboard-title-group"><h1 id="dashboard-title">{isReportView ? 'Báo cáo sản lượng' : activeView === 'admin' ? 'Quản trị' : 'Kế hoạch'}</h1>
      {isReportView && <nav className="report-shortcuts" aria-label="Tra cứu báo cáo"><a href="#production"><BarChart3 size={14} aria-hidden="true" />Phân tích sản lượng</a><a href="#voyages"><Ship size={14} aria-hidden="true" />Chuyến tàu</a><a href="#customers"><Users size={14} aria-hidden="true" />Khách hàng</a></nav>}</div>
      {isReportView && <button className="button secondary export-button" type="button" aria-label="Xuất báo cáo CSV" title="Xuất báo cáo CSV" onClick={exportCsv} disabled={!data || hasDraft}><Download size={16} aria-hidden="true" /><span>Xuất báo cáo CSV</span></button>}
    </section>
    {usesReportContext && <div className="production-scope-selector">
      <div className="production-scope-tabs" role="tablist" aria-label="Phạm vi sản lượng">{Object.entries(PRODUCTION_SCOPES).map(([scope, label], index) => <button key={scope} ref={(element) => { scopeButtons.current[index] = element; }} id={`production-scope-${scope}`} type="button" role="tab" aria-selected={filters.production_scope === scope} aria-controls="production-scope-report" tabIndex={filters.production_scope === scope ? 0 : -1} onClick={() => selectScope(scope)} onKeyDown={(event) => navigateScopes(event, index)}>{label}</button>)}</div>
      <p>{filters.production_scope === 'unclassified' ? productionScopeDescription(filters.production_scope) : 'Theo cầu ban đầu của toàn chuyến; chuyển cầu không đổi phạm vi.'}</p>
    </div>}
    <div id="production-scope-report" role={usesReportContext ? 'tabpanel' : undefined} aria-labelledby={usesReportContext ? `production-scope-${filters.production_scope}` : undefined}>
    {usesReportContext && <>
    <section className="filter-panel" aria-label="Bộ lọc báo cáo">
      <div className="filter-top"><span><SlidersHorizontal size={16} aria-hidden="true" />Kỳ báo cáo</span><div className="preset-buttons" role="group" aria-label="Chọn nhanh kỳ báo cáo">
        {[['today', 'Hôm nay'], ['yesterday', 'Hôm qua'], ['month', 'Tháng này'], ['previous', 'Tháng trước'], ['year', 'Từ đầu năm']].map(([key, label]) => {
          const dates = presetDates(key);
          return <button type="button" key={key} aria-pressed={filters.start_date === dates.start_date && filters.end_date === dates.end_date} onClick={() => applyPreset(key)}>{label}</button>;
        })}
      </div></div>
      <div className="quarter-filter">
        <label htmlFor="quarter-year">Năm xem quý<select id="quarter-year" value={quarterYear} onChange={(event) => setQuarterYear(Number(event.target.value))}>{Array.from({ length: Number(todayInVietnam().slice(0, 4)) - 1999 }, (_, index) => Number(todayInVietnam().slice(0, 4)) - index).map((year) => <option key={year} value={year}>{year}</option>)}</select></label>
        <div className="preset-buttons quarter-buttons" role="group" aria-label="Chọn quý báo cáo">{[1, 2, 3, 4].map((quarter) => {
          const dates = presetDates(`quarter-${quarter}`, todayInVietnam(), quarterYear);
          return <button key={quarter} type="button" disabled={!dates} title={!dates ? 'Quý chưa bắt đầu' : undefined} aria-pressed={Boolean(dates && filters.start_date === dates.start_date && filters.end_date === dates.end_date)} onClick={() => applyPreset(`quarter-${quarter}`)}>Quý {quarter}</button>;
        })}</div>
        <span>Quý hiện tại tính đến hôm nay.</span>
      </div>
      <form className="filter-form" onSubmit={applyFilters}>
        <label htmlFor="start-date">Từ ngày<input id="start-date" type="date" required max={todayInVietnam()} value={draft.start_date} onChange={(event) => { setDraft({ ...draft, start_date: event.target.value }); setFormError(''); }} /></label>
        <label htmlFor="end-date">Đến ngày<input id="end-date" type="date" required max={todayInVietnam()} value={draft.end_date} onChange={(event) => { setDraft({ ...draft, end_date: event.target.value }); setFormError(''); }} /></label>
        <label htmlFor="terminal">Phạm vi xí nghiệp<select id="terminal" value={draft.terminal} onChange={(event) => setDraft({ ...draft, terminal: event.target.value })}>{allowedTerminals(user).map((key) => <option key={key} value={key}>{TERMINALS[key]}</option>)}</select></label>
        <button className="button primary" type="submit"><Check size={16} aria-hidden="true" />Áp dụng</button>
        <button className="button icon-button" type="button" aria-label="Tải lại báo cáo đang chọn" title="Tải lại báo cáo đang chọn" disabled={loading} onClick={() => { requestReport(true); setExportMessage(''); }}><RefreshCw size={17} aria-hidden="true" /></button>
      </form>
      {formError && <p className="form-error" role="alert">{formError}</p>}
      {hasDraft && !formError && <p className="draft-note">Bộ lọc đã thay đổi. Chọn “Áp dụng” để cập nhật báo cáo.</p>}
    </section>
    <div className="report-toolbar">
      <div className="report-context"><span><CalendarDays size={15} aria-hidden="true" /><strong>{formatDate(filters.start_date)} – {formatDate(filters.end_date)}</strong><span className="context-divider" aria-hidden="true">·</span>{TERMINALS[filters.terminal]}<span className="context-divider" aria-hidden="true">·</span><strong>{productionScopeLabel(filters.production_scope)}</strong></span><span className="source-status">{data ? `Đọc nguồn: ${formatTimestamp(data.meta.source_read_at || data.meta.generated_at)}` : 'Giờ Việt Nam · UTC+7'}{incomplete && <a className="data-status-tag" href="#data-quality" onClick={() => { const details = document.getElementById('data-quality'); if (details) details.open = true; }}>Số liệu chưa đầy đủ</a>}{data && (view.status === 'stale' || oldSource) && <span className="data-status-tag">{view.status === 'stale' ? 'Chưa cập nhật được' : 'Dữ liệu hơn 3 phút trước'}</span>}</span></div>
      {isReportView && <div className="report-refresh-control"><label title={currentPeriod ? 'Tạm dừng khi đang nhập, mở chi tiết hoặc chuyển khỏi báo cáo.' : 'Chỉ cập nhật tự động với kỳ kết thúc hôm nay.'}><input type="checkbox" aria-describedby="refresh-help" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />Tự cập nhật mỗi 2 phút</label><span id="refresh-help" className="sr-only">{currentPeriod ? 'Tạm dừng khi đang nhập, mở chi tiết hoặc chuyển khỏi báo cáo.' : 'Chỉ cập nhật tự động với kỳ kết thúc hôm nay.'}</span></div>}
    </div>
    {data && view.status === 'stale' && <div className="refresh-error" role="alert"><span>{view.error} Đang giữ số liệu lần đọc trước.</span><button type="button" className="button" onClick={() => requestReport(true)}>Thử cập nhật lại</button></div>}
    {data && view.status === 'refreshing' && <p className="refresh-progress" role="status">Đang cập nhật số liệu…</p>}
    {isReportView && exportMessage && <p className="export-message" role="status">{exportMessage}</p>}
    <div aria-live="polite" aria-busy={loading}>
      {loading && !data && <div className="loading-region" role="status"><div className="loading-label"><span className="spinner" /><div><strong>Đang tổng hợp báo cáo</strong><p>{reportTimeout > 45000 ? 'Kỳ báo cáo dài có thể cần khoảng một phút. Trang sẽ chờ tối đa 90 giây.' : 'Đang lấy dữ liệu cho kỳ và xí nghiệp đã chọn…'}</p></div></div><div className="skeleton-grid" aria-hidden="true"><div /><div /><div /></div><div className="skeleton-chart" aria-hidden="true" /></div>}
      {error && <div className="error-state" role="alert"><CircleAlert size={32} aria-hidden="true" /><h2>Chưa tải được báo cáo</h2><p>{error}</p><button className="button primary" type="button" onClick={() => requestReport(false)}><RefreshCw size={16} aria-hidden="true" />Thử lại</button></div>}
    </div>
    </>}
    {isReportView && data && <>
      {data.meta.status === 'empty' && <div className="notice empty-notice" role="status"><Info size={19} aria-hidden="true" /><div><strong>Không có phát sinh trong kỳ đã chọn</strong><p>Hệ thống đã truy vấn thành công. Hãy chọn kỳ khác để xem dữ liệu sản xuất.</p></div></div>}
      <section className="kpi-grid" aria-label="Chỉ tiêu tổng quan">
        <Kpi title="Sản lượng thông qua" value={data.overview.total_tonnage} unit="tấn" trend={data.overview.trend_tonnage} status={data.overview.tonnage_status} icon={Activity} accent onInspect={data.meta.report_id ? (event) => inspect({}, 'Sản lượng thông qua', event) : null} />
        <Kpi title="Container qua tác nghiệp" value={data.overview.total_teu} unit="TEU" trend={data.overview.trend_teu} status={data.overview.teu_status} icon={Boxes} onInspect={data.meta.report_id ? (event) => inspect({ cargo: 'Hàng container' }, 'Container qua tác nghiệp', event) : null} />
        <Kpi title="Chuyến tàu có phát sinh" value={data.overview.vessel_calls} unit="chuyến" trend={data.overview.trend_vessels} icon={Ship} digits={0} listLink="#voyages" />
      </section>
      <div className="comparison-note"><Info size={14} aria-hidden="true" /><span>So sánh với {formatDate(data.meta.previous_period?.start_date)} – {formatDate(data.meta.previous_period?.end_date)}. {data.meta.previous_period?.label}</span></div>
      <ThroughputProgress report={data} user={user} apiBase={API_BASE} preferredPeriodType={preferredPeriodType} preferredPeriodKey={preferredPeriodKey} onSelectPeriod={selectPlan} />
      <section id="production" aria-labelledby="production-title">
        <div className="section-heading"><h2 id="production-title">Phân tích sản lượng</h2><span className="section-meta">{formatNumber(data.overview.record_count)} bản ghi tác nghiệp</span></div>
        <div className="production-grid"><Suspense fallback={<div className="panel empty-panel" role="status">Đang tải biểu đồ…</div>}><History key={filterKey} monthlyRows={data.history} dailyRows={data.daily_history} hasRecords={data.overview.record_count > 0} onInspect={data.meta.report_id ? (day, event) => inspect({ day }, `Sản lượng ngày ${formatDate(day)}`, event) : null} /></Suspense><Terminals rows={data.terminals} total={data.overview.total_tonnage} hasSignedInput={hasSignedInput} onInspect={data.meta.report_id ? (row, event) => inspect({ terminal: row.terminal_id }, row.name, event) : null} /></div>
        <div className="breakdown-grid"><CargoBreakdown key={filterKey} rows={data.cargo} onInspect={data.meta.report_id ? (row, event) => inspect({ cargo: row.name }, row.name, event) : null} /><Breakdown title="Cơ cấu hướng hàng" subtitle="Tỷ trọng trên tổng tấn" rows={data.directions} total={data.overview.total_tonnage} hasSignedInput={hasSignedInput} icon={Ship} /></div>
      </section>
      <Voyages key={filterKey} rows={data.voyages} count={data.overview.vessel_calls} filters={filters} reportId={data.meta.report_id} apiBase={API_BASE} onRetry={() => requestReport(true)} />
      <NativeUnits rows={data.native_units} />
      <Customers rows={data.customers} total={data.overview.total_tonnage} hasSignedInput={hasSignedInput} onInspect={data.meta.report_id ? (row, event) => inspect({ customer_id: row.drilldown_customer_id || row.customer_id || 'unassigned', customer_terminal: row.terminal_id }, row.name, event) : null} />
    </>}
    {!isReportView && <Management key={activeView} mode={activeView} activeTab={managementTab} onTabChange={setManagementTab} user={user} filters={filters} report={view.status === 'stale' || loading ? null : data} apiBase={API_BASE} preferredPeriodType={preferredPeriodType} onSelectPlan={selectPlan} onReportChange={() => requestReport(true)} />}
    {isReportView && data && <><DataQuality meta={data.meta} /><footer className="dashboard-footer"><span>CẢNG NGHỆ TĨNH <span aria-hidden="true">/</span> Báo cáo sản lượng</span><span>{TERMINALS[filters.terminal]} · {formatDate(filters.end_date)}</span></footer></>}
    {isReportView && data && inspection && <OperationsExplorer key={data.meta.report_id} report={data} {...inspection} apiBase={API_BASE} onClose={() => setInspection(null)} onReloadReport={() => { setInspection(null); requestReport(true); }} />}
    </div>
  </div>;
}

export default Dashboard;
