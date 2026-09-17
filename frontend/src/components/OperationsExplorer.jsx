import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Download, X } from 'lucide-react';
import { apiRequest } from '../api-client';
import useDownload from '../use-download';
import { formatDate, formatNumber, formatTimestamp } from '../dashboard-data';
import { BERTH_RULE_VERSION, initialBerthLabel, productionScopeLabel } from '../production-scope';
import './OperationsExplorer.css';

export default function OperationsExplorer({ report, selection = {}, label, apiBase, onClose, onReloadReport, trigger }) {
  const modal = useRef(null);
  const [page, setPage] = useState(1);
  const [operationFilter, setOperationFilter] = useState('all');
  const [resource, setResource] = useState({ key: '', data: null, error: '' });
  const [exportError, setExportError] = useState('');
  const download = useDownload();
  const [exportExpired, setExportExpired] = useState(false);
  const [revision, setRevision] = useState(0);
  const productionScope = report.meta.filters.production_scope;
  const query = new URLSearchParams({ ...selection, production_scope: productionScope, operation_filter: operationFilter, page: String(page), page_size: '25' }).toString();
  const path = `/reports/${encodeURIComponent(report.meta.report_id)}/operations?${query}`;
  const key = path;
  const data = resource.key === key ? resource.data : null;
  const error = resource.key === key ? resource.error : '';
  useEffect(() => {
    const element = modal.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    element.showModal();
    return () => { element.close(); document.body.style.overflow = previousOverflow; if (trigger?.isConnected) trigger.focus?.(); };
  }, [trigger]);
  useEffect(() => {
    const controller = new AbortController();
    apiRequest(path, { baseUrl: apiBase, signal: controller.signal }).then((result) => {
      if (result.meta?.filters?.production_scope !== productionScope || result.meta.berth_rule_version !== BERTH_RULE_VERSION) throw new Error('Chi tiết chưa khớp phạm vi sản lượng. Vui lòng tải lại báo cáo.');
      if (!controller.signal.aborted) setResource({ key, data: result, error: '' });
    }).catch((failure) => {
      if (!controller.signal.aborted) setResource({ key, data: null, error: failure.message, status: failure.status });
    });
    return () => controller.abort();
  }, [path, key, productionScope, apiBase, revision]);
  async function exportRows() {
    setExportError('');
    setExportExpired(false);
    const outcome = await download.run(`/reports/${encodeURIComponent(report.meta.report_id)}/export.xlsx?${new URLSearchParams({ ...selection, production_scope: productionScope, operation_filter: operationFilter })}`, `chi-tiet-san-luong-${productionScope}-${report.meta.filters.start_date}-${report.meta.filters.end_date}.xlsx`, { baseUrl: apiBase });
    if (outcome.error) { setExportError(outcome.error.message); setExportExpired(outcome.error.status === 410); }
  }
  return <dialog ref={modal} className="voyage-dialog operations-explorer" aria-label="Chi tiết sản lượng" onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header className="voyage-dialog-header"><div><h2>{label || 'Chi tiết sản lượng'}</h2><p>{formatDate(report.meta.filters.start_date)} – {formatDate(report.meta.filters.end_date)} · {productionScopeLabel(productionScope)}</p></div><button className="button icon-button" type="button" onClick={onClose} aria-label="Đóng chi tiết sản lượng" autoFocus><X size={20} /></button></header>
    <div className="voyage-dialog-body">
      <div className="explorer-tools"><label>Hiển thị<select value={operationFilter} onChange={(event) => { setOperationFilter(event.target.value); setPage(1); }}><option value="all">Tất cả tác nghiệp</option><option value="with_values">Có phát sinh</option><option value="missing_weight">Thiếu trọng lượng</option></select></label><button type="button" className="button" disabled={!data || download.busy} aria-busy={download.busy} onClick={exportRows}><Download size={16} />{download.busy ? 'Đang xuất Excel…' : 'Xuất Excel'}</button></div>
      {exportError && <p role="alert" className="form-error">{exportError}{exportExpired && onReloadReport && <button type="button" className="button" onClick={onReloadReport}>Tải lại báo cáo</button>}</p>}
      {error ? <div role="alert" className="form-error"><p>{error}</p>{resource.status === 410 && onReloadReport ? <button type="button" className="button" onClick={onReloadReport}>Tải lại báo cáo</button> : <button type="button" className="button" onClick={() => { setResource({ key: '', data: null, error: '' }); setRevision((value) => value + 1); }}>Thử lại</button>}</div> : !data ? <p role="status">Đang đọc chi tiết…</p> : <>
        <div className="voyage-summary"><div><span>Sản lượng thông qua</span><strong>{formatNumber(data.summary.tonnage)} <small>tấn</small></strong></div><div><span>Container tác nghiệp</span><strong>{formatNumber(data.summary.teu)} <small>TEU</small></strong></div><div><span>Dòng tác nghiệp</span><strong>{formatNumber(data.operations.total, 0)}<small> / {formatNumber(data.operations.total_all, 0)}</small></strong></div></div>
        {data.operations.rows.length ? <div className="table-scroll"><table className="operations-table"><thead><tr><th>Mã / ngày / ca</th><th>Xí nghiệp / tàu</th><th>Hàng hóa</th><th>Số lượng</th><th>Trọng lượng</th><th>Tấn</th><th>TEU</th></tr></thead><tbody>{data.operations.rows.map((row) => <tr key={row.row_key || `${row.terminal_id}/${row.id}`}><th scope="row">{row.operation_code || row.id}<small>{formatDate(row.operation_date)}</small><small>{row.shift_code || '—'}</small></th><td>{row.terminal_name}<small>{row.vessel_name || 'Chưa gắn tàu hợp lệ'}</small><small>{row.voyage_code || row.source_voyage_id || '—'}</small><small>Cầu ban đầu: {initialBerthLabel(row)}</small></td><td>{row.cargo_name}<small>{row.customer_name || '—'}</small></td><td>{formatNumber(row.quantity)}<small>{row.quantity_unit_name || row.quantity_unit}</small></td><td>{formatNumber(row.weight)}<small>{row.weight_unit_name || row.weight_unit}</small></td><td>{formatNumber(row.tonnage)}</td><td>{formatNumber(row.teu)}</td></tr>)}</tbody></table></div> : <p className="operation-empty">Không có tác nghiệp phù hợp bộ lọc.</p>}
        <div className="voyage-pagination"><span>Trang {page}/{Math.max(1, data.operations.total_pages)}</span><div><button type="button" className="button" disabled={page <= 1} onClick={() => setPage(page - 1)}><ArrowLeft size={15} />Trước</button><button type="button" className="button" disabled={page >= data.operations.total_pages} onClick={() => setPage(page + 1)}>Sau<ArrowRight size={15} /></button></div></div>
        <p className="voyage-detail-generated">Đọc nguồn: {formatTimestamp(data.meta.source_read_at)}</p>
      </>}
    </div>
  </dialog>;
}
