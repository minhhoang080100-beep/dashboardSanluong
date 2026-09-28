"""Corporate S and B/C catalog endpoints with machine auth and safe errors."""
from functools import lru_cache
import logging
import os
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query as QueryParam, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .auth import MachineAuthError, MachineStore, require_access
from .contracts import IDENTITY, MODELS, Page, Query
from .operation_contracts import OPERATION_MODELS, OPERATION_PATHS, OperationQuery
from .errors import CorporateError
from .store import ExportStore
from .freshness import max_age_seconds

logger = logging.getLogger(__name__)


def error_response(status, code, message, retry_after=None):
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
    headers['X-Error-Code'] = code
    if retry_after is not None:
        headers['Retry-After'] = str(retry_after)
    if status == 401:
        headers['WWW-Authenticate'] = 'Bearer'
    return JSONResponse(status_code=status, headers=headers,
                        content={'data': [], 'code': '0', 'message': message})


class CorporateRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                result = await original(request)
                result.headers['Cache-Control'] = 'no-store'
                result.headers['X-Content-Type-Options'] = 'nosniff'
                return result
            except MachineAuthError as exc:
                return error_response(exc.status_code, exc.code, exc.message, exc.retry_after)
            except CorporateError as exc:
                return error_response(exc.status, exc.code, exc.message, exc.retry_after)
            except RequestValidationError:
                # FastAPI's default validation includes raw input. Never echo a password.
                return error_response(422, 'INVALID_REQUEST', 'Tham số không hợp lệ. Kiểm tra định dạng và các trường bắt buộc trong tài liệu API.')
            except sqlite3.Error:
                logger.error('Corporate API state storage unavailable')
                return error_response(503, 'STORAGE_UNAVAILABLE', 'Kho dữ liệu API tạm thời không truy cập được.', 30)
        return handler


router = APIRouter(prefix='/api', tags=['Tổng công ty'], route_class=CorporateRoute)


def require_enabled():
    if os.environ.get('CORPORATE_API_ENABLED', '').strip().lower() not in {'1', 'true'}:
        raise CorporateError(503, 'CORPORATE_API_DISABLED', 'API Tổng công ty chưa được kích hoạt.')


@lru_cache(maxsize=1)
def default_auth():
    return MachineStore()


@lru_cache(maxsize=1)
def default_exports():
    return ExportStore()


def get_machine_store(request: Request, enabled=Depends(require_enabled)):
    return getattr(request.app.state, 'corporate_auth', None) or default_auth()


def get_exports(request: Request, enabled=Depends(require_enabled)):
    return getattr(request.app.state, 'corporate_exports', None) or default_exports()


def principal(request: Request, store=Depends(get_machine_store)):
    auth = request.headers.get('Authorization', '')
    parts = auth.split(' ')
    if len(parts) != 2 or parts[0].lower() != 'bearer' or not parts[1]:
        raise MachineAuthError(401, 'UNAUTHORIZED', 'Cần Bearer token của tài khoản API.')
    return store.authenticate(parts[1])


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    Username: str = Field(min_length=1, max_length=80)
    Password: SecretStr = Field(min_length=1, max_length=1024)


class LoginResult(BaseModel):
    code: str
    message: str
    accessToken: str
    expiresIn: int


@router.post('/login', response_model=LoginResult, name='corporate_login')
def login(body: Login, request: Request, store=Depends(get_machine_store)):
    # Do not trust caller-supplied X-Forwarded-For for throttling. Account and
    # global limits still apply when the immediate peer is a reverse proxy.
    client_key = request.client.host if request.client else 'unknown'
    return store.login(body.Username, body.Password.get_secret_value(), client_key)


def _headers(response, result, contract='S-v3-CNT-1'):
    response.headers['X-Snapshot-Id'] = result['pagination']['snapshotId']
    response.headers['X-Source-Read-At'] = result['pagination']['sourceReadAt']
    response.headers['X-Corporate-Contract'] = contract
    for key, header in (('page', 'X-Page'), ('limit', 'X-Limit'),
                        ('total', 'X-Total-Count'), ('hasNext', 'X-Has-Next')):
        response.headers[header] = str(result['pagination'][key]).lower()
    if contract != 'S-v3-CNT-1':
        count, limit = result['pagination']['total'], result['pagination']['limit']
        response.headers['X-Total-Pages'] = str((count + limit - 1) // limit)


def public_result(result):
    """Only the envelope specified by the receiving system; metadata stays internal."""
    return {key: result[key] for key in ('data', 'code', 'message')}


def list_endpoint(resource):
    def endpoint(response: Response, query: Annotated[Query, QueryParam()],
                 actor=Depends(principal), exports=Depends(get_exports)):
        require_access(actor, query.companyId, resource)
        result = exports.read(resource, query, max_age_seconds=max_age_seconds())
        _headers(response, result)
        return public_result(result)
    endpoint.__name__ = f'corporate_{resource}'
    return endpoint


def detail_endpoint(resource):
    def endpoint(response: Response, record_id: Annotated[str, Path(min_length=1, max_length=255)],
                 query: Annotated[Query, QueryParam()], actor=Depends(principal), exports=Depends(get_exports)):
        require_access(actor, query.companyId, resource)
        if query.page != 1:
            raise CorporateError(422, 'INVALID_PAGE', 'Tra cứu một mã không sử dụng page lớn hơn 1.')
        result = exports.read(resource, query, identity=record_id, max_age_seconds=max_age_seconds())
        _headers(response, result)
        return public_result(result)
    endpoint.__name__ = f'corporate_{resource}_detail'
    return endpoint


for resource, model in MODELS.items():
    router.add_api_route('/' + resource, list_endpoint(resource), methods=['GET'], response_model=Page[model],
                         tags=['Tổng công ty / S'])
    # containerSize only specifies a list; its copied detail example names a
    # different resource. Do not infer another public contract from that typo.
    if resource in IDENTITY and resource != 'containerSize':
        router.add_api_route('/' + resource + '/{record_id}', detail_endpoint(resource),
                             methods=['GET'], response_model=Page[model], tags=['Tổng công ty / S'])


def operation_list_endpoint(resource):
    def endpoint(response: Response, query: Annotated[OperationQuery, QueryParam()],
                 actor=Depends(principal), exports=Depends(get_exports)):
        require_access(actor, query.companyId, resource)
        result = exports.read(resource, query, max_age_seconds=max_age_seconds())
        _headers(response, result, 'OPRT-v1.5-CNT-1')
        return public_result(result)
    endpoint.__name__ = 'corporate_' + resource.replace('.', '_')
    return endpoint


for resource, model in OPERATION_MODELS.items():
    # The B/C catalog sheets specify list GET only; no invented detail or write API.
    router.add_api_route(OPERATION_PATHS[resource].removeprefix('/api'),
                         operation_list_endpoint(resource), methods=['GET'], response_model=Page[model],
                         tags=['Tổng công ty / Danh mục vận hành'])
