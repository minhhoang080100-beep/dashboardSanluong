import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { AlertCircle, ArrowLeft, ArrowRight, Braces, Check, Copy, Database, Play, RefreshCw, Search, Table2 } from 'lucide-react';
import { apiRequest } from '../api-client.js';
import { formatNumber, formatTimestamp } from '../dashboard-data.js';
import {
  INSPECTOR_GROUPS, INSPECTOR_HEADERS, INSPECTOR_LIMITS, buildInspectorQuery,
  dateInput, initialInspectorValues, inspectorCell, inspectorColumns, inspectorFields,
  inspectorHeader, inspectorPagination, inspectorRequestAddress, inspectorRows,
  validateInspection, validateInspectorCatalog,
} from '../api-inspector.js';
import './ApiInspector.css';

export default function ApiInspector({ apiBase }) {
  const prefix = useId();
  const [catalog, setCatalog] = useState(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState('');
  const [catalogRevision, setCatalogRevision] = useState(0);
  const [search, setSearch] = useState('');
  const [group, setGroup] = useState('all');
  const [selectedId, setSelectedId] = useState('');
  const [values, setValues] = useState({});
  const [limit, setLimit] = useState(20);
  const [response, setResponse] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [tab, setTab] = useState('table');
  const [copyNotice, setCopyNotice] = useState('');
  const requestSequence = useRef(0);
  const requestController = useRef(null);
  const copySequence = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    requestSequence.current += 1;
    requestController.current?.abort();
    setCatalogLoading(true); setCatalogError(''); setCatalog(null);
    setResponse(null); setBusy(false); setError(''); setCopyNotice('');
    apiRequest('/admin/corporate-api/catalog', { baseUrl: apiBase, signal: controller.signal })
      .then((value) => {
        if (controller.signal.aborted) return;
        const result = validateInspectorCatalog(value);
        const first = result.resources[0];
        setCatalog(result); setSelectedId(first?.id || ''); setValues(first ? initialInspectorValues(first) : {}); setLimit(20);
      })
      .catch((failure) => { if (!controller.signal.aborted) setCatalogError(failure.message || 'Chưa tải được danh sách API.'); })
      .finally(() => { if (!controller.signal.aborted) setCatalogLoading(false); });
    return () => { controller.abort(); requestSequence.current += 1; requestController.current?.abort(); copySequence.current += 1; };
  }, [apiBase, catalogRevision]);

  const resource = catalog?.resources.find((item) => item.id === selectedId) || null;
  const filteredResources = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase('vi-VN');
    return (catalog?.resources || []).filter((item) => (group === 'all' || item.group === group)
      && (!needle || `${item.label} ${item.path} ${item.id}`.toLocaleLowerCase('vi-VN').includes(needle)));
  }, [catalog, search, group]);
  const preview = useMemo(() => {
    if (!resource) return null;
    try {
      const query = buildInspectorQuery(resource, values, { limit });
      return inspectorRequestAddress(resource, query, { apiBase, origin: globalThis.location?.origin });
    } catch { return null; }
  }, [resource, values, limit, apiBase]);
  const rows = inspectorRows(response);
  const columns = inspectorColumns(rows);
  const pagination = inspectorPagination(response);
  const success = response && response.statusCode >= 200 && response.statusCode < 300 && String(response.body.code) === '1';
  const resultJson = response ? JSON.stringify(response.body, null, 2) : '';
  const actualAddress = response && resource ? inspectorRequestAddress(resource, response.requestQuery, { apiBase, origin: globalThis.location?.origin }) : null;
  const sourceReadAt = inspectorHeader(response?.headers, 'X-Source-Read-At');

  function clearInspection() {
    requestSequence.current += 1; requestController.current?.abort(); copySequence.current += 1;
    setResponse(null); setBusy(false); setError(''); setCopyNotice('');
  }
  function selectResource(next) {
    clearInspection(); setSelectedId(next.id); setValues(initialInspectorValues(next)); setLimit(20); setTab('table');
  }
  function updateField(name, value) { clearInspection(); setValues((previous) => ({ ...previous, [name]: value })); }

  async function inspect(page = 1, snapshotId) {
    if (!resource) return;
    let query;
    try { query = buildInspectorQuery(resource, values, { page, limit, snapshotId }); }
    catch (failure) { setError(failure.message); setResponse(null); return; }
    requestController.current?.abort();
    const controller = new AbortController(); requestController.current = controller;
    const sequence = ++requestSequence.current;
    setBusy(true); setError(''); setResponse(null); setCopyNotice(''); copySequence.current += 1;
    try {
      const result = await apiRequest('/admin/corporate-api/inspect', {
        baseUrl: apiBase, method: 'POST', body: { resource: resource.id, query }, signal: controller.signal,
      });
      if (controller.signal.aborted || sequence !== requestSequence.current) return;
      const checked = validateInspection(result, resource, query);
      setResponse(checked);
      if (checked.statusCode < 200 || checked.statusCode >= 300 || String(checked.body.code) !== '1') setTab('json');
    } catch (failure) {
      if (controller.signal.aborted || sequence !== requestSequence.current) return;
      setError(['REQUEST_TIMEOUT', 'NETWORK_ERROR'].includes(failure.code)
        ? 'Chưa nhận được phản hồi kiểm tra API. Vui lòng thử lại.'
        : failure.message || 'Chưa kiểm tra được API. Vui lòng thử lại.');
    } finally { if (sequence === requestSequence.current) setBusy(false); }
  }

  async function copyText(text, label) {
    const sequence = ++copySequence.current;
    try {
      if (!globalThis.navigator?.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(text);
      if (sequence === copySequence.current) setCopyNotice(`Đã sao chép ${label}.`);
    } catch { if (sequence === copySequence.current) setCopyNotice('Chưa sao chép được. Bạn có thể chọn nội dung và sao chép thủ công.'); }
  }

  function tabKey(event) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 'table' : event.key === 'End' ? 'json' : tab === 'table' ? 'json' : 'table';
    setTab(next); document.getElementById(`${prefix}-${next}-tab`)?.focus();
  }

  return <section className="api-inspector" aria-labelledby={`${prefix}-title`}>
    <header className="api-inspector-heading"><div><h1 id={`${prefix}-title`}>Kiểm tra API</h1><p>Chọn API, nhập tham số và xem kết quả trả về.</p></div>
      <button type="button" className="button" disabled={catalogLoading} onClick={() => setCatalogRevision((value) => value + 1)}><RefreshCw size={16} aria-hidden="true" />Tải lại danh sách</button>
    </header>
    <div className="api-inspector-notice"><Database size={18} aria-hidden="true" /><p>Dùng phiên đăng nhập dashboard để đọc bản dữ liệu đã công bố. Phần này không kiểm tra tài khoản máy hoặc gửi dữ liệu đến Tổng công ty.{catalog?.enabled === false && <strong> API công khai chưa bật.</strong>}</p></div>
    {catalogLoading && <p className="api-inspector-empty" role="status">Đang tải danh sách API…</p>}
    {catalogError && <div className="api-inspector-error" role="alert"><AlertCircle size={18} aria-hidden="true" /><p>{catalogError}</p></div>}
    {catalog && <div className="api-inspector-layout">
      <aside className="api-inspector-catalog" aria-label="Danh sách API">
        <label htmlFor={`${prefix}-search`}>Tìm API</label><div className="api-inspector-search"><Search size={16} aria-hidden="true" /><input id={`${prefix}-search`} type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Tên hoặc đường dẫn…" /></div>
        <label htmlFor={`${prefix}-group`}>Nhóm API</label><select id={`${prefix}-group`} value={group} onChange={(event) => setGroup(event.target.value)}><option value="all">Tất cả nhóm</option>{Object.entries(INSPECTOR_GROUPS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
        <p className="api-inspector-catalog-count">{filteredResources.length}/{catalog.resources.length} API</p>
        <nav className="api-inspector-list" aria-label="Chọn API để kiểm tra">{filteredResources.map((item) => <button className="api-inspector-resource" type="button" key={item.id} aria-pressed={item.id === selectedId} onClick={() => selectResource(item)}>
          <span className="api-inspector-resource-title">{item.label}</span><code>{item.path}</code><small className={item.status === 'published' ? 'is-published' : ''}>{item.status === 'published' ? 'Có bản công bố' : 'Chưa công bố'}</small>
        </button>)}</nav>
        {!filteredResources.length && <p className="api-inspector-empty">Không có API phù hợp.</p>}
      </aside>
      <div className="api-inspector-workspace">
        {resource ? <>
          <section className="api-inspector-card" aria-labelledby={`${prefix}-resource-title`}>
            <div className="api-inspector-resource-heading"><div><p className="api-inspector-eyebrow">{INSPECTOR_GROUPS[resource.group]}</p><h2 id={`${prefix}-resource-title`}>{resource.label}</h2></div><span className={`api-inspector-badge ${resource.status === 'published' ? 'is-published' : ''}`}>{resource.status === 'published' ? 'Có bản công bố' : 'Chưa công bố'}</span></div>
            <div className="api-inspector-path"><strong>GET</strong><code>{resource.path}</code></div>
            <div className="api-inspector-source"><span>Đọc nguồn: {resource.sourceReadAt ? formatTimestamp(resource.sourceReadAt) : 'Chưa có thông tin'}</span><span>{resource.rowCount != null ? `${formatNumber(resource.rowCount, 0)} dòng trong bản công bố` : 'Chưa có số dòng công bố'}</span></div>
            <p className="api-inspector-help">Có bản công bố không đồng nghĩa dữ liệu còn mới. Kết quả kiểm tra bên dưới sẽ hiển thị trạng thái thực tế.</p>
            <form className="api-inspector-form" onSubmit={(event) => { event.preventDefault(); inspect(); }}>
              <label>Đơn vị<input value={catalog.companyId} readOnly /></label>
              {inspectorFields(resource).map((field) => <label key={field.name} htmlFor={`${prefix}-field-${field.name}`}>{field.label}{field.required && <span className="sr-only"> (bắt buộc)</span>}<input id={`${prefix}-field-${field.name}`} type={field.type} value={values[field.name] || ''} required={field.required} maxLength={field.type === 'text' ? 255 : undefined} onChange={(event) => updateField(field.name, event.target.value)} /></label>)}
              <label htmlFor={`${prefix}-limit`}>Dòng mỗi trang<select id={`${prefix}-limit`} value={limit} onChange={(event) => { clearInspection(); setLimit(Number(event.target.value)); }}>{INSPECTOR_LIMITS.map((value) => <option key={value} value={value}>{value} dòng</option>)}</select></label>
              <button className="button primary api-inspector-run" type="submit" disabled={busy} aria-busy={busy}><Play size={16} aria-hidden="true" />{busy ? 'Đang kiểm tra…' : 'Chạy kiểm tra'}</button>
            </form>
            {resource.group === 'operations' && <p className="api-inspector-help">Khoảng ngày lọc ngày tạo hoặc ngày sửa danh mục, không phải ngày sản lượng.</p>}
            {resource.id === 'customers' && <p className="api-inspector-help">Khoảng ngày lọc ngày tạo khách hàng, không phải ngày sản lượng.</p>}
            {Array.isArray(resource.coverage) && resource.coverage.some((range) => Array.isArray(range) && dateInput(range[0]) && dateInput(range[1])) && <details className="api-inspector-coverage"><summary>Các kỳ có dữ liệu công bố</summary><ul>{resource.coverage.filter((range) => Array.isArray(range) && dateInput(range[0]) && dateInput(range[1])).map((range, index) => <li key={`${range.join('-')}/${index}`}>{dateInput(range[0]).split('-').reverse().join('/')} – {dateInput(range[1]).split('-').reverse().join('/')}</li>)}</ul></details>}
            <div className="api-inspector-request"><div><span>Đường dẫn yêu cầu</span><button type="button" className="api-inspector-copy" disabled={!preview} onClick={() => copyText(preview, 'đường dẫn yêu cầu')}><Copy size={14} aria-hidden="true" />Sao chép URL</button></div><code tabIndex={0}>{preview || 'Nhập đủ tham số hợp lệ để xem đường dẫn.'}</code></div>
          </section>

          <section className="api-inspector-card api-inspector-result" aria-labelledby={`${prefix}-result-title`} aria-busy={busy}>
            <div className="api-inspector-result-heading"><h2 id={`${prefix}-result-title`}>Kết quả</h2>{response && <span className={`api-inspector-http ${success ? 'is-success' : 'is-error'}`}>{success ? <Check size={15} aria-hidden="true" /> : <AlertCircle size={15} aria-hidden="true" />}HTTP {response.statusCode}{Number.isFinite(response.durationMs) ? ` · ${formatNumber(response.durationMs, 0)} ms` : ''}</span>}</div>
            <div className="api-inspector-live" role="status" aria-live="polite">{busy ? 'Đang kiểm tra API…' : response ? success ? rows.length ? `Lấy thành công ${formatNumber(rows.length, 0)} dòng trên trang này.` : 'Yêu cầu thành công. Không có bản ghi phù hợp.' : `API trả HTTP ${response.statusCode}. ${response.body.message || 'Chưa lấy được dữ liệu.'}` : !error ? 'Chưa chạy kiểm tra.' : ''}</div>
            {error && <div className="api-inspector-error" role="alert"><AlertCircle size={18} aria-hidden="true" /><p>{error}</p></div>}
            {response && <>
              <div className="api-inspector-result-tools"><div className="api-inspector-tabs" role="tablist" aria-label="Kiểu hiển thị kết quả" onKeyDown={tabKey}>
                <button type="button" id={`${prefix}-table-tab`} role="tab" aria-selected={tab === 'table'} aria-controls={`${prefix}-table-panel`} tabIndex={tab === 'table' ? 0 : -1} onClick={() => setTab('table')}><Table2 size={15} aria-hidden="true" />Bảng</button>
                <button type="button" id={`${prefix}-json-tab`} role="tab" aria-selected={tab === 'json'} aria-controls={`${prefix}-json-panel`} tabIndex={tab === 'json' ? 0 : -1} onClick={() => setTab('json')}><Braces size={15} aria-hidden="true" />JSON</button>
              </div><button type="button" className="api-inspector-copy" onClick={() => copyText(resultJson, 'JSON')}><Copy size={14} aria-hidden="true" />Sao chép JSON</button></div>
              <div id={`${prefix}-table-panel`} role="tabpanel" aria-labelledby={`${prefix}-table-tab`} hidden={tab !== 'table'}>
                {rows.length && columns.length ? <div className="api-inspector-table-scroll" role="region" aria-label="Dữ liệu API trả về" tabIndex={0}><table><caption className="sr-only">{resource.label}: dữ liệu trên trang hiện tại</caption><thead><tr>{columns.map((column) => <th scope="col" key={column}>{column}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{columns.map((column) => <td key={column}>{inspectorCell(row?.[column])}</td>)}</tr>)}</tbody></table></div> : <p className="api-inspector-empty">{success ? rows.length ? 'Dữ liệu không có cấu trúc bảng. Hãy xem JSON.' : 'Không có bản ghi phù hợp với yêu cầu.' : response.body.message || 'API chưa trả dữ liệu để hiển thị.'}</p>}
              </div>
              <div id={`${prefix}-json-panel`} role="tabpanel" aria-labelledby={`${prefix}-json-tab`} hidden={tab !== 'json'}><pre className="api-inspector-json" tabIndex={0} aria-label="JSON trả về"><code>{resultJson}</code></pre></div>
              <div className="api-inspector-pagination"><span>{pagination.total !== null ? `${formatNumber(rows.length, 0)}/${formatNumber(pagination.total, 0)} dòng` : `${formatNumber(rows.length, 0)} dòng trên trang`}{pagination.valid && ` · Trang ${pagination.page}/${pagination.pages}`}</span><div><button type="button" className="button" disabled={busy || !pagination.valid || pagination.page <= 1} onClick={() => inspect(pagination.page - 1, pagination.snapshotId)}><ArrowLeft size={14} aria-hidden="true" />Trước</button><button type="button" className="button" disabled={busy || !pagination.hasNext} onClick={() => inspect(pagination.page + 1, pagination.snapshotId)}>Sau<ArrowRight size={14} aria-hidden="true" /></button></div></div>
              {success && !pagination.valid && <p className="api-inspector-help">Phản hồi chưa đủ thông tin phân trang hoặc phiên dữ liệu. Chạy lại từ trang đầu để tiếp tục kiểm tra.</p>}
              {sourceReadAt && <p className="api-inspector-help">Thời điểm đọc nguồn trong phản hồi: {formatTimestamp(sourceReadAt)}</p>}
              <details className="api-inspector-response-meta"><summary>Thông tin yêu cầu và phân trang</summary><code className="api-inspector-actual-url">GET {actualAddress}</code><dl>{INSPECTOR_HEADERS.map((header) => { const value = inspectorHeader(response.headers, header); return value === null ? null : <div key={header}><dt>{header}</dt><dd>{value}</dd></div>; })}</dl></details>
            </>}
          </section>
        </> : <div className="api-inspector-card api-inspector-empty">Chưa có API trong danh sách.</div>}
      </div>
    </div>}
    <p className="api-inspector-copy-notice" role="status" aria-live="polite">{copyNotice}</p>
  </section>;
}
