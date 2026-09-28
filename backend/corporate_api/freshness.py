"""Track source read times per reporting period, independent of publication time."""
from datetime import datetime, timedelta, timezone
import json
import os

from .errors import CorporateError


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Source timestamp must include timezone')
    return parsed


def max_age_seconds():
    try:
        value = int(os.environ.get('CORPORATE_MAX_SOURCE_AGE_SECONDS', '86400'))
        if not 0 <= value <= 31 * 86400:
            raise ValueError()
        return value
    except ValueError:
        raise CorporateError(503, 'FRESHNESS_CONFIG_INVALID', 'Cấu hình độ mới dữ liệu API không hợp lệ.') from None


def windows(version):
    version = dict(version)
    if version.get('source_windows'):
        return json.loads(version['source_windows'])
    return [[start, end, version['read_at']] for start, end in json.loads(version['coverage'])]


def replace_window(version, start, end, read_at):
    before = (datetime.strptime(start, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
    after = (datetime.strptime(end, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
    result = [[start, end, read_at]]
    for a, b, stamp in windows(version) if version else []:
        if b < start or a > end:
            result.append([a, b, stamp])
        else:
            if a < start:
                result.append([a, before, stamp])
            if b > end:
                result.append([after, b, stamp])
    return sorted(result)


def read_time(version, start=None, end=None):
    if start is None:
        return version['read_at']
    stamps = [stamp for a, b, stamp in windows(version) if a <= end and b >= start]
    return min(stamps, key=timestamp) if stamps else version['read_at']


def ensure_fresh(read_at, max_age, *, now=None):
    if max_age is None or max_age == 0:
        return
    age = ((now or datetime.now(timezone.utc)) - timestamp(read_at)).total_seconds()
    if age < -300:
        raise CorporateError(503, 'SOURCE_TIME_INVALID', 'Thời điểm đọc nguồn dữ liệu không hợp lệ.', retry_after=300)
    if age > max_age:
        raise CorporateError(503, 'DATASET_STALE', 'Dữ liệu API quá thời hạn cập nhật. Vui lòng thử lại sau.', retry_after=300)
