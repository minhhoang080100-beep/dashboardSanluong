const terminals = new Set(['cua_lo', 'ben_thuy']);
const number = (value) => typeof value === 'number' && Number.isFinite(value);
const nullableNumber = (value) => value === null || number(value);
const dateText = (value) => value === null || typeof value === 'string';
const fail = () => { throw new Error('Dữ liệu chuyến tàu chưa đúng cấu trúc. Vui lòng thử lại.'); };

export function validateVoyage(row) {
  if (!row || !terminals.has(row.terminal_id) || typeof row.voyage_id !== 'string' || !row.voyage_id) fail();
  if (typeof row.terminal_name !== 'string') fail();
  for (const key of ['vessel_name', 'voyage_code']) if (row[key] !== null && typeof row[key] !== 'string') fail();
  for (const key of ['arrival_at', 'departure_at', 'first_operation_date', 'last_operation_date']) if (!dateText(row[key])) fail();
  for (const key of ['tonnage', 'teu']) if (!nullableNumber(row[key])) fail();
  if (!Number.isInteger(row.record_count) || row.record_count < 0) fail();
  if (!Array.isArray(row.cargo_names) || row.cargo_names.some((name) => typeof name !== 'string')) fail();
  return row;
}

export function validateVoyageList(rows, count, filters) {
  if (!Array.isArray(rows) || rows.length !== count) fail();
  const keys = new Set();
  for (const row of rows) {
    validateVoyage(row);
    if (filters.terminal !== 'all' && row.terminal_id !== filters.terminal) fail();
    const key = `${row.terminal_id}/${row.voyage_id}`;
    if (keys.has(key)) fail();
    keys.add(key);
  }
  return rows;
}

export function voyageListError(rows, count, filters) {
  try {
    validateVoyageList(rows, count, filters);
    return '';
  } catch {
    return 'Danh sách chuyến tàu chưa đầy đủ hoặc chưa khớp với chỉ tiêu tổng quan. Vui lòng tải lại báo cáo.';
  }
}

export function filterVoyages(rows, query) {
  const normalize = (value) => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[đĐ]/g, 'd').toLocaleLowerCase('vi-VN');
  const tokens = normalize(query).trim().split(/\s+/).filter(Boolean);
  return rows.filter((row) => {
    const searchable = normalize(`${row.vessel_name || ''} ${row.voyage_code || ''} ${row.voyage_id}`);
    return tokens.every((token) => searchable.includes(token));
  });
}

export function paginateVoyages(rows, page, pageSize = 10) {
  const totalPages = Math.ceil(rows.length / pageSize);
  const selectedPage = Math.max(1, Math.min(Number.isInteger(page) ? page : 1, totalPages || 1));
  return { items: rows.slice((selectedPage - 1) * pageSize, selectedPage * pageSize), page: selectedPage, total: rows.length, totalPages };
}

export function validateVoyageDetail(data, selected, filters, page, pageSize) {
  if (!data || !data.header || !data.summary || !data.operations || !data.meta) fail();
  validateVoyage(data.header);
  if (data.header.terminal_id !== selected.terminal_id || data.header.voyage_id !== selected.voyage_id) fail();
  const expected = { start_date: filters.start_date, end_date: filters.end_date, terminal: selected.terminal_id };
  for (const [key, value] of Object.entries(expected)) if (data.meta.filters?.[key] !== value) fail();
  for (const key of ['tonnage', 'teu']) if (!nullableNumber(data.summary[key])) fail();
  if (!Number.isInteger(data.summary.record_count) || data.summary.record_count < 0) fail();
  for (const [key, label] of [['cargo', 'name'], ['daily', 'date']]) {
    if (!Array.isArray(data[key]) || data[key].some((row) => !row || typeof row[label] !== 'string' || !nullableNumber(row.tonnage) || !nullableNumber(row.teu))) fail();
  }
  if (!Array.isArray(data.native_units) || data.native_units.some((row) => !row || typeof row.unit_name !== 'string' || typeof row.unit_code !== 'string' || !nullableNumber(row.value))) fail();
  const operations = data.operations;
  if (operations.page !== page || operations.page_size !== pageSize || !Number.isInteger(operations.total) || operations.total < 0 || !Number.isInteger(operations.total_pages) || operations.total_pages < 0 || !Array.isArray(operations.rows) || operations.rows.length > pageSize || operations.total !== data.summary.record_count) fail();
  if (operations.total_pages !== Math.ceil(operations.total / pageSize) || operations.rows.length !== Math.min(pageSize, Math.max(0, operations.total - (page - 1) * pageSize))) fail();
  for (const row of operations.rows) {
    if (!row || !['string', 'number'].includes(typeof row.id) || !dateText(row.operation_date)) fail();
    for (const key of ['job_method', 'cargo_name', 'direction', 'quantity_unit', 'weight_unit']) if (row[key] !== null && typeof row[key] !== 'string') fail();
    for (const key of ['quantity', 'weight', 'tonnage', 'teu']) if (!nullableNumber(row[key])) fail();
    for (const key of ['operation_code', 'quantity_unit_name', 'weight_unit_name']) if (row[key] != null && typeof row[key] !== 'string') fail();
  }
  return data;
}

export async function fetchVoyageDetail(selected, filters, page, { signal, fetcher = fetch, baseUrl = '/api', pageSize = 25 } = {}) {
  const query = new URLSearchParams({ start_date: filters.start_date, end_date: filters.end_date, page: String(page), page_size: String(pageSize) });
  const url = `${baseUrl.replace(/\/+$/, '')}/voyages/${encodeURIComponent(selected.terminal_id)}/${encodeURIComponent(selected.voyage_id)}?${query}`;
  const response = await fetcher(url, { signal, headers: { Accept: 'application/json' } });
  if (!response.ok) {
    if (response.status === 404) throw new Error('Không tìm thấy chuyến tàu có tác nghiệp trong kỳ đã chọn. Hãy tải lại danh sách.');
    if (response.status === 422) throw new Error('Trang phiếu hoặc kỳ báo cáo không còn hợp lệ. Hãy mở lại chuyến tàu.');
    if (response.status === 503) throw new Error('Tạm thời không truy vấn được chi tiết chuyến tàu. Vui lòng thử lại.');
    throw new Error(`Không tải được chi tiết chuyến tàu (HTTP ${response.status}).`);
  }
  return validateVoyageDetail(await response.json(), selected, filters, page, pageSize);
}
