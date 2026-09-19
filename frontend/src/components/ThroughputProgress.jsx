import { useEffect, useState } from 'react';
import { ArrowUpRight, Check, RefreshCw, Target } from 'lucide-react';
import { apiRequest } from '../api-client.js';
import { formatDate, formatNumber, TERMINALS, todayInVietnam } from '../dashboard-data.js';
import { canCreatePlan } from '../plan-permissions.js';
import { allowedTerminals } from '../filter-preferences.js';
import { PROGRESS_BANDS, completionView, planProvenance, progressPeriodLabel, progressPeriodOptions, selectProgressItem, validateThroughputProgress } from '../throughput-progress.js';
import './ThroughputProgress.css';

export default function ThroughputProgress({ report, user, apiBase, preferredPeriodType, preferredPeriodKey, onSelectPeriod, revision = 0 }) {
  const reportId = report?.meta?.report_id;
  const [resource, setResource] = useState({ key: '', data: null, error: '' });
  const [retry, setRetry] = useState(0);
  const [selection, setSelection] = useState({ reportId: '', key: '' });
  const [otherSelection, setOtherSelection] = useState({ reportId: '', key: '' });
  const key = `${reportId}/${revision}/${retry}`;
  const data = resource.key === key ? resource.data : null;
  const error = resource.key === key ? resource.error : '';
  useEffect(() => {
    if (!reportId) return;
    const controller = new AbortController();
    let active = true;
    apiRequest(`/reports/${encodeURIComponent(reportId)}/throughput-progress`, { baseUrl: apiBase, signal: controller.signal })
      .then((value) => { if (active) setResource({ key, data: validateThroughputProgress(value, report, allowedTerminals(user)), error: '' }); })
      .catch((failure) => { if (active && !controller.signal.aborted) setResource({ key, data: null, error: failure.message || 'Chưa tải được tiến độ kế hoạch.' }); });
    return () => { active = false; controller.abort(); };
  }, [reportId, report, apiBase, key, user]);
  const selectedKey = selection.reportId === reportId && selection.preferredKey === preferredPeriodKey ? selection.key : preferredPeriodKey;
  const item = data && selectProgressItem(data.items, selectedKey, preferredPeriodType);
  const options = progressPeriodOptions(data);
  const today = todayInVietnam();
  const canChoosePeriod = onSelectPeriod && options.length > 0;
  const otherPeriods = data?.other_scope_periods || [];
  const otherKey = (option) => `${option.terminal}/${option.key}`;
  const otherPlan = otherPeriods.find((option) => otherSelection.reportId === reportId && otherKey(option) === otherSelection.key)
    || otherPeriods.find((option) => option.start_date <= report.meta.filters.start_date && option.end_date >= report.meta.filters.start_date)
    || otherPeriods.find((option) => option.start_date <= today) || otherPeriods[0];
  const canOpenOther = onSelectPeriod && !item && otherPlan;
  const progress = completionView(item);
  const provenance = planProvenance(item);
  const differenceLabel = progress.percent !== null && progress.percent > 100
    ? (progress.provisional ? 'Vượt (tạm tính)' : 'Vượt kế hoạch')
    : (progress.provisional ? 'Còn lại (tạm tính)' : 'Còn lại');
  const manageLink = canCreatePlan(user) && report?.meta?.filters.production_scope === 'nghe_tinh';
  const percentage = progress.percent === null ? '—' : `${formatNumber(Math.floor((progress.percent + 1e-9) * 10) / 10, 1)}%`;
  return <section className="panel throughput-progress" aria-labelledby="throughput-progress-title">
    <div className="throughput-progress-heading"><div><h2 id="throughput-progress-title"><Target size={19} aria-hidden="true" />Tiến độ kế hoạch sản lượng</h2><p>Tấn thông qua thực tế / kế hoạch</p></div>
      <div className="throughput-progress-actions">
        {reportId && <button type="button" className="button" disabled={!data && !error} onClick={() => setRetry((value) => value + 1)}><RefreshCw size={15} aria-hidden="true" />Tải lại tiến độ</button>}
        {manageLink && <a className="button" href="#management">Nhập kế hoạch <ArrowUpRight size={15} aria-hidden="true" /></a>}
      </div>
    </div>
    {!reportId ? <p className="throughput-progress-empty">Tải lại báo cáo để xem tiến độ kế hoạch.</p>
      : error ? <div className="throughput-progress-error" role="alert"><span>{error}</span></div>
      : !data ? <p className="throughput-progress-empty" role="status">Đang đọc kế hoạch…</p>
      : <>
        {canChoosePeriod && <div className="throughput-progress-chooser"><label>Kế hoạch đối chiếu<select value={item?.key || ''} onChange={(event) => {
          const next = options.find((option) => option.key === event.target.value);
          if (!next) return;
          onSelectPeriod(next);
        }}>
          {!item && <option value="" disabled>Chọn kế hoạch đã duyệt</option>}
          {options.map((option) => <option key={option.key} value={option.key} disabled={option.start_date > today}>{progressPeriodLabel(option)} · {formatDate(option.start_date)} – {formatDate(option.end_date)} · {formatNumber(option.target)} tấn{option.start_date > today ? ' · Kỳ chưa bắt đầu' : ''}</option>)}
        </select></label><p>Chọn kỳ khác sẽ mở báo cáo từ đầu kỳ kế hoạch đến ngày hiện tại hoặc ngày kết thúc kỳ.</p></div>}
        {!item ? <>
          <p className="throughput-progress-empty">{canChoosePeriod ? 'Kỳ báo cáo đang xem chưa khớp các kế hoạch đã duyệt. Chọn kế hoạch ở trên để xem đúng tiến độ.' : data.reason || 'Chưa có kế hoạch được duyệt khớp kỳ và phạm vi báo cáo.'}</p>
          {canOpenOther && <div className="throughput-progress-recovery">
            <p>Đang xem <strong>{TERMINALS[report.meta.filters.terminal]}</strong>. Bạn có kế hoạch đã duyệt ở phạm vi khác.</p>
            <label htmlFor="other-scope-plan">Kế hoạch ở phạm vi khác</label>
            <div className="throughput-progress-recovery-controls"><select id="other-scope-plan" value={otherKey(otherPlan)} onChange={(event) => setOtherSelection({ reportId, key: event.target.value })}>
              {otherPeriods.map((option) => <option key={otherKey(option)} value={otherKey(option)} disabled={option.start_date > today}>{TERMINALS[option.terminal]} · {progressPeriodLabel(option)} · {formatNumber(option.target)} tấn{option.start_date > today ? ' · Kỳ chưa bắt đầu' : ''}</option>)}
            </select><button type="button" className="button primary" disabled={otherPlan.start_date > today} onClick={() => onSelectPeriod(otherPlan)}>Xem tiến độ {TERMINALS[otherPlan.terminal]}<ArrowUpRight size={16} aria-hidden="true" /></button></div>
            <small>{otherPlan.start_date > today ? `Kỳ kế hoạch bắt đầu từ ${formatDate(otherPlan.start_date)}; chưa có sản lượng để đối chiếu.` : `Mở báo cáo đúng phạm vi, từ ${formatDate(otherPlan.start_date)} đến ${formatDate(otherPlan.end_date < today ? otherPlan.end_date : today)}.`}</small>
          </div>}
        </> : <>
        <div className="throughput-progress-period">
          {!canChoosePeriod && data.items.length > 1 ? <label>Kế hoạch đối chiếu<select value={item.key} onChange={(event) => setSelection({ reportId, key: event.target.value, preferredKey: preferredPeriodKey })}>{data.items.map((option) => <option key={option.key} value={option.key}>{progressPeriodLabel(option)} · {formatDate(option.start_date)} – {formatDate(option.end_date)}</option>)}</select></label>
            : <strong>{progressPeriodLabel(item)} · {formatDate(item.start_date)} – {formatDate(item.end_date)}</strong>}
          <span>Thực tế đến {formatDate(report.meta.filters.end_date)}</span>
        </div>
        <div className="throughput-progress-summary"><div><strong className="throughput-progress-percent">{percentage}</strong><span>{progress.provisional ? 'Tạm tính theo số liệu đã ghi nhận' : progress.achieved ? <><Check size={15} aria-hidden="true" />Đạt kế hoạch</> : progress.percent !== null ? 'Hoàn thành kế hoạch' : 'Chưa tính được tỷ lệ'}</span></div>
          <dl><div><dt>Thực tế</dt><dd>{formatNumber(item.actual)} <span>tấn</span></dd></div><div><dt>Kế hoạch</dt><dd>{formatNumber(item.target)} <span>tấn</span></dd></div><div><dt>{differenceLabel}</dt><dd>{progress.percent === null ? '—' : formatNumber(progress.percent > 100 ? item.actual - item.target : Math.max(0, item.target - item.actual))} <span>tấn</span></dd></div></dl>
        </div>
        {progress.percent !== null ? <>
          <div className="throughput-progress-bar" role="progressbar" aria-label="Hoàn thành kế hoạch sản lượng thông qua" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, progress.percent)} aria-valuetext={`${progress.provisional ? 'Tạm tính ' : ''}${percentage} kế hoạch`}>
            {PROGRESS_BANDS.map((band, index) => <div className="throughput-progress-segment" key={band.name}><span style={{ backgroundColor: band.color, width: `${Math.max(0, Math.min(100, (progress.width - index * 20) * 5))}%` }} /></div>)}
          </div>
          <div className="throughput-progress-scale" aria-hidden="true"><span>0%</span><span>20%</span><span>40%</span><span>60%</span><span>80%</span><span>100%</span></div>
          <div className="throughput-progress-legend" aria-label="Năm mức hoàn thành kế hoạch">{PROGRESS_BANDS.map((band) => <span key={band.name} className={progress.band?.name === band.name ? 'current-band' : ''}><i style={{ backgroundColor: band.color }} aria-hidden="true" />{band.label}</span>)}</div>
        </> : <p className="throughput-progress-empty">{item.reason || (item.target === 0 ? 'Kế hoạch bằng 0 nên chưa tính tỷ lệ hoàn thành.' : item.actual < 0 ? 'Sản lượng đang âm; cần đối soát trước khi tính tiến độ.' : 'Chưa đủ kế hoạch hoặc số liệu để tính tiến độ.')}</p>}
        {item.pace && <div className="throughput-pace" aria-label="Tiến độ theo mốc kế hoạch">
          <h3>Tiến độ theo mốc kế hoạch</h3>
          {item.pace.status === 'ready' ? <><p>Đối chiếu từ đầu kỳ đến <strong>{formatDate(item.pace.milestone_date)}</strong></p>
            <dl><div><dt>Kế hoạch lũy kế tại mốc</dt><dd>{formatNumber(item.pace.target)} tấn</dd></div><div><dt>Thực tế đến mốc</dt><dd>{formatNumber(item.pace.actual)} tấn</dd></div></dl>
            <strong className={item.pace.difference < 0 ? 'pace-behind' : 'pace-met'}>{item.pace.difference < 0 ? `Thiếu ${formatNumber(-item.pace.difference)} tấn so với mốc` : item.pace.difference > 0 ? `Vượt mốc ${formatNumber(item.pace.difference)} tấn` : 'Đạt mốc kế hoạch'}</strong>
          </> : <p>{item.pace.reason}</p>}
        </div>}
        {provenance.references.length > 0 && <div className="throughput-progress-provenance"><span>Căn cứ: {provenance.references.join('; ')}</span>{provenance.isTest && <><span className="throughput-progress-test" title="Nhận diện theo mã tham chiếu bắt đầu bằng TEST.">Kế hoạch thử</span><p>Dữ liệu kế hoạch dùng để kiểm thử, không phải chỉ tiêu chính thức.</p></>}</div>}
        {progress.provisional && <p className="throughput-progress-provisional">Số liệu nguồn chưa đầy đủ; tỷ lệ này chưa dùng để xác nhận đạt kế hoạch.</p>}
        </>}
      </>}
  </section>;
}
