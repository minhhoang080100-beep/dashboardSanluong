import { Plus, Trash2 } from 'lucide-react';
import { milestoneBounds } from '../plan-milestones.js';
import './PlanMilestones.css';

export default function PlanMilestones({ form, setForm, disabled, error }) {
  const rows = form.milestones || [];
  const bounds = milestoneBounds(form);
  const eligible = form.metric === 'tonnage' && form.period_type !== 'voyage';
  const update = (next) => setForm({ ...form, milestones: next });
  return <fieldset className="plan-milestones management-wide" disabled={disabled}>
    <legend>Mốc tiến độ lũy kế (tùy chọn)</legend>
    <p>Nhập sản lượng cần đạt từ đầu kỳ đến từng ngày. Các mốc được duyệt cùng kế hoạch; không tự chia đều chỉ tiêu theo ngày.</p>
    {!eligible && <p>Chỉ dùng cho kế hoạch tấn theo kỳ.{rows.length > 0 && ' Hãy xóa các mốc trước khi đổi sang loại kế hoạch này.'}</p>}
    {rows.map((row, index) => <div className="plan-milestone-row" key={index}>
      <label>Ngày mốc {index + 1}<input type="date" min={bounds?.start_date || '2000-01-01'} max={bounds?.end_date || '2099-12-31'} value={row.date} onChange={(event) => update(rows.map((item, i) => i === index ? { ...item, date: event.target.value } : item))} required /></label>
      <label>Tấn lũy kế mốc {index + 1}<input type="text" inputMode="decimal" placeholder="Ví dụ: 50.000" value={row.amount} onChange={(event) => update(rows.map((item, i) => i === index ? { ...item, amount: event.target.value } : item))} required /></label>
      <button className="button" type="button" aria-label={`Xóa mốc ${index + 1}`} onClick={() => update(rows.filter((_, i) => i !== index))}><Trash2 size={15} aria-hidden="true" />Xóa mốc</button>
    </div>)}
    {error && <p className="plan-field-error" role="alert">{error}</p>}
    <button type="button" className="button" disabled={!eligible || rows.length >= 366} onClick={() => update([...rows, { date: '', amount: '' }])}><Plus size={15} aria-hidden="true" />Thêm mốc tiến độ</button>
  </fieldset>;
}
