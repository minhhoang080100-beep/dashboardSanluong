import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, RefreshCw, Search, Ship, X } from 'lucide-react';
import { formatDate, formatNumber, formatTimestamp } from '../dashboard-data';
import { fetchVoyageDetail, filterVoyages, paginateVoyages, voyageListError } from '../voyage-data';
import './Voyages.css';

const dateRange = (start, end) => start ? start === end || !end ? formatDate(start) : `${formatDate(start)} – ${formatDate(end)}` : '—';
const timestamp = (value) => value ? formatTimestamp(value) : '—';

function ProductionTable({ rows, label, heading, date = false }) {
  return <article className="voyage-breakdown"><h3>{heading}</h3><div className="table-scroll"><table><thead><tr><th scope="col">{label}</th><th scope="col">Tấn</th><th scope="col">TEU</th></tr></thead><tbody>{rows.map((row, index) => <tr key={index}><th scope="row">{date ? formatDate(row.date) : row.name}</th><td>{formatNumber(row.tonnage)}</td><td>{formatNumber(row.teu)}</td></tr>)}</tbody></table></div></article>;
}

function VoyageDialog({ selected, filters, apiBase, onClose, trigger }) {
  const dialog = useRef(null);
  const operationHeading = useRef(null);
  const pendingPageFocus = useRef(false);
  const [page, setPage] = useState(1);
  const [operationFilter, setOperationFilter] = useState('with_values');
  const [reload, setReload] = useState(0);
  const [resource, setResource] = useState({ key: '', status: 'loading', data: null, error: '' });
  const requestKey = `${selected.terminal_id}/${selected.voyage_id}/${filters.start_date}/${filters.end_date}/${operationFilter}/${page}`;
  const loading = resource.key !== requestKey || resource.status === 'loading';
  const data = !loading && resource.status === 'success' ? resource.data : null;
  const error = !loading && resource.status === 'error' ? resource.error : '';

  useEffect(() => {
    const element = dialog.current;
    const previousOverflow = document.body.style.overflow;
    element.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      element.close();
      document.body.style.overflow = previousOverflow;
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, [trigger]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let timedOut = false;
    setResource({ key: requestKey, status: 'loading', data: null, error: '' });
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 45000);
    fetchVoyageDetail(selected, filters, page, { signal: controller.signal, baseUrl: apiBase, operationFilter })
      .then((result) => { if (active) setResource({ key: requestKey, status: 'success', data: result, error: '' }); })
      .catch((failure) => {
        if (!active || (controller.signal.aborted && !timedOut)) return;
        const message = timedOut ? 'Truy vấn chi tiết mất quá 45 giây. Vui lòng thử lại.' : failure instanceof TypeError ? 'Không kết nối được máy chủ chi tiết chuyến tàu.' : failure instanceof SyntaxError ? 'Máy chủ trả về dữ liệu chi tiết không hợp lệ.' : failure.message;
        setResource({ key: requestKey, status: 'error', data: null, error: message });
      }).finally(() => clearTimeout(timeout));
    return () => { active = false; clearTimeout(timeout); controller.abort(); };
  }, [selected, filters, page, reload, requestKey, apiBase, operationFilter]);

  useEffect(() => {
    if (data && pendingPageFocus.current) {
      pendingPageFocus.current = false;
      operationHeading.current?.focus();
    }
  }, [data]);

  function changePage(delta) {
    pendingPageFocus.current = true;
    setPage((value) => value + delta);
  }

  function changeOperationFilter(event) {
    pendingPageFocus.current = true;
    setOperationFilter(event.target.value);
    setPage(1);
  }

  return <dialog ref={dialog} className="voyage-dialog" aria-label="Chi tiết chuyến tàu" onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header className="voyage-dialog-header"><div><span className="section-kicker">CHI TIẾT CHUYẾN TÀU</span><h2>{selected.vessel_name || 'Chưa có tên tàu'}</h2><p>{selected.voyage_code || selected.voyage_id} · {selected.terminal_name}</p></div><button type="button" className="button icon-button" aria-label="Đóng chi tiết chuyến tàu" onClick={onClose} autoFocus><X size={20} aria-hidden="true" /></button></header>
    <div className="voyage-dialog-body">
      <p className="voyage-detail-period">Kỳ tác nghiệp: {formatDate(filters.start_date)} – {formatDate(filters.end_date)}</p>
      <div aria-live="polite" aria-busy={loading}>
        {loading && <div className="voyage-detail-loading" role="status"><span className="spinner" />Đang tải chi tiết chuyến tàu…</div>}
        {error && <div className="voyage-detail-error" role="alert"><p>{error}</p><button type="button" className="button primary" onClick={() => setReload((value) => value + 1)}><RefreshCw size={15} aria-hidden="true" />Thử lại chi tiết</button></div>}
      </div>
      {data && <>
        <dl className="voyage-metadata"><div><dt>Tàu</dt><dd>{data.header.vessel_name || 'Chưa có tên tàu'}</dd></div><div><dt>Mã chuyến</dt><dd>{data.header.voyage_code || data.header.voyage_id}</dd></div><div><dt>Ngày làm hàng trong kỳ</dt><dd>{dateRange(data.header.first_operation_date, data.header.last_operation_date)}</dd></div><div><dt>Đến cảng thực tế</dt><dd>{timestamp(data.header.arrival_at)}</dd></div><div><dt>Rời cảng thực tế</dt><dd>{timestamp(data.header.departure_at)}</dd></div><div><dt>Xí nghiệp</dt><dd>{data.header.terminal_name}</dd></div></dl>
        <div className="voyage-summary"><div><span>Sản lượng qua cảng</span><strong>{formatNumber(data.summary.tonnage)} <small>tấn</small></strong></div><div><span>Container</span><strong>{formatNumber(data.summary.teu)} <small>TEU</small></strong></div><div><span>Dòng có phát sinh</span><strong>{formatNumber(data.operations.counts.with_values, 0)}</strong><small className="voyage-source-count">{formatNumber(data.operations.total_all, 0)} dòng nguồn</small></div></div>
        <div className="voyage-breakdowns"><ProductionTable rows={data.cargo} label="Nhóm hàng" heading="Cơ cấu hàng hóa" /><ProductionTable rows={data.daily} label="Ngày" heading="Sản lượng theo ngày" date /></div>
        {data.native_units.length > 0 && <article className="voyage-native"><h3>Đơn vị nguồn khác</h3><div className="table-scroll"><table><thead><tr><th scope="col">Đơn vị</th><th scope="col">Sản lượng theo đơn vị nguồn</th></tr></thead><tbody>{data.native_units.map((row, index) => <tr key={index}><th scope="row">{row.unit_name} ({row.unit_code})</th><td>{formatNumber(row.value)}</td></tr>)}</tbody></table></div></article>}
        <section className="voyage-operations" aria-labelledby="voyage-operations-title">
          <div className="voyage-section-heading"><h3 id="voyage-operations-title" ref={operationHeading} tabIndex={-1}>Tác nghiệp qua cảng trong kỳ</h3><span>{formatNumber(data.operations.total, 0)}/{formatNumber(data.operations.total_all, 0)} dòng</span></div>
          <label className="operation-filter"><span>Hiển thị tác nghiệp</span><select value={operationFilter} onChange={changeOperationFilter}>
            <option value="with_values">Có phát sinh ({formatNumber(data.operations.counts.with_values, 0)})</option>
            <option value="all">Tất cả ({formatNumber(data.operations.counts.all, 0)})</option>
            <option value="missing_weight">Thiếu trọng lượng ({formatNumber(data.operations.counts.missing_weight, 0)})</option>
          </select></label>
          {data.operations.rows.length > 0 ? <>
            <div className="table-scroll"><table className="operations-table"><caption className="sr-only">Danh sách dòng tác nghiệp của chuyến tàu theo bộ lọc đang chọn</caption><thead><tr><th scope="col">Mã tác nghiệp / ngày</th><th scope="col">Hàng hóa / phương án</th><th scope="col">Hướng hàng</th><th scope="col">Số lượng nguồn</th><th scope="col">Trọng lượng nguồn</th><th scope="col">Tấn</th><th scope="col">TEU</th></tr></thead><tbody>{data.operations.rows.map((row) => <tr key={row.id}><th scope="row">{row.operation_code || row.id}<small>{formatDate(row.operation_date)}</small>{row.shift_code && <small>Ca {row.shift_code}</small>}</th><td>{row.cargo_name || '—'}<small>{row.job_method || '—'}</small></td><td>{row.direction || '—'}</td><td>{formatNumber(row.quantity)}<small>{row.quantity_unit_name || row.quantity_unit || '—'}</small></td><td>{formatNumber(row.weight)}<small>{row.weight_unit_name || row.weight_unit || '—'}</small></td><td>{formatNumber(row.tonnage)}</td><td>{formatNumber(row.teu)}</td></tr>)}</tbody></table></div>
            <div className="voyage-pagination"><span>Trang {data.operations.page}/{data.operations.total_pages}</span><div><button className="button" type="button" aria-label="Trang phiếu trước" disabled={data.operations.page <= 1} onClick={() => changePage(-1)}><ArrowLeft size={15} aria-hidden="true" />Trước</button><button className="button" type="button" aria-label="Trang phiếu sau" disabled={data.operations.page >= data.operations.total_pages} onClick={() => changePage(1)}>Sau<ArrowRight size={15} aria-hidden="true" /></button></div></div>
          </> : <div className="operation-empty" role="status">{operationFilter === 'with_values' ? 'Không có dòng tác nghiệp có phát sinh trong kỳ đã chọn.' : operationFilter === 'missing_weight' ? 'Không có dòng tác nghiệp thiếu trọng lượng trong kỳ đã chọn.' : 'Không có dòng tác nghiệp trong kỳ đã chọn.'}</div>}
        </section>
        <p className="voyage-detail-generated">Tổng hợp: {formatTimestamp(data.meta.generated_at)} · Giờ Việt Nam</p>
      </>}
    </div>
  </dialog>;
}

export default function Voyages({ rows, count, filters, apiBase, onRetry }) {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(null);
  const trigger = useRef(null);
  const listError = voyageListError(rows, count, filters);
  const results = listError ? null : paginateVoyages(filterVoyages(rows, query), page);
  return <section className="panel voyages-panel" id="voyages" aria-labelledby="voyages-title">
    <div className="panel-heading"><div><h2 id="voyages-title">Chuyến tàu trong kỳ</h2><p>{formatNumber(count, 0)} chuyến · {formatDate(filters.start_date)} – {formatDate(filters.end_date)}</p></div><Ship size={22} className="heading-icon" aria-hidden="true" /></div>
    {listError ? <div className="voyage-detail-error" role="alert"><p>{listError}</p><button type="button" className="button primary" onClick={onRetry} disabled={!onRetry}><RefreshCw size={15} aria-hidden="true" />Tải lại danh sách chuyến</button></div> : <>
    <div className="voyage-list-tools"><label className="voyage-search"><Search size={16} aria-hidden="true" /><span className="sr-only">Tìm tàu hoặc mã chuyến</span><input type="search" value={query} placeholder="Tìm tàu hoặc mã chuyến…" onChange={(event) => { setQuery(event.target.value); setPage(1); }} /></label><span role="status">{formatNumber(results.total, 0)}/{formatNumber(count, 0)} chuyến</span></div>
    {results.items.length > 0 ? <>
      <div className="table-scroll voyage-table-scroll"><table className="voyage-table"><caption className="sr-only">Các chuyến tàu có tác nghiệp trong kỳ và xí nghiệp đang chọn</caption><thead><tr><th scope="col">Tàu / chuyến</th><th scope="col">Xí nghiệp</th><th scope="col">Ngày làm hàng</th><th scope="col">Tấn</th><th scope="col">TEU</th><th scope="col"><span className="sr-only">Chi tiết</span></th></tr></thead><tbody>{results.items.map((row) => <tr key={`${row.terminal_id}/${row.voyage_id}`}><th scope="row" className="voyage-name"><strong>{row.vessel_name || 'Chưa có tên tàu'}</strong><small>{row.voyage_code || row.voyage_id}</small></th><td data-label="Xí nghiệp">{row.terminal_name}</td><td data-label="Ngày làm hàng">{dateRange(row.first_operation_date, row.last_operation_date)}</td><td data-label="Tấn">{formatNumber(row.tonnage)}</td><td data-label="TEU">{formatNumber(row.teu)}</td><td className="voyage-open-cell"><button className="button voyage-open" type="button" aria-label={`Xem chi tiết ${row.vessel_name || 'Chưa có tên tàu'} · ${row.voyage_code || row.voyage_id} · ${row.terminal_name}`} onClick={(event) => { trigger.current = event.currentTarget; setSelected(row); }}>Xem chi tiết<ArrowRight size={14} aria-hidden="true" /></button></td></tr>)}</tbody></table></div>
      <div className="voyage-pagination"><span>Trang {results.page}/{results.totalPages}</span><div><button className="button" type="button" aria-label="Trang chuyến trước" disabled={results.page <= 1} onClick={() => setPage(results.page - 1)}><ArrowLeft size={15} aria-hidden="true" />Trước</button><button className="button" type="button" aria-label="Trang chuyến sau" disabled={results.page >= results.totalPages} onClick={() => setPage(results.page + 1)}>Sau<ArrowRight size={15} aria-hidden="true" /></button></div></div>
    </> : <div className="empty-panel"><Ship size={26} aria-hidden="true" /><p>{query ? 'Không tìm thấy tàu hoặc mã chuyến phù hợp.' : 'Không có chuyến tàu trong kỳ đã chọn.'}</p></div>}
    </>}
    {!listError && selected && <VoyageDialog selected={selected} filters={filters} apiBase={apiBase} trigger={trigger.current} onClose={() => setSelected(null)} />}
  </section>;
}
