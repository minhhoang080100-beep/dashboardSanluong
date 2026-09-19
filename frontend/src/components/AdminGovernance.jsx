import { useEffect, useId, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { apiRequest } from '../api-client.js';
import { formatNumber, formatTimestamp } from '../dashboard-data.js';
import { MANAGEMENT_ROLES, MANAGEMENT_TERMINALS, userTerminals } from '../management-data.js';
import './AdminGovernance.css';

const PERMISSION_LABELS = { create: 'Nhập và sửa kế hoạch', approve: 'Duyệt kế hoạch' };
const ACTION_LABELS = { user_created: 'Tạo tài khoản', user_updated: 'Cập nhật tài khoản', password_reset: 'Đặt lại mật khẩu' };
const AUDIT_FIELDS = { display_name: 'Tên hiển thị', username: 'Tên đăng nhập', role: 'Vai trò', terminals: 'Xí nghiệp', plan_permissions: 'Quyền kế hoạch', is_active: 'Trạng thái', must_change_password: 'Đổi mật khẩu khi đăng nhập' };
const PAGE_SIZE = 25;
const EMPTY_FILTERS = { terminal: '', userId: '', action: '' };

export function PlanPermissionsField({ value, onChange, role, disabled = false }) {
  const helpId = useId();
  const editableRole = ['admin', 'manager'].includes(role);
  const selected = editableRole ? (Array.isArray(value) ? value : value === undefined ? ['create', 'approve'] : []) : [];
  return <fieldset className="plan-permissions-field" disabled={disabled || !editableRole} aria-describedby={helpId}>
    <legend>Quyền kế hoạch</legend>
    <div className="plan-permissions-options">{Object.entries(PERMISSION_LABELS).map(([permission, label]) => <label key={permission}>
      <input type="checkbox" checked={selected.includes(permission)} onChange={(event) => {
        const next = new Set(selected);
        if (event.target.checked) next.add(permission); else next.delete(permission);
        onChange(Object.keys(PERMISSION_LABELS).filter((item) => next.has(item)));
      }} /><span>{label}</span>
    </label>)}</div>
    <p id={helpId}>{editableRole ? 'Áp dụng trong phạm vi xí nghiệp đã cấp cho tài khoản.' : 'Tài khoản xem báo cáo không có quyền nhập hoặc duyệt kế hoạch.'}</p>
  </fieldset>;
}

function auditValue(field, value) {
  if (value === undefined || value === null) return '—';
  if (field === 'role') return typeof value === 'string' && Object.hasOwn(MANAGEMENT_ROLES, value) ? MANAGEMENT_ROLES[value] : 'Không xác định';
  if (field === 'terminals') return Array.isArray(value) ? value.filter((item) => typeof item === 'string' && Object.hasOwn(MANAGEMENT_TERMINALS, item)).map((item) => MANAGEMENT_TERMINALS[item]).join(', ') || 'Không có' : '—';
  if (field === 'plan_permissions') return Array.isArray(value) ? value.filter((item) => typeof item === 'string' && Object.hasOwn(PERMISSION_LABELS, item)).map((item) => PERMISSION_LABELS[item]).join(', ') || 'Không có' : '—';
  if (field === 'is_active') return value === true ? 'Đang hoạt động' : value === false ? 'Đã khóa' : '—';
  if (field === 'must_change_password') return value === true ? 'Bắt buộc' : value === false ? 'Không yêu cầu' : '—';
  return typeof value === 'string' ? value : '—';
}

function AuditEvent({ event, user }) {
  const before = event.before || {};
  const after = event.after;
  const fields = Object.keys(AUDIT_FIELDS).filter((field) => JSON.stringify(before[field]) !== JSON.stringify(after[field]));
  const name = typeof after.display_name === 'string' ? after.display_name : `Tài khoản #${event.user_id}`;
  return <li className="admin-audit-event">
    <div className="admin-audit-event-heading"><strong>{ACTION_LABELS[event.action]}</strong><time dateTime={event.created_at}>{formatTimestamp(event.created_at)}</time></div>
    <p className="admin-audit-target">{name}{typeof after.username === 'string' && <span> · {after.username}</span>} <span>· ID {event.user_id}</span></p>
    <p className="admin-audit-actor">Người thực hiện: {event.actor_id === user.id ? `${user.display_name || user.username || 'Bạn'} (bạn)` : `Tài khoản #${event.actor_id}`}</p>
    {event.action === 'password_reset' && <p className="admin-audit-note">Đã cấp mật khẩu tạm và thu hồi các phiên đăng nhập.</p>}
    {fields.length > 0 && <details className="admin-audit-changes"><summary>{event.before ? 'Xem thay đổi' : 'Thông tin đã tạo'}</summary>
      <dl>{fields.map((field) => <div key={field}><dt>{AUDIT_FIELDS[field]}</dt><dd><span><small>Trước</small>{auditValue(field, before[field])}</span><span><small>Sau</small>{auditValue(field, after[field])}</span></dd></div>)}</dl>
    </details>}
  </li>;
}

function validateAuditResponse(value, page) {
  if (!value || !Array.isArray(value.items) || !Number.isInteger(value.total) || value.total < value.items.length
      || value.page !== page || value.page_size !== PAGE_SIZE || value.items.length > PAGE_SIZE
      || value.items.some((event) => !event || !Object.hasOwn(ACTION_LABELS, event.action)
        || ![event.id, event.actor_id, event.user_id].every((id) => Number.isSafeInteger(id) && id > 0)
        || typeof event.created_at !== 'string' || !Number.isFinite(Date.parse(event.created_at))
        || !event.after || typeof event.after !== 'object' || Array.isArray(event.after)
        || (event.before !== null && (!event.before || typeof event.before !== 'object' || Array.isArray(event.before))))) {
    throw new Error('Nhật ký quản trị trả về chưa hợp lệ. Vui lòng tải lại.');
  }
  return value;
}

export function AdminAudit({ apiBase, user }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(EMPTY_FILTERS);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [filterError, setFilterError] = useState('');
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [resource, setResource] = useState({ key: '', data: null, error: '' });
  const terminals = userTerminals(user);
  const scopeKey = `${user?.id}/${user?.role}/${terminals.join(',')}`;
  const query = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
  if (filters.terminal) query.set('terminal', filters.terminal);
  if (filters.userId) query.set('user_id', filters.userId);
  if (filters.action) query.set('action', filters.action);
  const path = `/admin/events?${query}`;
  const enabled = open && user?.role === 'admin';
  const key = `${apiBase}/${scopeKey}/${path}/${revision}`;
  const data = enabled && resource.key === key ? resource.data : null;
  const error = enabled && resource.key === key ? resource.error : '';
  const loading = enabled && !data && !error;
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let active = true;
    apiRequest(path, { baseUrl: apiBase, signal: controller.signal })
      .then((value) => { if (active) setResource({ key, data: validateAuditResponse(value, page), error: '' }); })
      .catch((failure) => { if (active && !controller.signal.aborted) setResource({ key, data: null, error: failure.message || 'Chưa tải được nhật ký quản trị.' }); });
    return () => { active = false; controller.abort(); };
  }, [enabled, key, path, page, apiBase]);
  if (user?.role !== 'admin') return null;
  function submit(event) {
    event.preventDefault();
    const userId = draft.userId.trim();
    if (userId && (!/^[1-9]\d*$/.test(userId) || !Number.isSafeInteger(Number(userId)))) {
      setFilterError('ID tài khoản phải là số nguyên dương.');
      return;
    }
    setFilterError(''); setFilters({ ...draft, userId }); setPage(1); setRevision((value) => value + 1);
  }
  const pages = Math.max(1, Math.ceil((data?.total || 0) / PAGE_SIZE));
  return <details className="management-card admin-audit" open={open} onToggle={(event) => {
    if (event.target !== event.currentTarget) return;
    const next = event.currentTarget.open;
    setOpen(next);
    if (next) setRevision((value) => value + 1);
  }}>
    <summary>Nhật ký quản trị tài khoản</summary>
    {open && <div className="admin-audit-content">
      <p className="admin-audit-caption">Lịch sử tạo tài khoản, thay đổi quyền và đặt lại mật khẩu trong phạm vi được cấp.</p>
      <form className="admin-audit-filters" onSubmit={submit}>
        <label>Xí nghiệp<select value={draft.terminal} onChange={(event) => setDraft({ ...draft, terminal: event.target.value })}><option value="">Trong phạm vi được cấp</option>{terminals.map((terminal) => <option key={terminal} value={terminal}>{MANAGEMENT_TERMINALS[terminal]}</option>)}</select></label>
        <label>ID tài khoản<input type="text" inputMode="numeric" maxLength={16} placeholder="Tất cả tài khoản" value={draft.userId} aria-invalid={Boolean(filterError)} onChange={(event) => { setDraft({ ...draft, userId: event.target.value }); setFilterError(''); }} /></label>
        <label>Thao tác<select value={draft.action} onChange={(event) => setDraft({ ...draft, action: event.target.value })}><option value="">Tất cả thao tác</option>{Object.entries(ACTION_LABELS).map(([action, label]) => <option key={action} value={action}>{label}</option>)}</select></label>
        <button className="button primary" type="submit">Áp dụng</button>
        <button className="button" type="button" disabled={loading} onClick={() => setRevision((value) => value + 1)}><RefreshCw size={15} aria-hidden="true" />Tải lại nhật ký</button>
      </form>
      {filterError && <p className="management-error" role="alert">{filterError}</p>}
      {loading && <p className="management-empty" role="status">Đang tải nhật ký quản trị…</p>}
      {error && <div className="management-error" role="alert"><p>{error}</p><button className="button" type="button" onClick={() => setRevision((value) => value + 1)}>Thử lại</button></div>}
      {data && <>
        <p className="admin-audit-count" role="status">{formatNumber(data.total, 0)} sự kiện</p>
        {data.items.length ? <ol className="admin-audit-list">{data.items.map((event) => <AuditEvent key={event.id} event={event} user={user} />)}</ol> : <p className="management-empty">Không có sự kiện phù hợp với bộ lọc.</p>}
        {(pages > 1 || page > 1) && <div className="admin-audit-pagination"><span>Trang {page}/{Math.max(page, pages)}</span><div><button type="button" className="button" disabled={loading || page <= 1} onClick={() => setPage((value) => value - 1)}>Trước</button><button type="button" className="button" disabled={loading || page >= pages} onClick={() => setPage((value) => value + 1)}>Sau</button></div></div>}
      </>}
    </div>}
  </details>;
}
