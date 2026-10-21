"""Independent HTTP surface for corporate consumers, without dashboard routes."""
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from starlette.exceptions import HTTPException

try:
    from ..config import settings
except ImportError:  # ``corporate_api`` imported from the backend working directory.
    from config import settings

from .router import error_response, read_mode, router


logger = logging.getLogger(__name__)

EXPOSED_HEADERS = [
    'X-Snapshot-Id', 'X-Source-Read-At', 'X-Corporate-Contract', 'X-Result-Digest',
    'X-Page', 'X-Limit', 'X-Total-Count', 'X-Total-Pages', 'X-Has-Next',
    'X-Error-Code', 'Retry-After', 'WWW-Authenticate',
]


def create_app():
    application = FastAPI(
        title='Cảng Nghệ Tĩnh - API Tổng công ty',
        description=(
            'API sản lượng và danh mục dành cho Tổng công ty.\n\n'
            '1. Mở **POST /api/login**, chọn **Try it out**, nhập tài khoản API rồi bấm **Execute**.\n'
            '2. Sao chép giá trị **accessToken** trong phản hồi đăng nhập thành công.\n'
            '3. Bấm **Authorize**, dán riêng token (không thêm chữ Bearer), rồi xác nhận.\n'
            '4. Mở API cần kiểm tra, chọn **Try it out**, nhập các tham số và bấm **Execute**. '
            'Ngày báo cáo dùng định dạng **YYYYMMDD**.\n\n'
            'Khi xong, gọi **POST /api/logout** để thu hồi token. '
            'Token không được lưu qua lần tải lại trang. '
            'Tài khoản API độc lập với tài khoản đăng nhập dashboard.'
        ),
        version='1.0.0',
        docs_url='/docs',
        redoc_url=None,
        openapi_url='/openapi.json',
        swagger_ui_oauth2_redirect_url=None,
        swagger_ui_parameters={'persistAuthorization': False, 'validatorUrl': None},
    )

    @application.middleware('http')
    async def secure_responses(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:
            # Unexpected storage/driver errors may contain source credentials.
            # Known source/auth failures retain their existing CorporateRoute envelope.
            logger.error('Corporate API request failed (%s)', type(exc).__name__)
            response = error_response(500, 'INTERNAL_ERROR', 'API tạm thời không xử lý được yêu cầu.')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 404:
            return error_response(404, 'NOT_FOUND', 'Đường dẫn API không tồn tại.')
        if exc.status_code == 405:
            response = error_response(405, 'METHOD_NOT_ALLOWED', 'Phương thức HTTP không được hỗ trợ.')
            if exc.headers and exc.headers.get('Allow'):
                response.headers['Allow'] = exc.headers['Allow']
            return response
        return error_response(exc.status_code, 'HTTP_ERROR', 'Không thể thực hiện yêu cầu API.')

    application.include_router(router)

    # Keep the route parser backward compatible with published snapshots while
    # documenting the deployed read mode. Resolve it when the schema is requested,
    # not at import time: tests, CLI imports and app factories may set env later.
    schemas_by_mode = {}

    def corporate_openapi():
        mode = read_mode()
        if mode not in schemas_by_mode:
            schema = get_openapi(title=application.title, version=application.version,
                                 description=application.description, routes=application.routes)
            if mode == 'live':
                for methods in schema['paths'].values():
                    for operation in methods.values():
                        operation['parameters'] = [parameter for parameter in operation.get('parameters', [])
                                                   if parameter.get('name') != 'snapshotId']
                        operation.get('responses', {}).get('200', {}).get('headers', {}).pop('X-Snapshot-Id', None)
            schemas_by_mode[mode] = schema
        application.openapi_schema = schemas_by_mode[mode]
        return application.openapi_schema

    application.openapi = corporate_openapi

    @application.get('/health', include_in_schema=False)
    def health():
        # Process liveness only: do not open SQLite or query SmartTOS here.
        return {'status': 'ok'}

    # CORS wraps secure_responses so unexpected error envelopes are also readable
    # by the configured dashboard origin; no cookie-based dashboard login is used.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_cors_origins,
        allow_credentials=False,
        allow_methods=['GET', 'POST'],
        allow_headers=['Accept', 'Content-Type', 'Authorization'],
        expose_headers=EXPOSED_HEADERS,
    )
    return application


app = create_app()
