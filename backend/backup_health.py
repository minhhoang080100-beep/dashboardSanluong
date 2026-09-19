"""Read non-secret backup receipts; never open or change the control database."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def receipt_path(state_path=None):
    source = Path(state_path or os.environ.get('DASHBOARD_STATE_PATH', Path(__file__).parent / '.data' / 'control.sqlite3'))
    return source.with_name('backup-health.json')


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timezone is required')
    return parsed.astimezone(timezone.utc)


def backup_health(state_path=None, *, now=None):
    now = now or datetime.now(timezone.utc)
    result = {'enabled': False, 'status': 'not_configured', 'last_attempt_at': None,
              'last_success_at': None, 'restore_verified': False, 'offsite_verified': False,
              'max_age_hours': 36, 'message': 'Chưa cấu hình sao lưu tự động.'}
    path = receipt_path(state_path)
    if not path.exists():
        return result
    try:
        if path.stat().st_size > 16384:
            raise ValueError('Invalid receipt')
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('schema') != 1 or type(data.get('enabled')) is not bool:
            raise ValueError('Invalid receipt')
        result['enabled'] = data['enabled']
        for field in ('last_attempt_at', 'last_success_at'):
            if data.get(field):
                stamp = _timestamp(data[field])
                if (stamp - now).total_seconds() > 300:
                    raise ValueError('Receipt is in the future')
                result[field] = stamp.isoformat()
        result['restore_verified'] = data.get('restore_verified') is True
        result['offsite_verified'] = data.get('offsite_verified') is True
        if data.get('last_error'):
            result.update(status='failed', message='Lần sao lưu gần nhất thất bại. Kiểm tra tác vụ sao lưu trên máy tính lưu bản sao.')
        elif not result['last_success_at']:
            result.update(status='never_run', message='Chưa có bản sao đã kiểm tra khôi phục.')
        elif not result['restore_verified'] or not result['offsite_verified']:
            result.update(status='failed', message='Chưa xác minh bản sao độc lập và khả năng khôi phục.')
        elif (now - _timestamp(result['last_success_at'])).total_seconds() > result['max_age_hours'] * 3600:
            result.update(status='stale', message='Đã quá 36 giờ chưa có bản sao được xác minh. Kiểm tra máy tính và lịch sao lưu.')
        else:
            result.update(status='ok', message='Bản sao trên máy tính đã được kiểm tra khôi phục.')
        return result
    except (OSError, ValueError, TypeError, AttributeError):
        result.update(status='failed', message='Không đọc được trạng thái sao lưu. Kiểm tra tác vụ sao lưu.')
        return result
