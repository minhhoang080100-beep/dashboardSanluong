import { Fragment, useEffect, useRef, useState } from 'react';
import { Check, Download, Eye, EyeOff, KeyRound, Plus, RefreshCw, Upload } from 'lucide-react';
import { apiRequest, downloadFile } from '../api-client.js';
import { formatDate, formatNumber, formatTimestamp } from '../dashboard-data.js';
import { buildPlanPayload, buildUserPayload, canManage, ISSUE_LABELS, ISSUE_STATUS_LABELS, MANAGEMENT_ROLES, MANAGEMENT_TERMINALS, METRIC_LABELS, PLAN_STATUS_LABELS, planPeriodEligibility, queryPath, userTerminals, validatedItems, validatedPlanPreview } from '../management-data.js';
import './Management.css';

function useResource(path, apiBase, revision = 0) {
  const key = `${apiBase}/${path}/${revision}`;
  const [resource, setResource] = useState({ key: '', data: null, error: '', loading: false });
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let active = true;
    let timedOut = false;
    setResource({ key, data: null, error: '', loading: true });
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 45000);
    apiRequest(path, { signal: controller.signal, baseUrl: apiBase })
      .then((data) => { if (active) setResource({ key, data, error: '', loading: false }); })
      .catch((failure) => {
        if (active && (!controller.signal.aborted || timedOut)) setResource({ key, data: null, error: timedOut ? 'Truy vấn quá 45 giây. Vui lòng tải lại.' : failure.message || 'Không tải được dữ liệu.', loading: false });
      }).finally(() => clearTimeout(timer));
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [path, apiBase, revision, key]);
  return !path ? { data: null, loading: false, error: '' } : resource.key === key ? resource : { data: null, loading: true, error: '' };
}

function useTask() {
  const [state, setState] = useState({ busy: false, error: '', message: '' });
  const active = useRef(true);
  const controller = useRef(null);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; controller.current?.abort(); };
  }, []);
  async function run(action, message = '') {
    if (controller.current) return { ok: false };
    const current = new AbortController();
    controller.current = current;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; current.abort(); }, 45000);
    setState({ busy: true, error: '', message: '' });
    try {
      const result = await action(current.signal);
      if (!active.current) return { ok: false };
      setState({ busy: false, error: '', message });
      return { ok: true, result };
    } catch (failure) {
      if (active.current) setState({ busy: false, error: timedOut ? 'Thao tác quá 45 giây. Tải lại danh sách để kiểm tra kết quả trước khi thực hiện lại.' : failure.message || 'Chưa xác nhận được thao tác. Hãy tải lại danh sách để kiểm tra.', message: '' });
      return { ok: false };
    } finally {
      controller.current = null;
      clearTimeout(timer);
    }
  }
  return { ...state, run };
}

function ResourceState({ resource, onRetry }) {
  if (resource.loading) return <div className="management-empty" role="status">Đang tải dữ liệu…</div>;
  if (resource.error) return <div className="management-error" role="alert"><p>{resource.error}</p>{onRetry && <button className="button" type="button" onClick={onRetry}><RefreshCw size={15} />Tải lại</button>}</div>;
  return null;
}

function TaskState({ task }) {
  return <>{task.error && <p className="management-error" role="alert">{task.error}</p>}{task.message && <p className="management-success" role="status">{task.message}</p>}</>;
}

function itemsOrError(resource) {
  if (!resource.data) return { items: [], resource };
  try { return { items: validatedItems(resource.data), resource }; }
  catch (error) { return { items: [], resource: { ...resource, data: null, error: error.message } }; }
}

function Pager({ page, pageSize = 25, total, onChange, disabled = false }) {
  const pages = Math.ceil(total / pageSize);
  if (pages <= 1) return null;
  return <div className="management-pagination"><span>Trang {page}/{pages} · {formatNumber(total, 0)} mục</span><div><button className="button" type="button" disabled={disabled || page <= 1} onClick={() => onChange(page - 1)}>Trước</button><button className="button" type="button" disabled={disabled || page >= pages} onClick={() => onChange(page + 1)}>Sau</button></div></div>;
}

function PlanProgress({ report, filters, apiBase, revision }) {
  const eligibility = planPeriodEligibility(filters);
  const reportId = report?.meta?.report_id;
  const resource = useResource(reportId && eligibility.eligible ? `/reports/${encodeURIComponent(reportId)}/plan-progress` : null, apiBase, revision);
  return <section className="management-card"><h3>Thực hiện so với kế hoạch tháng</h3>
    <p className="management-caption">Thực hiện từ {formatDate(filters.start_date)} đến {formatDate(filters.end_date)} · Kế hoạch cả tháng</p>
    {!reportId ? <p className="management-empty">Tải báo cáo sản lượng để đối chiếu kế hoạch.</p> : !eligibility.eligible ? <p className="management-empty">Chọn từ đầu tháng đến ngày cần xem trong cùng tháng để đối chiếu kế hoạch tháng.</p> : <>
      <ResourceState resource={resource} />
      {resource.data && (resource.data.eligible && Array.isArray(resource.data.rows) ? <div className="table-scroll"><table><thead><tr><th>Xí nghiệp</th><th>Chỉ tiêu</th><th>Kế hoạch</th><th>Thực hiện</th><th>Hoàn thành</th><th>Còn lại</th><th>Đối chiếu</th></tr></thead><tbody>{resource.data.rows.map((row) => <tr key={`${row.terminal}/${row.metric}`}><th scope="row">{row.terminal_name || MANAGEMENT_TERMINALS[row.terminal]}</th><td>{METRIC_LABELS[row.metric] || row.metric}{row.plan_version && <small>Phiên bản {row.plan_version}</small>}</td><td>{formatNumber(row.target)}</td><td>{formatNumber(row.actual)}</td><td>{row.completion_percent == null ? '—' : `${formatNumber(row.completion_percent, 1)}%`}</td><td>{formatNumber(row.remaining)}</td><td>{{ ready: 'Đủ dữ liệu', missing_plan: 'Chưa có kế hoạch duyệt', incomplete_actual: 'Chưa đủ số liệu', zero_target: 'Kế hoạch bằng 0' }[row.status] || '—'}</td></tr>)}</tbody></table></div> : <p className="management-empty">{resource.data.reason || 'Chưa có kế hoạch phù hợp với kỳ báo cáo.'}</p>)}
    </>}
  </section>;
}

function PlanImport({ apiBase, onImported, terminals }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const fileInput = useRef(null);
  const task = useTask();
  async function previewFile(event) {
    event.preventDefault();
    if (!file) return;
    setPreview(null);
    const form = new FormData();
    form.append('file', file);
    const outcome = await task.run(async (signal) => validatedPlanPreview(await apiRequest('/plans/import/preview', { method: 'POST', body: form, signal, baseUrl: apiBase }), terminals));
    if (outcome.ok) setPreview(outcome.result);
  }
  async function importRows() {
    if (!preview?.valid || !Array.isArray(preview.rows) || !preview.rows.length) return;
    const outcome = await task.run((signal) => apiRequest('/plans/import', { method: 'POST', body: { rows: preview.rows }, signal, baseUrl: apiBase }), 'Đã nhập kế hoạch dưới dạng bản nháp.');
    if (outcome.ok) { setPreview(null); setFile(null); if (fileInput.current) fileInput.current.value = ''; onImported(); }
  }
  return <details className="management-editor"><summary>Nhập kế hoạch từ Excel</summary><form className="management-upload" onSubmit={previewFile}>
    <label>Tệp kế hoạch (.xlsx)<input ref={fileInput} type="file" accept=".xlsx" required disabled={task.busy} onChange={(event) => { setFile(event.target.files?.[0] || null); setPreview(null); }} /></label>
    <button className="button" type="submit" disabled={!file || task.busy}><Upload size={15} />Xem trước</button>
    <button className="button" type="button" disabled={task.busy} onClick={() => task.run((signal) => downloadFile('/plans/template.xlsx', 'mau-ke-hoach.xlsx', { signal, baseUrl: apiBase }))}><Download size={15} />Tải mẫu Excel</button>
  </form><TaskState task={task} />
    {preview && <div className="management-preview"><h4>Kết quả kiểm tra</h4>{Array.isArray(preview.errors) && preview.errors.length > 0 && <ul className="management-import-errors" role="alert">{preview.errors.map((error, index) => <li key={index}>Dòng {error.row}: {error.message}</li>)}</ul>}
      {Array.isArray(preview.rows) && preview.rows.length > 0 && <><div className="table-scroll"><table><thead><tr><th>Xí nghiệp</th><th>Kỳ</th><th>Chỉ tiêu</th><th>Kế hoạch</th><th>Văn bản</th></tr></thead><tbody>{preview.rows.map((row, index) => <tr key={index}><th>{MANAGEMENT_TERMINALS[row.terminal] || row.terminal}</th><td>{row.month || `Chuyến ${row.voyage_id}`}</td><td>{METRIC_LABELS[row.metric] || row.metric}</td><td>{formatNumber(row.amount)}</td><td>{row.reference}</td></tr>)}</tbody></table></div><button className="button primary" type="button" disabled={!preview.valid || task.busy} onClick={importRows}><Check size={15} />Nhập {preview.rows.length} dòng kế hoạch</button></>}
    </div>}
  </details>;
}

function VoyagePicker({ terminal, value, onChange, apiBase, disabled }) {
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [revision, setRevision] = useState(0);
  const result = itemsOrError(useResource(queryPath('/planning/voyages', { terminal, search: query }), apiBase, revision));
  const choices = result.items;
  const includesSelected = choices.some((row) => String(row.voyage_id) === String(value));
  function find() { setQuery(search.trim()); setRevision((number) => number + 1); }
  return <div className="management-wide voyage-picker"><div className="management-search"><label>Tìm tàu hoặc mã chuyến<input type="search" value={search} maxLength={100} disabled={disabled} onChange={(event) => setSearch(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); find(); } }} /></label><button className="button" type="button" disabled={disabled || result.resource.loading} onClick={find}>Tìm chuyến</button></div>
    <label>Chuyến tàu kế hoạch<select value={value} onChange={onChange} required disabled={disabled}><option value="">Chọn chuyến tàu</option>{value && !includesSelected && <option value={value}>Chuyến {value} (đang chọn)</option>}{choices.map((row) => <option key={row.voyage_id} value={row.voyage_id}>{row.vessel_name || 'Chưa có tên tàu'} · {row.voyage_code || row.voyage_id} · Vào {row.arrival_date ? formatTimestamp(row.arrival_date) : 'chưa ghi nhận'}</option>)}</select></label>
    <ResourceState resource={result.resource} onRetry={find} /><p className="management-caption">Danh mục chuyến tại xí nghiệp, không giới hạn kỳ báo cáo. Tìm theo tên hoặc mã nếu chưa thấy chuyến cần chọn.</p>
  </div>;
}

function PlanFields({ form, setForm, terminals, apiBase, disabled }) {
  const update = (key) => (event) => setForm((value) => ({ ...value, [key]: event.target.value, ...(key === 'terminal' ? { voyage_id: '' } : {}) }));
  return <>
    <label>Xí nghiệp kế hoạch<select value={form.terminal} onChange={update('terminal')} required disabled={disabled}>{terminals.map((item) => <option value={item} key={item}>{MANAGEMENT_TERMINALS[item]}</option>)}</select></label>
    <label>Loại kế hoạch<select value={form.period_type} onChange={update('period_type')} disabled={disabled}><option value="month">Theo tháng</option><option value="voyage">Theo chuyến tàu</option></select></label>
    {form.period_type === 'month' ? <label>Tháng áp dụng<input type="month" value={form.month} onChange={update('month')} required disabled={disabled} /></label> : <VoyagePicker key={form.terminal} terminal={form.terminal} value={form.voyage_id} onChange={update('voyage_id')} apiBase={apiBase} disabled={disabled} />}
    <label>Chỉ tiêu<select value={form.metric} onChange={update('metric')} disabled={disabled}>{Object.entries(METRIC_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <label>Giá trị kế hoạch<input type="number" min="0" max="1000000000000" step="0.000001" value={form.amount} onChange={update('amount')} required disabled={disabled} /></label>
    <label className="management-wide">Số văn bản / nguồn phê duyệt<input value={form.reference} onChange={update('reference')} maxLength={500} required disabled={disabled} /></label>
    <label className="management-wide">Ghi chú<textarea value={form.note || ''} onChange={update('note')} maxLength={2000} rows={2} disabled={disabled} /></label>
  </>;
}

function PlanEditor({ plan, terminals, apiBase, onSaved, onCancel }) {
  const [form, setForm] = useState({ ...plan, voyage_id: String(plan.voyage_id || ''), month: plan.month || '', amount: String(plan.amount) });
  const task = useTask();
  async function save(event) {
    event.preventDefault();
    const outcome = await task.run((signal) => apiRequest(`/plans/${encodeURIComponent(plan.id)}`, { method: 'PATCH', body: { ...buildPlanPayload(form, terminals), expected_revision: plan.revision || 1 }, signal, baseUrl: apiBase }), 'Đã sửa bản nháp.');
    if (outcome.ok) onSaved();
  }
  return <section className="management-editor" aria-label="Sửa bản nháp kế hoạch"><h4>Sửa bản nháp kế hoạch · Phiên bản {plan.version}</h4><form className="management-form" onSubmit={save}><PlanFields form={form} setForm={setForm} terminals={terminals} apiBase={apiBase} disabled={task.busy} /><div className="management-actions management-wide"><button type="submit" className="button primary" disabled={task.busy}>Lưu thay đổi</button><button type="button" className="button" disabled={task.busy} onClick={onCancel}>Đóng chỉnh sửa</button></div></form><TaskState task={task} /></section>;
}

function PlanCancel({ plan, apiBase, onSaved, onCancel }) {
  const [note, setNote] = useState('');
  const task = useTask();
  async function cancel(event) {
    event.preventDefault();
    const outcome = await task.run((signal) => apiRequest(`/plans/${encodeURIComponent(plan.id)}/cancel`, { method: 'POST', body: { expected_revision: plan.revision || 1, note: note.trim() }, signal, baseUrl: apiBase }), 'Đã hủy bản nháp.');
    if (outcome.ok) onSaved();
  }
  return <section className="management-editor" aria-label="Hủy bản nháp kế hoạch"><h4>Hủy bản nháp · Phiên bản {plan.version}</h4><p className="management-caption">{METRIC_LABELS[plan.metric]}: {formatNumber(plan.amount)} · {plan.reference}</p><form className="management-form" onSubmit={cancel}><label className="management-wide">Lý do hủy<textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} required disabled={task.busy} /></label><div className="management-actions management-wide"><button type="submit" className="button primary" disabled={task.busy || !note.trim()}>Xác nhận hủy bản nháp</button><button type="button" className="button" onClick={onCancel} disabled={task.busy}>Giữ bản nháp</button></div></form><TaskState task={task} /></section>;
}

function PlanHistory({ id, apiBase, onClose }) {
  const resource = useResource(`/plans/${encodeURIComponent(id)}`, apiBase);
  const plan = resource.data;
  const actor = (value) => typeof value === 'object' ? value?.display_name || value?.username || value?.id || '—' : value || '—';
  return <section className="management-editor" aria-label="Lịch sử kế hoạch"><div className="management-heading"><h4>Lịch sử kế hoạch</h4><button type="button" className="button" onClick={onClose}>Đóng lịch sử</button></div><ResourceState resource={resource} />{plan && <><dl className="management-config"><div><dt>ID người lập</dt><dd>{actor(plan.created_by_name || plan.created_by)} · {formatTimestamp(plan.created_at)}</dd></div><div><dt>ID người duyệt</dt><dd>{actor(plan.approved_by_name || plan.approved_by)}{plan.approved_at && ` · ${formatTimestamp(plan.approved_at)}`}</dd></div></dl>{Array.isArray(plan.history) && plan.history.length > 0 && <div className="table-scroll"><table><thead><tr><th>Thời gian</th><th>Thao tác</th><th>ID người thực hiện</th><th>Giá trị / văn bản</th><th>Ghi chú</th></tr></thead><tbody>{plan.history.map((event, index) => <tr key={event.id || index}><td>{formatTimestamp(event.created_at)}</td><td>{{ created: 'Tạo bản nháp', updated: 'Sửa bản nháp', approved: 'Duyệt', cancelled: 'Hủy bản nháp', legacy_baseline: 'Dữ liệu trước khi lưu lịch sử' }[event.action] || event.action}</td><td>{actor(event.actor_id)}</td><td>{formatNumber(event.snapshot?.amount)}<small>{event.snapshot?.reference}</small></td><td>{event.note || '—'}</td></tr>)}</tbody></table></div>}</>}</section>;
}

function PlansPanel({ user, filters, report, apiBase, onReportChange }) {
  const terminals = userTerminals(user);
  const [month, setMonth] = useState(filters.start_date.slice(0, 7));
  const [terminal, setTerminal] = useState(filters.terminal);
  const [periodType, setPeriodType] = useState('month');
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [form, setForm] = useState({ terminal: terminals.includes(filters.terminal) ? filters.terminal : terminals[0] || '', period_type: 'month', month: filters.start_date.slice(0, 7), metric: 'tonnage', amount: '', reference: '', note: '', voyage_id: '' });
  const [editing, setEditing] = useState(null);
  const [cancelling, setCancelling] = useState(null);
  const [historyId, setHistoryId] = useState(null);
  const task = useTask();
  const result = itemsOrError(useResource(queryPath('/plans', { month: periodType === 'month' ? month : undefined, period_type: periodType, terminal, page, page_size: 25 }), apiBase, revision));
  const refresh = () => setRevision((value) => value + 1);
  async function createPlan(event) {
    event.preventDefault();
    const outcome = await task.run((signal) => apiRequest('/plans', { method: 'POST', body: buildPlanPayload(form, terminals), signal, baseUrl: apiBase }), 'Đã lưu bản nháp kế hoạch.');
    if (outcome.ok) { setForm((value) => ({ ...value, amount: '', note: '' })); setPeriodType(form.period_type); if (form.month) setMonth(form.month); setPage(1); refresh(); }
  }
  async function approve(plan) {
    const outcome = await task.run((signal) => apiRequest(`/plans/${encodeURIComponent(plan.id)}/approve`, { method: 'POST', body: { expected_revision: plan.revision || 1 }, signal, baseUrl: apiBase }), 'Đã duyệt kế hoạch.');
    if (outcome.ok) { refresh(); onReportChange?.(); }
  }
  return <div className="management-stack"><PlanProgress report={report} filters={filters} apiBase={apiBase} revision={revision} />
    <section className="management-card"><div className="management-heading"><h3>Kế hoạch và phiên bản</h3><button className="button" type="button" onClick={refresh} disabled={result.resource.loading}><RefreshCw size={15} />Tải lại</button></div>
      <div className="management-filters"><label>Danh sách kế hoạch<select value={periodType} onChange={(event) => { setPeriodType(event.target.value); setPage(1); }}><option value="month">Theo tháng</option><option value="voyage">Theo chuyến tàu</option></select></label><label>Tháng kế hoạch<input type="month" value={month} onChange={(event) => { setMonth(event.target.value); setPage(1); }} /></label><label>Xí nghiệp<select value={terminal} onChange={(event) => { setTerminal(event.target.value); setPage(1); }}><option value="all">Trong phạm vi được cấp</option>{terminals.map((item) => <option value={item} key={item}>{MANAGEMENT_TERMINALS[item]}</option>)}</select></label></div>
      {editing && <PlanEditor key={`${editing.id}/${editing.revision}`} plan={editing} terminals={terminals} apiBase={apiBase} onSaved={() => { setEditing(null); refresh(); }} onCancel={() => setEditing(null)} />}
      {cancelling && <PlanCancel key={cancelling.id} plan={cancelling} apiBase={apiBase} onSaved={() => { setCancelling(null); refresh(); }} onCancel={() => setCancelling(null)} />}
      {historyId && <PlanHistory key={historyId} id={historyId} apiBase={apiBase} onClose={() => setHistoryId(null)} />}
      {canManage(user) && <><details className="management-editor"><summary>Tạo phiên bản kế hoạch</summary><form className="management-form" onSubmit={createPlan}>
        <PlanFields form={form} setForm={setForm} terminals={terminals} apiBase={apiBase} disabled={task.busy} /><div className="management-actions management-wide"><button className="button primary" type="submit" disabled={task.busy || !terminals.length}><Plus size={15} />Lưu bản nháp</button></div>
      </form></details><PlanImport apiBase={apiBase} terminals={terminals} onImported={refresh} /></>}
      <TaskState task={task} /><ResourceState resource={result.resource} onRetry={refresh} />
      {result.resource.data && (result.items.length ? <><div className="table-scroll"><table><thead><tr><th>Kỳ / xí nghiệp</th><th>Chỉ tiêu</th><th>Kế hoạch</th><th>Phiên bản</th><th>Trạng thái</th><th>Văn bản</th><th>Thao tác</th></tr></thead><tbody>{result.items.map((plan) => <tr key={plan.id}><th scope="row">{plan.month || `Chuyến ${plan.voyage_id}`}<small>{MANAGEMENT_TERMINALS[plan.terminal] || plan.terminal}</small></th><td>{METRIC_LABELS[plan.metric] || plan.metric}</td><td>{formatNumber(plan.amount)}</td><td>{plan.version}</td><td><span className={`management-status status-${plan.status}`}>{PLAN_STATUS_LABELS[plan.status] || plan.status}</span>{plan.status === 'approved' && plan.is_current === false && <small>Phiên bản trước</small>}</td><td>{plan.reference}</td><td><div className="management-row-actions">{canManage(user) && plan.status === 'draft' && <><button className="button" type="button" disabled={task.busy || Boolean(editing || cancelling)} onClick={() => approve(plan)}><Check size={14} />Duyệt</button><button className="button" type="button" disabled={task.busy} onClick={() => { setCancelling(null); setEditing(plan); }}>Sửa nháp</button><button className="button" type="button" disabled={task.busy} onClick={() => { setEditing(null); setCancelling(plan); }}>Hủy nháp</button></>}<button className="button" type="button" onClick={() => setHistoryId(plan.id)}>Lịch sử</button></div></td></tr>)}</tbody></table></div><Pager page={page} total={result.resource.data.total || 0} onChange={setPage} /></> : <p className="management-empty">Chưa có kế hoạch trong kỳ và phạm vi đã chọn.</p>)}
    </section>
  </div>;
}

function IssueEditor({ row, issue, user, apiBase, onSaved }) {
  const [revision, setRevision] = useState(0);
  const resource = useResource(queryPath('/issues', { terminal: row.terminal_id, namespace: 'tally_shift', source_id: row.source_id || row.id }), apiBase, revision);
  const result = itemsOrError(resource);
  const latest = result.items.filter((item) => item.issue === issue).sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))[0];
  const [draft, setDraft] = useState(null);
  const task = useTask();
  const value = draft || { status: latest?.status || 'open', note: latest?.note || '' };
  async function save(event) {
    event.preventDefault();
    const outcome = await task.run((signal) => apiRequest('/issues', { method: 'POST', body: { terminal: row.terminal_id, namespace: 'tally_shift', source_id: String(row.source_id || row.id), issue, status: value.status, note: value.note.trim() }, signal, baseUrl: apiBase }), 'Đã lưu kết quả đối soát.');
    if (outcome.ok) { setDraft(null); setRevision((number) => number + 1); onSaved?.(); }
  }
  return <div className="management-issue-editor"><ResourceState resource={result.resource} onRetry={() => setRevision((number) => number + 1)} />
    {result.resource.data && (canManage(user) ? <form className="management-form" onSubmit={save}>
      <label>Trạng thái đối soát<select value={value.status} onChange={(event) => setDraft({ ...value, status: event.target.value })}>{Object.entries(ISSUE_STATUS_LABELS).map(([status, label]) => <option key={status} value={status}>{label}</option>)}</select></label>
      <label className="management-wide">Nội dung đối soát<textarea value={value.note} onChange={(event) => setDraft({ ...value, note: event.target.value })} rows={2} maxLength={4000} required /></label>
      <div className="management-actions management-wide"><button className="button primary" type="submit" disabled={task.busy}>Lưu đối soát</button>{latest?.updated_at && <span>Lần cập nhật: {formatTimestamp(latest.updated_at)}</span>}</div>
    </form> : <div className="management-note"><strong>{latest ? ISSUE_STATUS_LABELS[latest.status] || latest.status : 'Chưa có kết quả đối soát'}</strong>{latest?.note && <p>{latest.note}</p>}{latest?.updated_at && <small>{formatTimestamp(latest.updated_at)}</small>}</div>)}
    <TaskState task={task} />
  </div>;
}

function ReconciliationPanel({ user, report, apiBase, onReportChange }) {
  const [issue, setIssue] = useState('missing_weight');
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState(null);
  const [revision, setRevision] = useState(0);
  const reportId = report?.meta?.report_id;
  const resource = useResource(reportId ? queryPath(`/reports/${encodeURIComponent(reportId)}/operations`, { issue, page, page_size: 25 }) : null, apiBase, revision);
  const operations = resource.data?.operations;
  const rows = Array.isArray(operations?.rows) ? operations.rows : [];
  const malformed = resource.data && (!operations || !Array.isArray(operations.rows) || !Number.isInteger(operations.total));
  const displayedResource = malformed ? { ...resource, data: null, error: 'Dữ liệu đối soát chưa đúng cấu trúc. Vui lòng tải lại.' } : resource;
  const requestIdentity = `${reportId}/${issue}/${page}`;
  return <section className="management-card"><div className="management-heading"><h3>Đối soát dữ liệu nguồn</h3>{onReportChange && <button className="button" type="button" onClick={onReportChange}><RefreshCw size={15} />Tải báo cáo mới</button>}</div>
    {!reportId ? <p className="management-empty">Tải báo cáo sản lượng để xem các dòng cần đối soát.</p> : <>
      <div className="management-filters"><label>Nội dung đối soát<select value={issue} onChange={(event) => { setIssue(event.target.value); setPage(1); setExpanded(null); }}>{Object.entries(ISSUE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{operations && !resource.loading && <span>{formatNumber(operations.total, 0)} dòng trong kỳ báo cáo</span>}</div>
      <ResourceState resource={displayedResource} onRetry={() => setRevision((number) => number + 1)} />
      {displayedResource.data && (rows.length ? <><div className="table-scroll"><table className="management-operations"><thead><tr><th>Mã / ngày</th><th>Xí nghiệp / tàu</th><th>Hàng hóa</th><th>Số lượng nguồn</th><th>Trọng lượng nguồn</th><th>Tấn</th><th>Đối soát</th></tr></thead><tbody>{rows.map((row) => {
        const rowKey = row.row_key || `${row.terminal_id}:${row.id}`;
        const key = `${requestIdentity}/${rowKey}`;
        const open = expanded === key;
        return <Fragment key={rowKey}><tr><th scope="row">{row.operation_code || row.id}<small>{formatDate(row.operation_date)}</small>{row.shift_code && <small>Ca {row.shift_code}</small>}</th><td>{row.terminal_name || MANAGEMENT_TERMINALS[row.terminal_id]}<small>{row.vessel_name || '—'}</small></td><td>{row.cargo_name || '—'}</td><td>{formatNumber(row.quantity)}<small>{row.quantity_unit_name || row.quantity_unit || '—'}</small></td><td>{formatNumber(row.weight)}<small>{row.weight_unit_name || row.weight_unit || '—'}</small></td><td>{formatNumber(row.tonnage)}</td><td><button className="button" type="button" aria-expanded={open} aria-controls={`issue-${rowKey}`} onClick={() => setExpanded(open ? null : key)}>{canManage(user) ? 'Ghi nhận' : 'Xem ghi chú'}</button></td></tr>{open && <tr><td colSpan={7} id={`issue-${rowKey}`}><IssueEditor row={row} issue={issue} user={user} apiBase={apiBase} /></td></tr>}</Fragment>;
      })}</tbody></table></div><Pager page={page} total={operations.total} onChange={(value) => { setPage(value); setExpanded(null); }} /></> : <p className="management-empty">Không có dòng dữ liệu thuộc nội dung đối soát đã chọn.</p>)}
    </>}
  </section>;
}

function TemporaryPassword({ result, onDismiss }) {
  const [visible, setVisible] = useState(false);
  if (!result?.temporary_password) return null;
  return <section className="management-password" aria-label="Mật khẩu tạm thời"><div><strong>{result.user?.display_name || result.user?.username}</strong><p>Mật khẩu tạm thời được hiển thị trong phiên này. Người dùng phải đổi khi đăng nhập.</p></div><label>Mật khẩu tạm thời<input type={visible ? 'text' : 'password'} value={result.temporary_password} readOnly autoComplete="off" /></label><div className="management-actions"><button className="button" type="button" onClick={() => setVisible((value) => !value)}>{visible ? <EyeOff size={15} /> : <Eye size={15} />}{visible ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}</button><button className="button" type="button" onClick={onDismiss}>Đã ghi nhận</button></div></section>;
}

function UserEditor({ item, user, apiBase, onSaved, onCancel }) {
  const [form, setForm] = useState({ ...item, terminals: [...item.terminals] });
  const task = useTask();
  async function save(event) {
    event.preventDefault();
    const outcome = await task.run((signal) => {
      const { display_name, role, terminals } = buildUserPayload(form);
      return apiRequest(`/users/${encodeURIComponent(item.id)}`, { method: 'PATCH', body: { display_name, role, terminals }, signal, baseUrl: apiBase });
    });
    if (outcome.ok) onSaved();
  }
  return <section className="management-editor" aria-label="Sửa tài khoản"><h4>Sửa tài khoản {item.username}</h4><form className="management-form" onSubmit={save}>
    <label>Tên hiển thị<input value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} maxLength={160} required autoFocus /></label>
    <label>Quyền tài khoản<select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}>{Object.entries(MANAGEMENT_ROLES).map(([role, label]) => <option key={role} value={role}>{label}</option>)}</select></label>
    <fieldset className="management-scope management-wide"><legend>Xí nghiệp được cấp</legend>{userTerminals(user).map((terminal) => <label key={terminal}><input type="checkbox" checked={form.terminals.includes(terminal)} onChange={(event) => setForm({ ...form, terminals: event.target.checked ? [...form.terminals, terminal] : form.terminals.filter((value) => value !== terminal) })} />{MANAGEMENT_TERMINALS[terminal]}</label>)}</fieldset>
    <p className="management-caption management-wide">Thay đổi quyền sẽ kết thúc các phiên đăng nhập hiện tại của tài khoản.</p>
    <div className="management-actions management-wide"><button className="button primary" type="submit" disabled={task.busy}>Lưu tài khoản</button><button className="button" type="button" onClick={onCancel} disabled={task.busy}>Hủy</button></div>
  </form><TaskState task={task} /></section>;
}

function UsersPanel({ user, apiBase }) {
  const [revision, setRevision] = useState(0);
  const result = itemsOrError(useResource('/users', apiBase, revision));
  const [form, setForm] = useState({ username: '', display_name: '', role: 'viewer', terminals: [] });
  const [password, setPassword] = useState(null);
  const [editing, setEditing] = useState(null);
  const task = useTask();
  const refresh = () => setRevision((number) => number + 1);
  const update = (key) => (event) => setForm((value) => ({ ...value, [key]: event.target.value }));
  async function createUser(event) {
    event.preventDefault();
    setPassword(null);
    const outcome = await task.run((signal) => apiRequest('/users', { method: 'POST', body: buildUserPayload(form), signal, baseUrl: apiBase }), 'Đã tạo tài khoản.');
    if (outcome.ok) { setPassword(outcome.result); setForm({ username: '', display_name: '', role: 'viewer', terminals: [] }); refresh(); }
  }
  async function resetPassword(item) {
    setPassword(null);
    const outcome = await task.run((signal) => apiRequest(`/users/${encodeURIComponent(item.id)}/reset-password`, { method: 'POST', signal, baseUrl: apiBase }), 'Đã cấp mật khẩu tạm thời mới.');
    if (outcome.ok) setPassword(outcome.result);
  }
  async function toggleActive(item) {
    const outcome = await task.run((signal) => apiRequest(`/users/${encodeURIComponent(item.id)}`, { method: 'PATCH', body: { is_active: !item.is_active }, signal, baseUrl: apiBase }), item.is_active ? 'Đã khóa tài khoản.' : 'Đã mở tài khoản.');
    if (outcome.ok) refresh();
  }
  return <section className="management-card"><div className="management-heading"><h3>Tài khoản nội bộ</h3><button className="button" type="button" onClick={refresh} disabled={result.resource.loading}><RefreshCw size={15} />Tải lại</button></div>
    {editing && <UserEditor key={editing.id} item={editing} user={user} apiBase={apiBase} onSaved={() => { setEditing(null); refresh(); }} onCancel={() => setEditing(null)} />}
    <TemporaryPassword result={password} onDismiss={() => setPassword(null)} />
    <details className="management-editor"><summary>Tạo tài khoản</summary><form className="management-form" onSubmit={createUser}>
      <label>Tên đăng nhập<input value={form.username} onChange={update('username')} minLength={3} maxLength={80} pattern="[A-Za-z0-9][A-Za-z0-9._-]+" autoComplete="off" required /></label><label>Tên người dùng<input value={form.display_name} onChange={update('display_name')} maxLength={160} required /></label>
      <label>Quyền<select value={form.role} onChange={update('role')}>{Object.entries(MANAGEMENT_ROLES).map(([role, label]) => <option key={role} value={role}>{label}</option>)}</select></label>
      <fieldset className="management-scope"><legend>Phạm vi xí nghiệp</legend>{userTerminals(user).map((terminal) => <label key={terminal}><input type="checkbox" checked={form.terminals.includes(terminal)} onChange={(event) => setForm((value) => ({ ...value, terminals: event.target.checked ? [...value.terminals, terminal] : value.terminals.filter((item) => item !== terminal) }))} />{MANAGEMENT_TERMINALS[terminal]}</label>)}</fieldset>
      <div className="management-actions management-wide"><button className="button primary" type="submit" disabled={task.busy}><Plus size={15} />Tạo tài khoản</button></div>
    </form></details><TaskState task={task} /><ResourceState resource={result.resource} onRetry={refresh} />
    {result.resource.data && (result.items.length ? <div className="table-scroll"><table><thead><tr><th>Người dùng</th><th>Quyền</th><th>Xí nghiệp</th><th>Trạng thái</th><th>Thao tác</th></tr></thead><tbody>{result.items.map((item) => <tr key={item.id}><th scope="row">{item.display_name}<small>{item.username}</small></th><td>{MANAGEMENT_ROLES[item.role] || item.role}</td><td>{userTerminals(item).map((terminal) => MANAGEMENT_TERMINALS[terminal]).join(', ')}</td><td>{item.is_active ? 'Đang hoạt động' : 'Đã khóa'}{item.must_change_password && <small>Cần đổi mật khẩu</small>}</td><td><div className="management-row-actions"><button className="button" type="button" onClick={() => { setPassword(null); setEditing(item); }} disabled={task.busy || !item.terminals.every((terminal) => user.terminals.includes(terminal))}>Sửa quyền</button><button className="button" type="button" onClick={() => resetPassword(item)} disabled={task.busy || !item.is_active || !item.terminals.every((terminal) => user.terminals.includes(terminal))}><KeyRound size={14} />Cấp mật khẩu mới</button>{item.id !== user.id && <button className="button" type="button" onClick={() => toggleActive(item)} disabled={task.busy || !item.terminals.every((terminal) => user.terminals.includes(terminal))}>{item.is_active ? 'Khóa' : 'Mở khóa'}</button>}</div></td></tr>)}</tbody></table></div> : <p className="management-empty">Chưa có tài khoản.</p>)}
  </section>;
}

const comparisonLabels = { total_tonnage: 'Sản lượng qua cảng (tấn)', total_teu: 'Container (TEU)', vessel_calls: 'Chuyến tàu có phát sinh', record_count: 'Dòng tác nghiệp', trend_tonnage: 'Biến động sản lượng (%)', trend_teu: 'Biến động TEU (%)', trend_vessels: 'Biến động chuyến tàu (%)' };

function ClosedPlanSnapshot({ item, apiBase, onClose }) {
  const resource = useResource(`/closed-reports/${encodeURIComponent(item.id)}`, apiBase);
  const planning = resource.data?.planning;
  const plans = Array.isArray(planning?.plans) ? planning.plans : [];
  return <section className="management-editor" aria-label="Kế hoạch tại thời điểm chốt"><div className="management-heading"><h4>Kế hoạch tại thời điểm chốt · Phiên bản báo cáo {item.version}</h4><button className="button" type="button" onClick={onClose}>Đóng kế hoạch đã chốt</button></div><ResourceState resource={resource} />{resource.data && (!planning?.captured ? <p className="management-empty">Bản chốt này chưa lưu kế hoạch tại thời điểm chốt.</p> : <><p className="management-caption">Kế hoạch và tỷ lệ hoàn thành được giữ theo lần chốt này.</p>{!planning.eligible && <p className="management-empty">{planning.reason || 'Kỳ này không đủ điều kiện đối chiếu kế hoạch tháng.'}</p>}{Array.isArray(planning.rows) && planning.rows.length > 0 && <div className="table-scroll"><table><thead><tr><th>Xí nghiệp / chỉ tiêu</th><th>Phiên bản kế hoạch</th><th>Kế hoạch</th><th>Thực hiện</th><th>Hoàn thành</th><th>Văn bản / duyệt</th></tr></thead><tbody>{planning.rows.map((row) => {
      const plan = plans.find((value) => value.id === row.plan_id) || {};
      return <tr key={`${row.terminal}/${row.metric}`}><th>{MANAGEMENT_TERMINALS[row.terminal] || row.terminal}<small>{METRIC_LABELS[row.metric] || row.metric}</small></th><td>{row.plan_version || '—'}</td><td>{formatNumber(row.target)}</td><td>{formatNumber(row.actual)}</td><td>{row.completion_percent == null ? '—' : `${formatNumber(row.completion_percent, 1)}%`}</td><td>{row.reference || plan.reference || 'Chưa có kế hoạch duyệt'}{(row.approved_at || plan.approved_at) && <small>{formatTimestamp(row.approved_at || plan.approved_at)} · ID {row.approved_by || plan.approved_by}</small>}</td></tr>;
    })}</tbody></table></div>}</>)}</section>;
}

function ClosedReportsPanel({ user, filters, report, apiBase, onReportChange }) {
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [title, setTitle] = useState('');
  const [note, setNote] = useState('');
  const [comparison, setComparison] = useState(null);
  const [planningItem, setPlanningItem] = useState(null);
  const result = itemsOrError(useResource(queryPath('/closed-reports', { terminal: filters.terminal, page, page_size: 25 }), apiBase, revision));
  const task = useTask();
  const reportId = report?.meta?.report_id;
  const refresh = () => setRevision((number) => number + 1);
  const defaultTitle = `Sản lượng ${formatDate(filters.start_date)} – ${formatDate(filters.end_date)}`;
  async function closeReport(event) {
    event.preventDefault();
    if (!reportId) return;
    const outcome = await task.run((signal) => apiRequest('/closed-reports', { method: 'POST', body: { report_id: reportId, title: title.trim() || defaultTitle, note: note.trim() }, signal, baseUrl: apiBase }), 'Đã chốt báo cáo thành một phiên bản mới.');
    if (outcome.ok) { setTitle(''); setNote(''); setPage(1); refresh(); }
  }
  async function compare(item) {
    setComparison(null);
    const outcome = await task.run((signal) => apiRequest(`/closed-reports/${encodeURIComponent(item.id)}/compare`, { method: 'POST', body: { report_id: reportId }, signal, baseUrl: apiBase }));
    if (outcome.ok) setComparison({ reportId, item, data: outcome.result });
  }
  const visibleComparison = comparison?.reportId === reportId ? comparison : null;
  return <section className="management-card"><div className="management-heading"><h3>Báo cáo đã chốt</h3><button className="button" type="button" onClick={refresh} disabled={result.resource.loading}><RefreshCw size={15} />Tải lại danh sách</button></div>
    {canManage(user) && <details className="management-editor"><summary>Chốt báo cáo đang xem</summary>{reportId ? <form className="management-form" onSubmit={closeReport}>
      <p className="management-context management-wide">{formatDate(filters.start_date)} – {formatDate(filters.end_date)} · {MANAGEMENT_TERMINALS[filters.terminal] || 'Toàn công ty'}{report.meta.source_read_at && <> · Dữ liệu đọc lúc {formatTimestamp(report.meta.source_read_at)}</>}</p>
      <label className="management-wide">Tên báo cáo<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder={defaultTitle} maxLength={200} /></label><label className="management-wide">Nội dung chốt<textarea value={note} onChange={(event) => setNote(event.target.value)} rows={2} maxLength={4000} /></label>
      <div className="management-actions management-wide"><button className="button primary" type="submit" disabled={task.busy}><Check size={15} />Chốt báo cáo hiện tại</button></div>
    </form> : <div className="management-empty"><p>Tải báo cáo sản lượng trước khi chốt.</p>{onReportChange && <button className="button" type="button" onClick={onReportChange}>Tải báo cáo</button>}</div>}</details>}
    <TaskState task={task} /><ResourceState resource={result.resource} onRetry={refresh} />
    {planningItem && <ClosedPlanSnapshot key={planningItem.id} item={planningItem} apiBase={apiBase} onClose={() => setPlanningItem(null)} />}
    {visibleComparison && <section className="management-comparison" aria-label="Kết quả so sánh báo cáo"><div className="management-heading"><h4>So sánh với {visibleComparison.item.title || `báo cáo phiên bản ${visibleComparison.item.version}`}</h4><button className="button" type="button" onClick={() => setComparison(null)}>Đóng so sánh</button></div>
      {Array.isArray(visibleComparison.data.changes) ? <><p>{visibleComparison.data.changed ? 'Số liệu nguồn đã thay đổi so với bản chốt.' : 'Số liệu không thay đổi so với bản chốt.'}</p>{visibleComparison.data.changes.length > 0 ? <div className="table-scroll"><table><thead><tr><th>Chỉ tiêu</th><th>Đã chốt</th><th>Đang xem</th><th>Chênh lệch</th></tr></thead><tbody>{visibleComparison.data.changes.map((row) => <tr key={row.metric}><th>{comparisonLabels[row.metric] || row.metric}</th><td>{formatNumber(row.closed)}</td><td>{formatNumber(row.current)}</td><td>{formatNumber(row.delta)}</td></tr>)}</tbody></table></div> : visibleComparison.data.source_changed && <p>Tổng các chỉ tiêu không đổi; có thay đổi ở dữ liệu chi tiết.</p>}<small>Đối chiếu lúc {formatTimestamp(visibleComparison.data.compared_at)}</small></> : <p className="management-error" role="alert">Kết quả so sánh chưa đúng cấu trúc. Vui lòng thử lại.</p>}
    </section>}
    {result.resource.data && (result.items.length ? <><div className="table-scroll"><table><thead><tr><th>Báo cáo / kỳ</th><th>Phạm vi</th><th>Phiên bản</th><th>Ngày chốt</th><th>Số dòng nguồn</th><th>Thao tác</th></tr></thead><tbody>{result.items.map((item) => {
      const sameScope = reportId && item.terminal === filters.terminal && item.start_date === filters.start_date && item.end_date === filters.end_date;
      return <tr key={item.id}><th scope="row">{item.title || 'Báo cáo sản lượng'}<small>{formatDate(item.start_date)} – {formatDate(item.end_date)}</small></th><td>{MANAGEMENT_TERMINALS[item.terminal] || 'Toàn công ty'}</td><td>{item.version}</td><td>{formatTimestamp(item.created_at)}</td><td>{formatNumber(item.source_fact_count, 0)}</td><td><div className="management-row-actions"><button className="button" type="button" disabled={task.busy} onClick={() => task.run((signal) => downloadFile(`/closed-reports/${encodeURIComponent(item.id)}/export.xlsx`, `bao-cao-da-chot-${item.id}-v${item.version}.xlsx`, { signal, baseUrl: apiBase }))}><Download size={14} />Excel</button><button className="button" type="button" onClick={() => setPlanningItem(item)}>Kế hoạch đã chốt</button><button className="button" type="button" disabled={task.busy || !sameScope} title={sameScope ? 'So sánh với báo cáo đang xem' : 'Chọn cùng kỳ và xí nghiệp để so sánh'} onClick={() => compare(item)}>So sánh</button></div></td></tr>;
    })}</tbody></table></div><Pager page={page} total={result.resource.data.total || 0} onChange={setPage} /></> : <p className="management-empty">Chưa có báo cáo đã chốt trong phạm vi được cấp.</p>)}
  </section>;
}

const operationLabels = { get_report: 'Tải báo cáo', get_report_snapshot: 'Đọc bản báo cáo', source_read: 'Đọc nguồn SQL', drilldown: 'Chi tiết tác nghiệp', get_operations: 'Chi tiết tác nghiệp', plan_progress: 'Đối chiếu kế hoạch', get_voyage: 'Chi tiết chuyến tàu', get_voyage_from_report: 'Chuyến tàu trong báo cáo', get_voyage_progress: 'Kế hoạch chuyến tàu', export_snapshot: 'Xuất báo cáo', export_drilldown: 'Xuất chi tiết tác nghiệp' };

function OperationsPanel({ apiBase }) {
  const [revision, setRevision] = useState(0);
  const resource = useResource('/admin/metrics', apiBase, revision);
  const data = resource.data;
  const valid = data && data.cache && data.operations && typeof data.operations === 'object';
  return <section className="management-card"><div className="management-heading"><h3>Vận hành hệ thống</h3><button className="button" type="button" disabled={resource.loading} onClick={() => setRevision((number) => number + 1)}><RefreshCw size={15} />Cập nhật</button></div><ResourceState resource={resource} />
    {valid ? <><div className="management-metrics">{[['Báo cáo trong RAM', data.cache.snapshots], ['Dòng nguồn trong RAM', data.cache.stored_rows], ['Yêu cầu đang xử lý', data.cache.inflight], ['Lượt dùng bộ nhớ đệm', data.cache.hits]].map(([label, value]) => <article key={label}><span>{label}</span><strong>{formatNumber(value, 0)}</strong></article>)}</div>
      {data.storage && <><h4>Bản báo cáo lưu trên ổ đĩa</h4><div className="management-metrics">{[['Báo cáo lưu', data.storage.snapshots], ['Dòng nguồn lưu', data.storage.stored_rows], ['Dung lượng nén (MiB)', (data.storage.compressed_bytes || 0) / 1048576]].map(([label, value]) => <article key={label}><span>{label}</span><strong>{formatNumber(value, label.includes('MiB') ? 2 : 0)}</strong></article>)}</div><p className="management-caption">Giới hạn: {formatNumber(data.storage.max_snapshots, 0)} báo cáo · {formatNumber(data.storage.max_rows, 0)} dòng · {formatNumber(data.storage.max_bytes / 1048576, 0)} MiB. Bản lưu ổ đĩa giúp tra cứu khi bản trong RAM đã được thay thế.</p></>}
      <div className="table-scroll"><table><thead><tr><th>Tác vụ</th><th>Yêu cầu</th><th>Thành công</th><th>Lỗi</th><th>Trung bình (ms)</th><th>Chậm nhất (ms)</th></tr></thead><tbody>{Object.entries(data.operations).map(([name, values]) => <tr key={name}><th>{operationLabels[name] || name}</th><td>{formatNumber(values.request_count, 0)}</td><td>{formatNumber(values.success_count, 0)}</td><td>{formatNumber(values.failure_count, 0)}</td><td>{formatNumber(values.latency_ms_mean, 1)}</td><td>{formatNumber(values.latency_ms_max, 1)}</td></tr>)}</tbody></table></div>
      <dl className="management-config"><div><dt>Thời gian dùng lại báo cáo</dt><dd>{formatNumber(data.cache.ttl_seconds, 0)} giây</dd></div><div><dt>Thời gian lưu báo cáo để tra cứu</dt><dd>{formatNumber(data.cache.snapshot_ttl_seconds, 0)} giây</dd></div><div><dt>Số báo cáo tối đa trong RAM</dt><dd>{formatNumber(data.cache.max_snapshots, 0)}</dd></div><div><dt>Số dòng nguồn tối đa trong RAM</dt><dd>{formatNumber(data.cache.max_snapshot_rows, 0)}</dd></div></dl>
    </> : data && <p className="management-error" role="alert">Số liệu vận hành chưa đúng cấu trúc. Vui lòng cập nhật lại.</p>}
  </section>;
}

export default function Management({ mode = 'management', user, filters, report, apiBase, onReportChange }) {
  const area = mode === 'admin' ? 'admin' : 'management';
  const title = area === 'admin' ? 'Quản trị' : 'Kế hoạch & đối soát';
  const tabs = area === 'admin'
    ? [['users', 'Tài khoản'], ['operations', 'Vận hành']]
    : [['plans', 'Kế hoạch'], ['reconciliation', 'Đối soát'], ['closed', 'Báo cáo đã chốt']];
  const [selectedTab, setSelectedTab] = useState(tabs[0][0]);
  const tab = tabs.some(([id]) => id === selectedTab) ? selectedTab : tabs[0][0];
  const buttons = useRef([]);
  function navigateTabs(event, index) {
    const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    setSelectedTab(tabs[next][0]);
    buttons.current[next]?.focus();
  }
  const props = { user, filters, report, apiBase, onReportChange };
  if (area === 'admin' && user?.role !== 'admin') return null;
  return <section className="management" id={`${area}-content`} aria-label={title}>
    <div className="management-tabs" role="tablist" aria-label={title}>{tabs.map(([id, label], index) => <button key={id} ref={(element) => { buttons.current[index] = element; }} id={`${area}-tab-${id}`} type="button" role="tab" aria-selected={tab === id} aria-controls={`${area}-panel-${id}`} tabIndex={tab === id ? 0 : -1} onClick={() => setSelectedTab(id)} onKeyDown={(event) => navigateTabs(event, index)}>{label}</button>)}</div>
    <div role="tabpanel" id={`${area}-panel-${tab}`} aria-labelledby={`${area}-tab-${tab}`} tabIndex={0}>
      {tab === 'plans' && <PlansPanel {...props} />}{tab === 'reconciliation' && <ReconciliationPanel {...props} />}{tab === 'closed' && <ClosedReportsPanel {...props} />}{tab === 'users' && user?.role === 'admin' && <UsersPanel {...props} />}{tab === 'operations' && user?.role === 'admin' && <OperationsPanel {...props} />}
    </div>
  </section>;
}
