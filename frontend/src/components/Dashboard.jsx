import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { Activity, ArrowDownRight, ArrowUpRight, ArrowUpRight as OpenArrow, BarChart3, Boxes, CalendarDays, Check, CircleAlert, Clock3, Database, Download, Info, MapPin, Package, RefreshCw, Ship, SlidersHorizontal, Users, Warehouse } from 'lucide-react';
import { dashboardCsv, dashboardResourceView, fetchDashboard, formatDate, formatNumber, formatTimestamp, isNumber, presetDates, ratioAvailability, TERMINALS, todayInVietnam, validateFilters } from '../dashboard-data';
import Voyages from './Voyages';
import CargoBreakdown from './CargoBreakdown';
import './Dashboard.css';

const COLORS = ['#176c5b', '#397b9b', '#b48636', '#7285a6', '#7a9b71', '#8c6e94'];
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

function Kpi({ title, value, unit, trend, status, icon: Icon, accent, digits = 3, listLink }) {
  return (
    <article className={`kpi-card ${accent ? 'kpi-primary' : ''}`}>
      <div className="kpi-top"><span>{title}</span><span className="kpi-icon"><Icon size={19} aria-hidden="true" /></span></div>
      <p className="kpi-value">{formatNumber(value, digits)}<span>{unit}</span></p>
      <Trend value={['partial', 'unavailable'].includes(status) ? null : trend} />
      {listLink && <a className="kpi-link" href={listLink}>Xem danh sách <OpenArrow size={13} aria-hidden="true" /></a>}
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

function Terminals({ rows, total, hasSignedInput }) {
  const ratios = ratioAvailability(rows.map((row) => row.tonnage), total, hasSignedInput);
  return <article className="panel terminal-panel">
    <div className="panel-heading"><div><h3><MapPin size={17} aria-hidden="true" />Sản lượng theo xí nghiệp</h3><p>Tỷ trọng theo sản lượng qua cảng</p></div></div>
    {!rows.length ? <EmptyPanel /> : <div className="terminal-list">{rows.map((row, index) => {
      const share = ratios.available ? row.tonnage / total * 100 : null;
      return <div className="terminal-item" key={row.name}>
        <div className="terminal-name"><span className="terminal-number">0{index + 1}</span><h4>{row.name}</h4>{ratios.available && <strong>{formatNumber(share, 1)}%</strong>}</div>
        <div className="terminal-value">{formatNumber(row.tonnage)} <span>tấn</span></div>
        {ratios.available && <div className="bar-track" aria-hidden="true"><span style={{ width: `${share}%`, backgroundColor: COLORS[index % COLORS.length] }} /></div>}
        <p className="terminal-teu"><Boxes size={13} aria-hidden="true" />{formatNumber(row.teu)} TEU</p>
      </div>;
    })}</div>}
  </article>;
}

function Customers({ rows, total, hasSignedInput }) {
  const ratios = ratioAvailability(rows.map((row) => row.volume), total, hasSignedInput);
  return <section className="panel customer-panel" id="customers" aria-labelledby="customers-title">
    <div className="panel-heading"><div><h2 id="customers-title">Sản lượng theo khách hàng</h2><p>Xếp hạng theo tấn trong kỳ báo cáo</p></div><Users size={22} className="heading-icon" aria-hidden="true" /></div>
    {!rows.length ? <EmptyPanel /> : <div className="table-scroll"><table className="customer-table">
      <caption className="sr-only">Khách hàng có sản lượng qua cảng lớn nhất trong phạm vi đã chọn</caption>
      <thead><tr><th scope="col">Hạng</th><th scope="col">Khách hàng</th><th scope="col">Sản lượng <span>(tấn)</span></th><th scope="col">Tỷ trọng toàn kỳ</th></tr></thead>
      <tbody>{rows.map((row, index) => {
        const share = ratios.available ? row.volume / total * 100 : null;
        return <tr key={`${row.name}-${index}`}><td><span className={`rank ${index === 0 ? 'rank-first' : ''}`}>{String(index + 1).padStart(2, '0')}</span></td><th scope="row">{row.name}{row.terminal_name && <small className="customer-terminal">{row.terminal_name}</small>}</th><td>{formatNumber(row.volume)}</td><td>{ratios.available ? <div className="customer-share"><span>{formatNumber(share, 1)}%</span><div className="bar-track" aria-hidden="true"><span style={{ width: `${share}%` }} /></div></div> : '—'}</td></tr>;
      })}</tbody>
    </table></div>}
  </section>;
}

const definitionNames = { tonnage: 'Sản lượng qua cảng', measured_tonnage: 'Khối lượng ghi nhận', native_units: 'Các đơn vị nguồn khác', teu: 'Container (TEU)', throughput: 'Phạm vi sản lượng', vessel_calls: 'Chuyến tàu có phát sinh', cargo: 'Nhóm hàng', customers: 'Khách hàng', history: 'Chuỗi thời gian theo tháng', daily_history: 'Chuỗi thời gian theo ngày', sources: 'Thời điểm dữ liệu nguồn', comparison: 'Kỳ so sánh', consistency: 'Tính nhất quán số liệu' };
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

function Dashboard() {
  const [filters, setFilters] = useState(() => ({ ...presetDates('month'), terminal: 'all' }));
  const [draft, setDraft] = useState(filters);
  const [reload, setReload] = useState(0);
  const [formError, setFormError] = useState('');
  const [exportMessage, setExportMessage] = useState('');
  const [resource, setResource] = useState({ status: 'loading', data: null, error: '', key: '' });
  const filterKey = JSON.stringify(filters);
  const view = useMemo(() => dashboardResourceView(resource, filters), [resource, filters]);
  const loading = view.status === 'loading' || view.status === 'recovering';
  const data = view.data;
  const error = view.status === 'error' ? view.error : '';
  const hasDraft = JSON.stringify(draft) !== filterKey;
  const hasSignedInput = (data?.meta.data_quality?.negative_value_count || 0) > 0;

  useEffect(() => {
    // Recover retained state once. A malformed fresh response becomes an explicit
    // fetch error below, so it cannot trigger an automatic retry loop.
    if (view.status === 'recovering') setReload((value) => value + 1);
  }, [view.status]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let timedOut = false;
    setResource({ status: 'loading', data: null, error: '', key: filterKey });
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 45000);
    fetchDashboard(filters, { signal: controller.signal, baseUrl: API_BASE })
      .then((result) => { if (active) setResource({ status: 'success', data: result, error: '', key: filterKey }); })
      .catch((failure) => {
        if (!active) return;
        if (controller.signal.aborted && !timedOut) return;
        const message = timedOut ? 'Truy vấn mất quá 45 giây. Vui lòng thu hẹp kỳ báo cáo hoặc thử lại.' : failure instanceof SyntaxError ? 'Máy chủ trả về dữ liệu không hợp lệ. Vui lòng thử lại hoặc liên hệ bộ phận CNTT.' : failure instanceof TypeError ? 'Không kết nối được máy chủ báo cáo. Vui lòng kiểm tra kết nối và thử lại.' : failure.message;
        setResource({ status: 'error', data: null, error: message, key: filterKey });
      })
      .finally(() => clearTimeout(timer));
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [filters, filterKey, reload]);

  function applyFilters(event) {
    event.preventDefault();
    const validation = validateFilters(draft);
    setFormError(validation);
    if (validation) return;
    setExportMessage('');
    setFilters({ ...draft });
  }

  function applyPreset(preset) {
    const next = { ...draft, ...presetDates(preset) };
    setDraft(next);
    setFilters(next);
    setFormError('');
    setExportMessage('');
  }

  function exportCsv() {
    if (!data) return;
    const blob = new Blob([dashboardCsv(data)], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `san-luong-${filters.terminal}-${filters.start_date}-${filters.end_date}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setExportMessage('Đã tạo tệp CSV theo kỳ báo cáo đang hiển thị.');
  }

  return <div className="dashboard">
    <section className="dashboard-intro" id="overview" aria-labelledby="dashboard-title">
      <h1 id="dashboard-title">Báo cáo sản lượng</h1>
      <button className="button secondary export-button" type="button" onClick={exportCsv} disabled={!data || hasDraft}><Download size={16} aria-hidden="true" />Xuất báo cáo CSV</button>
    </section>
    <section className="filter-panel" aria-label="Bộ lọc báo cáo">
      <div className="filter-top"><span><SlidersHorizontal size={16} aria-hidden="true" />Kỳ báo cáo</span><div className="preset-buttons" role="group" aria-label="Chọn nhanh kỳ báo cáo">
        {[['month', 'Tháng này'], ['previous', 'Tháng trước'], ['year', 'Từ đầu năm']].map(([key, label]) => {
          const dates = presetDates(key);
          return <button type="button" key={key} aria-pressed={filters.start_date === dates.start_date && filters.end_date === dates.end_date} onClick={() => applyPreset(key)}>{label}</button>;
        })}
      </div></div>
      <form className="filter-form" onSubmit={applyFilters}>
        <label htmlFor="start-date">Từ ngày<input id="start-date" type="date" required max={todayInVietnam()} value={draft.start_date} onChange={(event) => { setDraft({ ...draft, start_date: event.target.value }); setFormError(''); }} /></label>
        <label htmlFor="end-date">Đến ngày<input id="end-date" type="date" required max={todayInVietnam()} value={draft.end_date} onChange={(event) => { setDraft({ ...draft, end_date: event.target.value }); setFormError(''); }} /></label>
        <label htmlFor="terminal">Phạm vi xí nghiệp<select id="terminal" value={draft.terminal} onChange={(event) => setDraft({ ...draft, terminal: event.target.value })}>{Object.entries(TERMINALS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <button className="button primary" type="submit"><Check size={16} aria-hidden="true" />Áp dụng</button>
        <button className="button icon-button" type="button" aria-label="Tải lại báo cáo đang chọn" title="Tải lại báo cáo đang chọn" disabled={loading} onClick={() => { setReload((value) => value + 1); setExportMessage(''); }}><RefreshCw size={17} aria-hidden="true" /></button>
      </form>
      {formError && <p className="form-error" role="alert">{formError}</p>}
      {hasDraft && !formError && <p className="draft-note">Bộ lọc đã thay đổi. Chọn “Áp dụng” để cập nhật báo cáo.</p>}
    </section>
    <div className="report-context"><span><CalendarDays size={15} aria-hidden="true" /><strong>{formatDate(filters.start_date)} – {formatDate(filters.end_date)}</strong><span className="context-divider" aria-hidden="true">·</span>{TERMINALS[filters.terminal]}</span><span>{data ? `Tổng hợp: ${formatTimestamp(data.meta.generated_at)}` : 'Giờ Việt Nam · UTC+7'}</span></div>
    {exportMessage && <p className="export-message" role="status">{exportMessage}</p>}
    <div aria-live="polite" aria-busy={loading}>
      {loading && <div className="loading-region" role="status"><div className="loading-label"><span className="spinner" /><div><strong>Đang tổng hợp báo cáo</strong><p>Đang lấy dữ liệu cho kỳ và xí nghiệp đã chọn…</p></div></div><div className="skeleton-grid" aria-hidden="true"><div /><div /><div /></div><div className="skeleton-chart" aria-hidden="true" /></div>}
      {error && <div className="error-state" role="alert"><CircleAlert size={32} aria-hidden="true" /><h2>Chưa tải được báo cáo</h2><p>{error}</p><button className="button primary" type="button" onClick={() => setReload((value) => value + 1)}><RefreshCw size={16} aria-hidden="true" />Thử lại</button></div>}
    </div>
    {data && <>
      {data.meta.status === 'empty' && <div className="notice empty-notice" role="status"><Info size={19} aria-hidden="true" /><div><strong>Không có phát sinh trong kỳ đã chọn</strong><p>Hệ thống đã truy vấn thành công. Hãy chọn kỳ khác để xem dữ liệu sản xuất.</p></div></div>}
      <section className="kpi-grid" aria-label="Chỉ tiêu tổng quan">
        <Kpi title="Sản lượng qua cảng" value={data.overview.total_tonnage} unit="tấn" trend={data.overview.trend_tonnage} status={data.overview.tonnage_status} icon={Activity} accent />
        <Kpi title="Container qua tác nghiệp" value={data.overview.total_teu} unit="TEU" trend={data.overview.trend_teu} status={data.overview.teu_status} icon={Boxes} />
        <Kpi title="Chuyến tàu có phát sinh" value={data.overview.vessel_calls} unit="chuyến" trend={data.overview.trend_vessels} icon={Ship} digits={0} listLink="#voyages" />
      </section>
      <div className="comparison-note"><Info size={14} aria-hidden="true" /><span>So sánh với {formatDate(data.meta.previous_period?.start_date)} – {formatDate(data.meta.previous_period?.end_date)}. {data.meta.previous_period?.label}</span></div>
      <section id="production" aria-labelledby="production-title">
        <div className="section-heading"><h2 id="production-title">Phân tích sản lượng</h2><span className="section-meta">{formatNumber(data.overview.record_count)} bản ghi tác nghiệp</span></div>
        <div className="production-grid"><Suspense fallback={<div className="panel empty-panel" role="status">Đang tải biểu đồ…</div>}><History key={filterKey} monthlyRows={data.history} dailyRows={data.daily_history} hasRecords={data.overview.record_count > 0} /></Suspense><Terminals rows={data.terminals} total={data.overview.total_tonnage} hasSignedInput={hasSignedInput} /></div>
        <div className="breakdown-grid"><CargoBreakdown key={filterKey} rows={data.cargo} /><Breakdown title="Cơ cấu hướng hàng" subtitle="Tỷ trọng trên tổng tấn" rows={data.directions} total={data.overview.total_tonnage} hasSignedInput={hasSignedInput} icon={Ship} /></div>
      </section>
      <Voyages rows={data.voyages} count={data.overview.vessel_calls} filters={filters} apiBase={API_BASE} onRetry={() => setReload((value) => value + 1)} />
      <NativeUnits rows={data.native_units} />
      <Customers rows={data.customers} total={data.overview.total_tonnage} hasSignedInput={hasSignedInput} />
      <DataQuality meta={data.meta} />
      <footer className="dashboard-footer"><span>CẢNG NGHỆ TĨNH <span aria-hidden="true">/</span> Báo cáo sản lượng</span><span>{TERMINALS[filters.terminal]} · {formatDate(filters.end_date)}</span></footer>
    </>}
  </div>;
}

export default Dashboard;
