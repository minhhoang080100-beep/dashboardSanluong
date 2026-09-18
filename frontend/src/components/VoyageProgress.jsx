import { useEffect, useState } from 'react';
import { apiRequest } from '../api-client';
import { formatDate, formatNumber, formatTimestamp } from '../dashboard-data';
import { validateVoyageProgress } from '../voyage-data';
import { productionScopeLabel } from '../production-scope';

export default function VoyageProgress({ selected, productionScope, apiBase }) {
  const [resource, setResource] = useState({ key: '', data: null, error: '' });
  const [revision, setRevision] = useState(0);
  const key = `${productionScope}/${selected.terminal_id}/${selected.voyage_id}`;
  const data = resource.key === key ? resource.data : null;
  const error = resource.key === key ? resource.error : '';
  useEffect(() => {
    const controller = new AbortController();
    setResource({ key, data: null, error: '' });
    apiRequest(`/voyages/${selected.terminal_id}/${selected.voyage_id}/progress?${new URLSearchParams({ production_scope: productionScope })}`, { baseUrl: apiBase, signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setResource({ key, data: validateVoyageProgress(result, selected, productionScope), error: '' }); })
      .catch((failure) => { if (!controller.signal.aborted) setResource({ key, data: null, error: failure.message }); });
    return () => controller.abort();
  }, [selected, productionScope, apiBase, revision, key]);
  return <section className="voyage-native"><h3>Tiến độ toàn chuyến</h3>
    {error ? <div role="alert" className="form-error"><p>{error}</p><button type="button" className="button" onClick={() => setRevision((value) => value + 1)}>Tải lại tiến độ</button></div> : !data ? <p role="status">Đang đọc tiến độ chuyến…</p> : <>
      {data.planning_eligible === false ? <p className="management-empty">{data.planning_reason || 'Chưa áp dụng kế hoạch cho phạm vi sản lượng này.'}</p> : <div className="table-scroll" tabIndex={0} role="region" aria-label="Tiến độ kế hoạch toàn chuyến"><table className="voyage-progress-table"><caption className="sr-only">Kế hoạch và sản lượng đã ghi nhận của toàn chuyến</caption><thead><tr><th scope="col">Chỉ tiêu</th><th scope="col">Kế hoạch</th><th scope="col">Đã ghi nhận</th><th scope="col">Còn lại</th><th scope="col">Hoàn thành</th></tr></thead><tbody>{data.planning.map((row) => <tr key={row.metric}><th scope="row">{row.metric === 'tonnage' ? 'Tấn' : 'TEU'}<small>{row.status === 'missing_plan' ? 'Chưa có kế hoạch được duyệt' : row.status === 'incomplete_actual' ? 'Số liệu nguồn chưa đầy đủ' : row.reference || ''}</small></th><td>{formatNumber(row.target)}</td><td>{formatNumber(row.actual)}</td><td>{formatNumber(row.remaining)}</td><td>{row.completion_percent == null ? '—' : `${formatNumber(row.completion_percent, 1)}%`}</td></tr>)}</tbody></table></div>}
      <p className="voyage-detail-generated">{productionScopeLabel(productionScope)} · Toàn bộ tác nghiệp qua cảng của chuyến đến thời điểm đọc nguồn: {formatTimestamp(data.meta.source_read_at)}.</p>
      <details className="chart-data"><summary>Sản lượng toàn chuyến theo ca</summary><div className="table-scroll" tabIndex={0} role="region" aria-label="Sản lượng toàn chuyến theo ca"><table className="voyage-shifts-table"><thead><tr><th scope="col">Ngày</th><th scope="col">Ca</th><th scope="col">Tấn</th><th scope="col">TEU</th></tr></thead><tbody>{data.shifts.map((row, index) => <tr key={index}><th scope="row">{formatDate(row.date || row.operation_date)}</th><td>{row.shift_code || '—'}</td><td>{formatNumber(row.tonnage)}</td><td>{formatNumber(row.teu)}</td></tr>)}</tbody></table></div></details>
    </>}
  </section>;
}
