"""Fresh SmartTOS reads on every request, with no retained result or page cache.

The source adapters and publication validators remain the authority for mapping,
company completeness and data quality. A transient store is only an implementation
detail for validation, filtering and paging within one request; it is closed
before responding. No export file, result cache or background preload is needed.
"""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import threading

from .errors import CorporateError
from .manage_exports import extract, json_default, publish_preview, references_for
from .reconciliation import unaccepted_nulls
from .registry import IDENTITY, MODELS, PRODUCTION
from .sql import source_read_budget


MAX_PROFILE_BYTES = 2 * 1024 * 1024
TZ = timezone(timedelta(hours=7))

# Ordered, fixed diagnostics only. Do not expose adapter messages, row values or
# arbitrary codes; connection failures retain priority in _dataset_error.
PRODUCTION_BLOCKER_MESSAGES = {
    'DATE_BASIS_UNCONFIRMED': 'Chưa xác nhận cơ sở ngày dùng để tính sản lượng API.',
    'PRODUCTION_SCOPE_UNCONFIRMED': 'Chưa xác nhận phạm vi sản lượng áp dụng cho API.',
    'QUAY_METHODS_UNCONFIRMED': 'Chưa cấu hình phương án tác nghiệp thuộc sản lượng qua cầu.',
    'GATE_METHODS_UNCONFIRMED': 'Chưa cấu hình phương án tác nghiệp thuộc sản lượng qua cổng/bãi.',
    'CARGO_KIND_UNMAPPED': 'Còn mặt hàng chưa được phân loại để tính sản lượng API.',
    'CARGO_TYPE_UNMAPPED': 'Còn mặt hàng chưa có ánh xạ loại hàng hợp lệ cho API.',
    'CONTAINER_SIZE_RELATION_UNCONFIRMED': 'Chưa xác minh quan hệ giữa mặt hàng và mã kích cỡ container.',
    'CONTAINER_SIZE_UNMAPPED': 'Còn mặt hàng container chưa có kích cỡ hoặc hệ số TEU hợp lệ.',
    'CONTAINER_QUANTITY_UNIT_UNCONFIRMED': 'Còn phiếu container chưa xác minh được đơn vị số lượng.',
    'WEIGHT_OR_UNIT_UNAVAILABLE': 'Còn phiếu thiếu khối lượng hoặc đơn vị quy đổi sang tấn hợp lệ.',
    'CONTAINER_QUANTITY_UNAVAILABLE': 'Còn phiếu container thiếu số lượng hợp lệ để tính TEU.',
    'WEIGHT_OUT_OF_RANGE': 'Khối lượng của phiếu vượt giới hạn số liệu cho phép của API.',
    'AGGREGATE_OUT_OF_RANGE': 'Tổng sản lượng vượt giới hạn số liệu cho phép của API.',
}


class LiveReader:
    """Store-compatible read interface; callers enforce account grants first.

    Every request, including repeated requests and subsequent pages, reads the
    source again. Page totals and rows may change between requests when SmartTOS
    changes. No snapshot token or source result survives the current request.
    """

    def __init__(self, *, extract_fn=None, store_factory=None, profile=None,
                 max_total_bytes=32 * 1024 * 1024,
                 max_concurrent=2, wait_seconds=0.2):
        if (not 1024 <= max_total_bytes <= 256 * 1024 * 1024
                or not 1 <= max_concurrent <= 8 or not 0 <= wait_seconds <= 5):
            raise ValueError('Invalid live-reader limits')
        self._extract = extract_fn or extract
        if store_factory is None:
            from .memory_store import MemoryExportStore
            store_factory = MemoryExportStore
        self._store_factory = store_factory
        self._configured_profile = deepcopy(profile)
        self._max_bytes = max_total_bytes
        self._wait = wait_seconds
        self._slots = threading.BoundedSemaphore(max_concurrent)

    def _profile(self):
        if self._configured_profile is not None:
            profile = deepcopy(self._configured_profile)
        else:
            filename = os.environ.get('CORPORATE_SOURCE_PROFILE') or os.environ.get('CORPORATE_SYNC_PROFILE')
            if not filename:
                raise CorporateError(503, 'SOURCE_PROFILE_REQUIRED',
                                     'Chưa cấu hình ánh xạ nguồn SmartTOS cho API trực tiếp.')
            try:
                # Bounded read, including files that change between stat/read.
                with Path(filename).open('rb') as handle:
                    raw = handle.read(MAX_PROFILE_BYTES + 1)
                if len(raw) > MAX_PROFILE_BYTES:
                    raise ValueError('Oversized profile')
                profile = json.loads(raw.decode('utf-8-sig'))
            except (OSError, ValueError, UnicodeError):
                raise CorporateError(503, 'SOURCE_PROFILE_INVALID',
                                     'Không đọc được cấu hình ánh xạ nguồn SmartTOS hợp lệ.') from None
        if not isinstance(profile, dict) or profile.get('company_id', 'CNT') != 'CNT':
            raise CorporateError(503, 'SOURCE_PROFILE_INVALID', 'Cấu hình ánh xạ nguồn SmartTOS không hợp lệ.')
        if profile.get('approved') is not True:
            raise CorporateError(503, 'SOURCE_PROFILE_UNAPPROVED',
                                 'Ánh xạ dữ liệu SmartTOS chưa được đối chiếu và duyệt.')
        # Never serve a company-wide response from a configured subset of sources.
        terminals = profile.get('terminals', [])
        if (not isinstance(terminals, list) or any(not isinstance(value, str) for value in terminals)
                or sorted(terminals) != ['ben_thuy', 'cua_lo']):
            raise CorporateError(503, 'SOURCE_TERMINALS_INCOMPLETE',
                                 'Cần cấu hình đầy đủ nguồn Cửa Lò và Bến Thủy cho CNT.')
        return profile

    def close(self):
        """Compatibility hook: requests release their own stores before returning."""

    @staticmethod
    def _check_production_profile(resource, profile):
        scope = profile.get('production_scope')
        gate_selection = profile.get('gate_selection', 'methods')
        valid = (profile.get('date_basis') == 'shiftDate'
                 and isinstance(scope, str) and scope in {'nghe_tinh', 'vietsun', 'all_activity'}
                 and isinstance(gate_selection, str) and gate_selection in {'methods', 'vessel_type'})
        methods_key = ('quay_method_ids' if resource in {'contQuayVolumesCB', 'bulkQuayVolumesCB'}
                       else 'gate_method_ids' if gate_selection == 'methods' else None)
        if methods_key is not None:
            methods = profile.get(methods_key)
            valid = valid and isinstance(methods, dict) and all(
                isinstance(methods.get(terminal), list) and bool(methods[terminal])
                for terminal in ('cua_lo', 'ben_thuy'))
        if not valid:
            raise CorporateError(503, 'SOURCE_MAPPING_REQUIRED',
                                 'Chưa cấu hình đầy đủ cơ sở ngày, phạm vi sản lượng và cách chọn tác nghiệp cho API sản lượng.')

    @staticmethod
    def _busy():
        return CorporateError(503, 'SOURCE_BUSY',
                              'Nguồn SmartTOS đang xử lý yêu cầu khác. Vui lòng thử lại sau.', retry_after=2)

    def _check_size(self, preview):
        size = len(json.dumps(preview, ensure_ascii=False, default=json_default,
                              separators=(',', ':')).encode('utf-8'))
        # Leave room for SQLite indexes and the public JSON copy during paging.
        if size * 2 > self._max_bytes:
            raise CorporateError(422, 'SOURCE_RESULT_TOO_LARGE',
                                 'Kết quả nguồn vượt giới hạn bộ nhớ. Hãy chia nhỏ kỳ truy vấn.')
        return size

    @staticmethod
    def _dataset_error(dataset):
        # Adapters use strings for production blockers and code/message objects
        # for catalog blockers. Only fixed known codes select public messages;
        # source messages, identifiers and arbitrary exception text stay private.
        blockers = dataset.get('blockers', [])
        codes = set()
        if isinstance(blockers, list):
            for blocker in blockers:
                code = blocker.get('code') if isinstance(blocker, dict) else blocker
                if isinstance(code, str):
                    codes.add(code)
        if 'SOURCE_TIMEOUT' in codes:
            return CorporateError(503, 'SOURCE_TIMEOUT',
                                  'Truy vấn SmartTOS quá thời gian. Hãy chọn kỳ ngắn hơn hoặc thử lại.', retry_after=5)
        if 'SOURCE_UNAVAILABLE' in codes:
            return CorporateError(503, 'SOURCE_UNAVAILABLE',
                                  'Không kết nối được SmartTOS. Vui lòng thử lại hoặc kiểm tra kết nối máy chủ.', retry_after=5)
        if 'SOURCE_ID_CONFLICT' in codes:
            return CorporateError(503, 'SOURCE_ID_CONFLICT',
                                  'ID nguồn trùng nhưng khác nội dung giữa Cửa Lò và Bến Thủy. Cần thống nhất cách phân biệt mã trước khi trả dữ liệu.')
        for code, message in PRODUCTION_BLOCKER_MESSAGES.items():
            if code in codes:
                return CorporateError(503, code, message)
        return CorporateError(503, 'SOURCE_DATA_NOT_READY',
                              'Dữ liệu SmartTOS chưa đáp ứng ánh xạ hoặc kiểm tra chất lượng của API.')

    def _build(self, resource, query, profile):
        today = datetime.now(TZ).date()
        start = datetime.strptime(query.startDate, '%Y%m%d').date() if query.startDate else today
        end = datetime.strptime(query.endDate, '%Y%m%d').date() if query.endDate else today
        pending, selected = {resource}, set()
        preview = None
        # Dependencies are discovered only from populated fields in extracted
        # rows. A null optional FK never makes an unrelated catalog mandatory.
        while pending:
            names = sorted(pending - selected)
            if not names:
                break
            part = self._extract(profile, start, end, names)
            if (not isinstance(part, dict) or not isinstance(part.get('datasets'), dict)
                    or set(part['datasets']) != set(names)):
                raise ValueError('Invalid source adapter result')
            if preview is None:
                preview = part
            else:
                preview['datasets'].update(part['datasets'])
            self._check_size(preview)
            selected.update(names)
            pending = set()
            for name in names:
                dataset = preview['datasets'][name]
                if dataset.get('ready') is not True or dataset.get('blockers'):
                    raise self._dataset_error(dataset)
                if name in PRODUCTION and unaccepted_nulls(name, dataset['rows'], profile):
                    raise CorporateError(503, 'SOURCE_NULL_POLICY_REQUIRED',
                                         'Dữ liệu sản lượng còn trường thiếu nguồn; cần bổ sung nguồn hoặc xác nhận các trường được phép để trống trước khi trả API.')
                for row in dataset['rows']:
                    for field, (target, _) in references_for(name).items():
                        if field == IDENTITY.get(name) or field not in row or row[field] is None:
                            continue
                        if isinstance(row[field], list) and not row[field]:
                            continue
                        if target not in selected:
                            pending.add(target)
        store = self._store_factory()
        try:
            versions = publish_preview(preview, profile, store, sorted(selected), replace_all=True)
            with store.db() as db:
                size = (db.execute('PRAGMA page_count').fetchone()[0]
                        * db.execute('PRAGMA page_size').fetchone()[0])
            size = max(size, self._check_size(preview) * 2)
            if size > self._max_bytes:
                raise CorporateError(422, 'SOURCE_RESULT_TOO_LARGE',
                                     'Kết quả nguồn vượt giới hạn bộ nhớ. Hãy chia nhỏ kỳ truy vấn.')
            return store, versions[resource]['snapshotId']
        except BaseException:
            store.close()
            raise

    def read(self, resource, query, *, identity=None, max_age_seconds=None):
        if resource not in MODELS:
            raise CorporateError(422, 'UNSUPPORTED_RESOURCE', 'API không hợp lệ.')
        query.check_resource(resource)
        if query.companyId != 'CNT':
            raise CorporateError(403, 'FORBIDDEN', 'Tài khoản không có quyền truy cập phạm vi này.')
        if resource in PRODUCTION:
            start = datetime.strptime(query.startDate, '%Y%m%d').date()
            end = datetime.strptime(query.endDate, '%Y%m%d').date()
            if (end - start).days >= 31:
                raise CorporateError(422, 'SOURCE_RANGE_TOO_LARGE',
                                     'API trực tiếp cho phép tối đa 31 ngày mỗi yêu cầu. Hãy truy vấn lần lượt từng tháng.')
        if query.snapshotId is not None:
            raise CorporateError(422, 'SNAPSHOT_NOT_SUPPORTED',
                                 'API trực tiếp luôn đọc lại SmartTOS và không nhận snapshotId. Hãy bỏ tham số này.')
        profile = self._profile()
        if resource in PRODUCTION:
            self._check_production_profile(resource, profile)
        store = None
        acquired = False
        try:
            acquired = self._slots.acquire(timeout=self._wait)
            if not acquired:
                raise self._busy()
            with source_read_budget(35):
                store, internal_id = self._build(resource, query, profile)
            # Reuse the tested filter/page implementation only for this request.
            # Its internal identifier must never reach a caller or be retained.
            result = store.read(resource, query.model_copy(update={'snapshotId': internal_id}),
                                identity=identity, max_age_seconds=max_age_seconds)
            result['pagination'].pop('snapshotId', None)
            return result
        except CorporateError:
            raise
        except Exception:
            # Mapping and driver exceptions can contain private source values.
            raise CorporateError(503, 'SOURCE_DATA_NOT_READY',
                                 'Không thể trả dữ liệu SmartTOS hợp lệ. Cần kiểm tra kết nối, ánh xạ và dữ liệu nguồn.') from None
        finally:
            if store is not None:
                store.close()
            if acquired:
                self._slots.release()
