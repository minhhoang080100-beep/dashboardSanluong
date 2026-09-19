import { formatTimestamp } from '../dashboard-data.js';

export default function BackupStatus({ value }) {
  if (!value) return null;
  const verified = value.status === 'ok' && value.restore_verified === true && value.offsite_verified === true;
  return <section className="management-comparison" aria-label="Sao lưu dữ liệu">
    <h4>Sao lưu dữ liệu</h4>
    <p className={verified ? '' : 'plan-field-error'}>{value.message || 'Chưa xác minh trạng thái sao lưu.'}</p>
    <dl className="management-config">
      <div><dt>Lần sao lưu thành công</dt><dd>{value.last_success_at ? formatTimestamp(value.last_success_at) : 'Chưa ghi nhận'}</dd></div>
      <div><dt>Kiểm tra khôi phục</dt><dd>{value.restore_verified ? 'Đã kiểm tra' : 'Chưa xác minh'}</dd></div>
      <div><dt>Bản sao ngoài máy chủ</dt><dd>{value.offsite_verified ? 'Đã xác minh' : 'Chưa xác minh'}</dd></div>
    </dl>
    <small>Lịch trên máy tính chỉ chạy khi máy bật, có mạng và đã đăng nhập Windows. Kiểm tra cảnh báo nếu quá {value.max_age_hours || 36} giờ chưa có bản sao mới.</small>
  </section>;
}
