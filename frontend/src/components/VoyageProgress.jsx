import { useEffect, useState } from 'react';
import { apiRequest } from '../api-client';
import { formatDate, formatNumber, formatTimestamp } from '../dashboard-data';

export default function VoyageProgress({ selected, apiBase }) {
  const [resource, setResource] = useState({ data: null, error: '' });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setResource({ data: null, error: '' });
    apiRequest(`/voyages/${selected.terminal_id}/${selected.voyage_id}/progress`, { baseUrl: apiBase, signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setResource({ data, error: '' }); })
      .catch((failure) => { if (!controller.signal.aborted) setResource({ data: null, error: failure.message }); });
    return () => controller.abort();
  }, [selected, apiBase, revision]);
  return <section className="voyage-native"><h3>Tiến độ toàn chuyến</h3>
    {resource.error ? <div role="alert" className="form-error"><p>{resource.error}</p><button type="button" className="button" onClick={() => setRevision((value) => value + 1)}>Tải lại tiến độ</button></div> : !resource.data ? <p role="status">Đang đọc tiến độ chuyến…</p> : <>
      <div className="table-scroll"><table><thead><tr><th>Chỉ tiêu</th><th>Kế hoạch</th><th>Đã ghi nhận</th><th>Còn lại</th><th>Hoàn thành</th></tr></thead><tbody>{resource.data.planning.map((row) => <tr key={row.metric}><th scope="row">{row.metric === 'tonnage' ? 'Tấn' : 'TEU'}<small>{row.status === 'missing_plan' ? 'Chưa có kế hoạch được duyệt' : row.status === 'incomplete_actual' ? 'Số liệu nguồn chưa đầy đủ' : row.reference || ''}</small></th><td>{formatNumber(row.target)}</td><td>{formatNumber(row.actual)}</td><td>{formatNumber(row.remaining)}</td><td>{row.completion_percent == null ? '—' : `${formatNumber(row.completion_percent, 1)}%`}</td></tr>)}</tbody></table></div>
      <p className="voyage-detail-generated">Toàn bộ tác nghiệp qua cảng của chuyến đến thời điểm đọc nguồn: {formatTimestamp(resource.data.meta.source_read_at)}.</p>
      <details className="chart-data"><summary>Sản lượng toàn chuyến theo ca</summary><div className="table-scroll"><table><thead><tr><th>Ngày</th><th>Ca</th><th>Tấn</th><th>TEU</th></tr></thead><tbody>{resource.data.shifts.map((row, index) => <tr key={index}><th scope="row">{formatDate(row.date || row.operation_date)}</th><td>{row.shift_code || '—'}</td><td>{formatNumber(row.tonnage)}</td><td>{formatNumber(row.teu)}</td></tr>)}</tbody></table></div></details>
    </>}
  </section>;
}
