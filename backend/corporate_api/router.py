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
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .auth import MachineAuthError, MachineStore, require_access
from .contracts import ErrorResult, IDENTITY, MODELS, Page, PRODUCTION, PUBLIC_QUERY_MODELS, Query
from .operation_contracts import OPERATION_MODELS, OPERATION_PATHS, OperationQuery
from .errors import CorporateError
from .store import ExportStore
from .freshness import max_age_seconds

logger = logging.getLogger(__name__)


def _header(description, kind='string', **schema):
    return {'description': description, 'schema': {'type': kind, **schema}}


ERROR_RESPONSES = {
    status: {
        'model': ErrorResult,
        'description': description,
        'headers': {
            'X-Error-Code': _header('Mã lỗi để ứng dụng phân biệt lỗi xác thực, tham số và dữ liệu nguồn.'),
            'Cache-Control': _header('Không lưu bộ nhớ đệm của phản hồi.', enum=['no-store']),
            **({'WWW-Authenticate': _header('Cơ chế xác thực yêu cầu.', enum=['Bearer'])} if status == 401 else {}),
            **({'Retry-After': _header('Số giây nên chờ trước khi thử lại; có khi lỗi có thể thử lại.', 'integer', minimum=0)}
               if status in {429, 503} else {}),
        },
    }
    for status, description in {
        401: 'Thiếu token, token không hợp lệ/hết hạn hoặc thông tin đăng nhập sai.',
        403: 'Tài khoản không có quyền trên đơn vị hoặc API yêu cầu.',
        404: 'Không tìm thấy ID trong danh mục hoặc đường dẫn API không tồn tại.',
        409: 'Bản công bố thay đổi hoặc thiếu snapshotId khi phân trang bản công bố.',
        410: 'Bản công bố đã hết hiệu lực; lấy lại từ trang 1 với snapshotId mới.',
        422: 'Tham số không hợp lệ, bộ lọc không hỗ trợ hoặc kỳ truy vấn vượt giới hạn.',
        429: 'Vượt giới hạn tần suất; chờ Retry-After nếu có trước khi thử lại.',
        500: 'Lỗi xử lý nội bộ; không trả chi tiết nguồn hoặc thông tin bí mật.',
        503: 'Nguồn tạm thời không sẵn sàng hoặc dữ liệu chưa đủ điều kiện trả đúng hợp đồng.',
    }.items()
}
PAGE_HEADERS = {
    'Cache-Control': _header('Không lưu bộ nhớ đệm của phản hồi.', enum=['no-store']),
    'X-Page': _header('Trang hiện tại.', 'integer', minimum=1),
    'X-Limit': _header('Số dòng tối đa trên trang.', 'integer', minimum=1, maximum=100),
    'X-Total-Count': _header('Tổng số bản ghi sau lọc trước phân trang.', 'integer', minimum=0),
    'X-Has-Next': _header('Còn trang tiếp theo hay không.', 'boolean'),
    'X-Source-Read-At': _header('Thời điểm đọc nguồn của kết quả.', format='date-time'),
    'X-Corporate-Contract': _header('Phiên bản hợp đồng API.'),
    'X-Snapshot-Id': _header('Chỉ có ở chế độ bản công bố; dùng cho các trang kế tiếp.', pattern='^[a-f0-9]{32}$'),
}
PAGE_RESPONSES = {200: {'description': 'Thành công. JSON chỉ gồm data, code, message; thông tin phân trang nằm trong header.',
                        'headers': PAGE_HEADERS}}
PRODUCTION_PAGE_RESPONSES = {200: {**PAGE_RESPONSES[200], 'headers': {
    **PAGE_HEADERS,
    'X-Result-Digest': _header('SHA-256 của toàn bộ kết quả sau lọc, trước phân trang. Nếu đổi giữa các trang, lấy lại từ trang 1.', pattern='^[a-f0-9]{64}$'),
}}}


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


router = APIRouter(prefix='/api', tags=['Tổng công ty'], route_class=CorporateRoute, responses=ERROR_RESPONSES)
bearer_scheme = HTTPBearer(
    scheme_name='CorporateBearer',
    bearerFormat='Opaque token',
    description='Dán accessToken từ POST /api/login, không thêm chữ Bearer.',
    auto_error=False,
)


def require_enabled():
    if os.environ.get('CORPORATE_API_ENABLED', '').strip().lower() not in {'1', 'true'}:
        raise CorporateError(503, 'CORPORATE_API_DISABLED', 'API Tổng công ty chưa được kích hoạt.')


@lru_cache(maxsize=1)
def default_auth():
    return MachineStore()


@lru_cache(maxsize=1)
def default_exports():
    return ExportStore()


def read_mode():
    mode = os.environ.get('CORPORATE_READ_MODE', 'published').strip().lower()
    if mode not in {'published', 'live'}:
        raise CorporateError(503, 'SOURCE_CONFIGURATION_INVALID', 'Cấu hình nguồn đọc API không hợp lệ.')
    return mode


@lru_cache(maxsize=1)
def default_live():
    from .live import LiveReader
    return LiveReader()


def get_machine_store(request: Request, enabled=Depends(require_enabled)):
    return getattr(request.app.state, 'corporate_auth', None) or default_auth()


def get_exports(request: Request, enabled=Depends(require_enabled)):
    configured = getattr(request.app.state, 'corporate_exports', None)
    if configured is not None:
        return configured
    return default_live() if read_mode() == 'live' else default_exports()


def principal(request: Request, store=Depends(get_machine_store), _bearer=Depends(bearer_scheme)):
    # HTTPBearer declares Swagger's Authorize scheme only. The existing exact
    # header parsing and machine-store validation below remain authoritative.
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


@router.post('/logout', response_model=Page[dict], name='corporate_logout')
def logout(request: Request, actor=Depends(principal), store=Depends(get_machine_store)):
    # principal already checked the exact Bearer syntax and machine identity.
    # The store validates the token again under its write transaction so a
    # concurrent expiry, logout or account change cannot revoke another token.
    token = request.headers['Authorization'].split(' ')[1]
    return store.logout(token)


def _headers(response, result, contract='S-v3-CNT-1'):
    snapshot_id = result['pagination'].get('snapshotId')
    if snapshot_id:
        response.headers['X-Snapshot-Id'] = snapshot_id
    result_digest = result['pagination'].get('resultDigest')
    if result_digest:
        response.headers['X-Result-Digest'] = result_digest
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
    query_model = PUBLIC_QUERY_MODELS[resource]
    def endpoint(response: Response, query: Annotated[query_model, QueryParam()],
                 actor=Depends(principal), exports=Depends(get_exports)):
        query = Query.model_validate(query.model_dump(exclude_unset=True))
        require_access(actor, query.companyId, resource)
        result = exports.read(resource, query, max_age_seconds=max_age_seconds())
        _headers(response, result)
        return public_result(result)
    endpoint.__name__ = f'corporate_{resource}'
    return endpoint


def detail_endpoint(resource):
    query_model = PUBLIC_QUERY_MODELS[resource]
    # These five S sheets explicitly require a nonempty ID shorter than 255.
    # Keep other catalog contracts unchanged; native numeric IDs are narrower.
    id_max_length = 254 if resource in {
        'cargoType', 'cargoCategory', 'handlingMethodList', 'class', 'origins'} else 255
    def endpoint(response: Response, record_id: Annotated[str, Path(min_length=1, max_length=id_max_length)],
                 query: Annotated[query_model, QueryParam()], actor=Depends(principal), exports=Depends(get_exports)):
        query = Query.model_validate(query.model_dump(exclude_unset=True))
        require_access(actor, query.companyId, resource)
        if query.page != 1:
            raise CorporateError(422, 'INVALID_PAGE', 'Tra cứu một mã không sử dụng page lớn hơn 1.')
        result = exports.read(resource, query, identity=record_id, max_age_seconds=max_age_seconds())
        _headers(response, result)
        return public_result(result)
    endpoint.__name__ = f'corporate_{resource}_detail'
    return endpoint


for resource, model in MODELS.items():
    description = ('Lấy sản lượng theo kỳ, bao gồm cả ngày bắt đầu và ngày kết thúc. '
                   'startDate và endDate bắt buộc, dạng YYYYMMDD. Chế độ trực tiếp cho phép tối đa '
                   '31 ngày mỗi yêu cầu (tính cả hai đầu); chia kỳ dài thành từng tháng. '
                   'Mã lọc tàu/phương án phải tồn tại trong danh mục nguồn hợp lệ: '
                   'chế độ trực tiếp trả 422 FILTER_ID_NOT_FOUND nếu không có mã; '
                   'mã hợp lệ không có tác nghiệp trong kỳ trả 200 với data rỗng. '
                   'Mỗi yêu cầu đọc lại nguồn; không dùng kết quả lưu từ yêu cầu trước. '
                   'Đọc đủ các trang tới khi X-Has-Next=false và kiểm tra X-Result-Digest không đổi giữa các trang.'
                   if resource in PRODUCTION else
                   'Lấy danh mục theo các bộ lọc được liệt kê. Đọc đủ các trang tới khi X-Has-Next=false. '
                   'Ở chế độ trực tiếp, mỗi yêu cầu đọc lại nguồn; danh mục có thể thay đổi giữa các trang.')
    router.add_api_route('/' + resource, list_endpoint(resource), methods=['GET'], response_model=Page[model],
                         tags=['Tổng công ty / S'], description=description,
                         responses=PRODUCTION_PAGE_RESPONSES if resource in PRODUCTION else PAGE_RESPONSES)
    # containerSize only specifies a list; its copied detail example names a
    # different resource. Do not infer another public contract from that typo.
    if resource in IDENTITY and resource != 'containerSize':
        router.add_api_route('/' + resource + '/{record_id}', detail_endpoint(resource),
                             methods=['GET'], response_model=Page[model], tags=['Tổng công ty / S'],
                             description='Tra cứu ID gốc trong danh mục. Nếu không có ID phù hợp, trả 404 NOT_FOUND; page phải bằng 1.',
                             responses=PAGE_RESPONSES)


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
                         tags=['Tổng công ty / Danh mục vận hành'],
                         responses={200: {**PAGE_RESPONSES[200], 'headers': {
                             **PAGE_HEADERS, 'X-Total-Pages': _header('Tổng số trang.', 'integer', minimum=0)}}})
