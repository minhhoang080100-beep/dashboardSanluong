import { useState } from 'react';
import { ChevronDown, Package } from 'lucide-react';
import { formatNumber, isNumber } from '../dashboard-data';
import './CargoBreakdown.css';

const PREVIEW_COUNT = 8;

function CargoRow({ row, rank, maximum }) {
  return <li className={`cargo-row${rank === 1 ? ' cargo-row-leading' : ''}${row.tonnage < 0 ? ' cargo-row-negative' : ''}`}>
    <span className="cargo-rank" aria-hidden="true">{rank ? String(rank).padStart(2, '0') : '−'}</span>
    <div className="cargo-row-content">
      <div className="cargo-row-label"><span className="cargo-name">{row.name}</span><strong className="cargo-value">{formatNumber(row.tonnage)}<span className="sr-only"> tấn</span></strong></div>
      {row.tonnage > 0 && <div className="cargo-bar" aria-hidden="true"><span style={{ width: `${row.tonnage / maximum * 100}%` }} /></div>}
    </div>
  </li>;
}

export default function CargoBreakdown({ rows }) {
  const [expanded, setExpanded] = useState(false);
  // Compare known volumes directly. Missing or signed values must not turn
  // this chart into an incomplete percentage distribution.
  const positive = rows.filter((row) => isNumber(row.tonnage) && row.tonnage > 0).sort((a, b) => b.tonnage - a.tonnage);
  const negative = rows.filter((row) => isNumber(row.tonnage) && row.tonnage < 0).sort((a, b) => a.tonnage - b.tonnage);
  const zero = rows.filter((row) => row.tonnage === 0);
  const unknown = rows.filter((row) => !isNumber(row.tonnage));
  const visible = expanded ? positive : positive.slice(0, PREVIEW_COUNT);
  const maximum = positive[0]?.tonnage || 0;
  const foldedLabel = [zero.length > 0 && `${zero.length} nhóm bằng 0`, unknown.length > 0 && `${unknown.length} nhóm chưa có số tấn`].filter(Boolean).join(' · ');

  return <article className="panel cargo-panel" aria-labelledby="cargo-title">
    <div className="panel-heading cargo-heading">
      <div><h3 id="cargo-title"><Package size={18} aria-hidden="true" />Cơ cấu nhóm hàng</h3><p>Sản lượng theo nhóm hàng · Xếp giảm dần</p></div>
      <span className="cargo-count">{rows.length} nhóm</span>
    </div>
    {!rows.length ? <div className="empty-panel"><Package size={27} aria-hidden="true" /><p>Không có phát sinh trong kỳ đã chọn.</p></div> : <>
      {(positive.length > 0 || negative.length > 0) && <div className="cargo-columns" aria-hidden="true"><span>Nhóm hàng</span><span>Sản lượng (tấn)</span></div>}
      {positive.length > 0 && <ol className="cargo-ranking" id="cargo-ranking" aria-label="Nhóm hàng theo sản lượng giảm dần">
        {visible.map((row, index) => <CargoRow key={`${row.name}-${index}`} row={row} rank={index + 1} maximum={maximum} />)}
      </ol>}
      {positive.length > PREVIEW_COUNT && <button type="button" className="cargo-expand" aria-expanded={expanded} aria-controls="cargo-ranking" onClick={() => setExpanded((value) => !value)}>
        {expanded ? 'Thu gọn danh sách' : `Xem thêm ${positive.length - PREVIEW_COUNT} nhóm hàng`}<ChevronDown size={15} aria-hidden="true" />
      </button>}
      {negative.length > 0 && <div className="cargo-adjustments"><h4>Điều chỉnh giảm (tấn)</h4><ul className="cargo-ranking" aria-label="Nhóm hàng có sản lượng âm">{negative.map((row, index) => <CargoRow key={`${row.name}-${index}`} row={row} maximum={maximum} />)}</ul></div>}
      {(zero.length > 0 || unknown.length > 0) && <details className="cargo-other">
        <summary><span>{foldedLabel}</span><ChevronDown size={15} aria-hidden="true" /></summary>
        <ul className="cargo-other-list">{[...zero, ...unknown].map((row, index) => <li key={`${row.name}-${index}`}><span>{row.name}</span><span className="cargo-other-value">{isNumber(row.tonnage) ? '0 tấn' : 'Chưa có số tấn'}</span></li>)}</ul>
      </details>}
    </>}
  </article>;
}
