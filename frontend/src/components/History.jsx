import { useState } from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { formatNumber, isNumber } from '../dashboard-data';

const metricNames = { tonnage: 'Tấn', teu: 'TEU' };

export default function History({ monthlyRows, dailyRows, hasRecords }) {
  const [metric, setMetric] = useState('tonnage');
  const [granularity, setGranularity] = useState(dailyRows?.length && dailyRows.length <= 62 ? 'day' : 'month');
  const byDay = granularity === 'day' && Array.isArray(dailyRows);
  const rows = byDay ? dailyRows : monthlyRows;
  const timeLabel = byDay ? 'ngày' : 'tháng';
  const hasMetricValues = rows.some((row) => isNumber(row[metric]));
  const dateLabel = (date, compact = false) => {
    const parts = date.split('-').reverse();
    return compact && byDay ? parts.slice(0, 2).join('/') : parts.join('/');
  };
  return (
    <article className="panel history-panel">
      <div className="panel-heading">
        <div><h3>Sản lượng theo {timeLabel}</h3><p>Chỉ tính các ngày nằm trong kỳ báo cáo</p></div>
        <div className="segmented" role="group" aria-label="Đơn vị biểu đồ">
          {Object.entries(metricNames).map(([key, label]) => <button key={key} type="button" aria-pressed={metric === key} onClick={() => setMetric(key)}>{label}</button>)}
        </div>
      </div>
      {Array.isArray(dailyRows) && <div className="history-controls"><div className="segmented" role="group" aria-label="Độ chi tiết thời gian">
        <button type="button" aria-pressed={byDay} onClick={() => setGranularity('day')}>Theo ngày</button>
        <button type="button" aria-pressed={!byDay} onClick={() => setGranularity('month')}>Theo tháng</button>
      </div><span>{byDay ? 'Ngày không có bản ghi theo bộ lọc hiển thị bằng 0' : 'Tháng đầu / cuối có thể chưa đủ tháng'}</span></div>}
      {!hasRecords || !rows.length ? <div className="empty-panel"><p>Không có phát sinh trong kỳ đã chọn.</p></div> : <>
        <div className="history-unit">{metricNames[metric]}</div>
        {!hasMetricValues ? <div className="empty-panel"><p>Chưa đủ dữ liệu để tính {metricNames[metric]} trong kỳ đã chọn.</p></div> : <div className="history-chart" role="img" aria-label={`Biểu đồ sản lượng theo ${timeLabel}, đơn vị ${metricNames[metric]}. Số liệu chi tiết trong bảng bên dưới.`}>
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <BarChart data={rows} margin={{ top: 10, right: 10, bottom: 0, left: 0 }} accessibilityLayer>
              <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="#e3e9ed" />
              <XAxis dataKey="date" tickFormatter={(date) => dateLabel(date, true)} axisLine={false} tickLine={false} minTickGap={18} tick={{ fill: '#5b6f7c', fontSize: 11 }} dy={9} />
              <YAxis axisLine={false} tickLine={false} width={64} tick={{ fill: '#5b6f7c', fontSize: 11 }} tickFormatter={(value) => formatNumber(value)} />
              <Tooltip cursor={{ fill: '#edf4f2' }} formatter={(value) => [formatNumber(value), metricNames[metric]]} labelFormatter={(value) => `${byDay ? 'Ngày' : 'Tháng'} ${dateLabel(value)}`} contentStyle={{ background: '#fff', borderColor: '#dce5e9', borderRadius: 8, fontSize: 12 }} />
              <Bar dataKey={metric} fill={metric === 'tonnage' ? '#267865' : '#397b9b'} radius={[4, 4, 0, 0]} maxBarSize={60} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>}
        <details className="chart-data"><summary>Xem bảng số liệu theo {timeLabel}</summary><div className="table-scroll"><table><caption className="sr-only">Sản lượng theo {timeLabel} trong kỳ báo cáo</caption><thead><tr><th scope="col">{byDay ? 'Ngày' : 'Tháng'}</th><th scope="col">Tấn</th><th scope="col">TEU</th></tr></thead><tbody>{rows.map((row) => <tr key={row.date}><th scope="row">{dateLabel(row.date)}</th><td>{formatNumber(row.tonnage)}</td><td>{formatNumber(row.teu)}</td></tr>)}</tbody></table></div></details>
      </>}
    </article>
  );
}
