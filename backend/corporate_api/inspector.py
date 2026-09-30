"""Dashboard-admin inspection through the configured published or live API reader.

This is an internal reader, not a test of public API authentication or reachability.
It deliberately shares the public reader's validation, freshness and snapshot rules.
"""
import json
import os
import sqlite3
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

try:
    from ..control_api import require_user
    from ..control_store import require_admin, require_scope
except ImportError:  # Backend also supports its existing standalone main import.
    from control_api import require_user
    from control_store import require_admin, require_scope

from .contracts import PRODUCTION, Query
from .errors import CorporateError
from .freshness import max_age_seconds
from .operation_contracts import OPERATION_MODELS, OPERATION_PATHS, OperationQuery
from .registry import MODELS
from .router import CorporateRoute, _headers, default_exports, default_live, read_mode, error_response, public_result


COMPANY_ID = 'CNT'
LABELS = {
    'contQuayVolumesCB': 'Container qua cầu',
    'contGateVolumesCB': 'Container qua cổng / bãi',
    'bulkQuayVolumesCB': 'Hàng rời qua cầu',
    'bulkGateVolumesCB': 'Hàng rời qua cổng / bãi',
    'shipDetails': 'Tàu',
    'customers': 'Khách hàng',
    'cargoType': 'Loại hàng',
    'cargoCategory': 'Nhóm hàng',
    'handlingMethodList': 'Phương án tác nghiệp',
    'class': 'Hướng hàng',
    'origins': 'Nguồn gốc hàng',
    'containerSize': 'Kích cỡ container',
    'oprt.portEquipment': 'Thiết bị cảng (Bulk)',
    'oprt.portEquipType': 'Loại thiết bị cảng',
    'oprt.portWHYard': 'Kho / bãi (Bulk)',
    'oprt.portWHYardType': 'Loại kho / bãi',
    'oprt.berths': 'Cầu tàu',
    'oprt.jobType': 'Loại tác nghiệp',
    'oprt.jobMethod': 'Phương án tác nghiệp vận hành',
    'oprt.deliveryMethod': 'Phương thức giao nhận',
    'oprt.serviceType': 'Loại dịch vụ',
    'oprt.cargoItems': 'Mặt hàng',
    'oprt.cargoGroups': 'Nhóm mặt hàng',
    'oprt.unitMeasurement': 'Đơn vị tính',
    'oprt.cargoDirect': 'Hướng hàng vận hành',
    'oprt.operationLocationType': 'Loại vị trí tác nghiệp',
    'oprt.portOpTeam': 'Tổ / đội tác nghiệp',
    'oprt.portOpStaff': 'Nhân sự tác nghiệp',
    'oprt.vesselType': 'Loại phương tiện',
    'oprt.equipments': 'Thiết bị container',
    'oprt.contwhYards': 'Kho / bãi container',
    'oprt.contSizeType': 'Kích cỡ / loại container',
}
TEXT_FILTERS = {
    'contQuayVolumesCB': [('shipId', 'Mã tàu'), ('handlingMethodId', 'Mã phương án')],
    'contGateVolumesCB': [('handlingMethodId', 'Mã phương án')],
    'bulkQuayVolumesCB': [('shipId', 'Mã tàu'), ('handlingMethodId', 'Mã phương án')],
    'bulkGateVolumesCB': [('handlingMethodId', 'Mã phương án')],
    'customers': [('customerTaxCode', 'Mã số thuế'), ('customerType', 'Loại khách hàng')],
    'cargoCategory': [('cargoTypeId', 'Mã loại hàng')],
    'containerSize': [('containerSizeId', 'Mã kích cỡ container')],
}
HEADER_NAMES = (
    'Cache-Control', 'X-Content-Type-Options', 'X-Snapshot-Id', 'X-Source-Read-At',
    'X-Corporate-Contract', 'X-Page', 'X-Limit', 'X-Total-Count', 'X-Total-Pages',
    'X-Has-Next', 'X-Error-Code', 'Retry-After',
)


def require_inspector(user: dict = Depends(require_user)):
    require_admin(user)
    require_scope(user, 'all')
    return user


def _exports(request):
    # Called only inside an endpoint after all dashboard permission checks.
    configured = getattr(request.app.state, 'corporate_exports', None)
    if configured is not None:
        return configured
    return default_live() if read_mode() == 'live' else default_exports()


def _path(resource):
    return OPERATION_PATHS[resource] if resource in OPERATION_MODELS else '/api/' + resource


def _filters(resource):
    result = []
    if resource in PRODUCTION or resource in OPERATION_MODELS or resource == 'customers':
        result.extend({'name': name, 'label': label, 'type': 'date', 'required': resource != 'customers'}
                      for name, label in [('startDate', 'Từ ngày'), ('endDate', 'Đến ngày')])
    result.extend({'name': name, 'label': label, 'type': 'text', 'required': False}
                  for name, label in TEXT_FILTERS.get(resource, []))
    return result


router = APIRouter(prefix='/api/admin/corporate-api', tags=['Kiểm tra API'], route_class=CorporateRoute)


@router.get('/catalog')
def catalog(request: Request, user=Depends(require_inspector)):
    live = read_mode() == 'live'
    published = {}
    if not live:
        exports = _exports(request)
        # Count indexed rows only; never fetch export payloads or private warnings.
        placeholders = ','.join('?' for _ in MODELS)
        with exports.db() as db:
            published = {row['resource']: dict(row) for row in db.execute(f'''
                SELECT v.resource,v.snapshot_id,v.read_at,v.coverage,COUNT(r.seq) AS row_count
                FROM export_versions v LEFT JOIN export_rows r ON v.snapshot_id=r.snapshot_id
                WHERE v.current=1 AND v.company_id=? AND v.resource IN ({placeholders})
                GROUP BY v.snapshot_id''', [COMPANY_ID, *MODELS])}
    resources = []
    for resource in MODELS:
        version = published.get(resource)
        resources.append({
            'id': resource, 'label': LABELS[resource],
            'group': 'production' if resource in PRODUCTION else 'operations' if resource in OPERATION_MODELS else 'catalog_s',
            'path': _path(resource), 'method': 'GET', 'filters': _filters(resource),
            'status': 'live' if live else 'published' if version else 'not_published',
            'rowCount': version['row_count'] if version else None,
            'sourceReadAt': version['read_at'] if version else None,
            'coverage': json.loads(version['coverage']) if version else [],
            'snapshotId': version['snapshot_id'] if version else None,
        })
    return {'enabled': os.environ.get('CORPORATE_API_ENABLED', '').strip().lower() in {'1', 'true'},
            'companyId': COMPANY_ID, 'resources': resources, 'mode': 'internal',
            **({'readMode': 'live'} if live else {})}


class InspectInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resource: str = Field(min_length=1, max_length=80)
    query: dict[str, Any] = Field(default_factory=dict, max_length=20)


def _invalid_query():
    return error_response(422, 'INVALID_REQUEST',
                          'Tham số không hợp lệ. Kiểm tra định dạng và các trường bắt buộc trong tài liệu API.')


@router.post('/inspect')
def inspect(body: InspectInput, request: Request, user=Depends(require_inspector)):
    started = perf_counter()
    request_query = {'companyId': COMPANY_ID}
    path = _path(body.resource) if body.resource in MODELS else None
    try:
        if body.resource not in MODELS:
            raise CorporateError(422, 'UNSUPPORTED_RESOURCE', 'API không thuộc danh sách được hỗ trợ.')
        if body.query.get('companyId', COMPANY_ID) != COMPANY_ID:
            raise CorporateError(403, 'COMPANY_FORBIDDEN', 'Chỉ hỗ trợ dữ liệu Cảng Nghệ Tĩnh (CNT).')
        query_model = OperationQuery if body.resource in OPERATION_MODELS else Query
        query = query_model.model_validate({'companyId': COMPANY_ID, **body.query})
        # Never echo unknown/raw inputs, including accidentally supplied credentials.
        request_query = query.model_dump(mode='json', exclude_none=True)
        query.check_resource(body.resource)
        result = _exports(request).read(body.resource, query, max_age_seconds=max_age_seconds())
        response = Response()
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        _headers(response, result, 'OPRT-v1.5-CNT-1' if body.resource in OPERATION_MODELS else 'S-v3-CNT-1')
        status, payload = 200, public_result(result)
    except ValidationError:
        response = _invalid_query()
        status, payload = response.status_code, json.loads(response.body)
    except CorporateError as exc:
        response = error_response(exc.status, exc.code, exc.message, exc.retry_after)
        status, payload = response.status_code, json.loads(response.body)
    except sqlite3.Error:
        response = error_response(503, 'STORAGE_UNAVAILABLE', 'Kho dữ liệu API tạm thời không truy cập được.', 30)
        status, payload = response.status_code, json.loads(response.body)
    return {'resource': body.resource, 'path': path, 'method': 'GET', 'requestQuery': request_query,
            'statusCode': status, 'durationMs': round((perf_counter() - started) * 1000, 2),
            'headers': {name: response.headers[name] for name in HEADER_NAMES if name in response.headers},
            'body': payload}
