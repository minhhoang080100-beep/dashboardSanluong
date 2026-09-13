"""Bearer-authenticated APIs for the dashboard's separate control database."""
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from decimal import Decimal

if __package__:
    from .control_store import ControlError, ControlStore, require_admin, require_editor, require_scope
    from .repository import dashboard_repo
else:
    from control_store import ControlError, ControlStore, require_admin, require_editor, require_scope
    from repository import dashboard_repo

router = APIRouter(prefix="/api")
Terminal = Literal["cua_lo", "ben_thuy"]


@lru_cache(maxsize=1)
def _default_store():
    return ControlStore()


def get_store(request: Request) -> ControlStore:
    configured = getattr(request.app.state, "control_store", None)
    return configured if configured is not None else _default_store()


def get_repository(request: Request):
    configured = getattr(request.app.state, 'reporting_service', None)
    return configured.repo if configured is not None else dashboard_repo


def validate_plan_voyages(user, rows, repo):
    """Check scope before source access; monthly-only requests never need SQL."""
    require_editor(user)
    pairs = set()
    for row in rows:
        ControlStore.validate_plan(row)
        require_scope(user, row['terminal'])
        if row.get('period_type', 'month') == 'voyage':
            pairs.add((row['terminal'], str(row['voyage_id'])))
    if pairs:
        valid = repo.validate_voyages(pairs)
        missing = sorted(pairs - set(valid))
        if missing:
            description = ', '.join(f'{terminal}/{voyage_id}' for terminal, voyage_id in missing[:10])
            raise ControlError(422, 'PLAN_VOYAGE_NOT_FOUND', f'Chuyến không tồn tại hoặc không còn hoạt động tại xí nghiệp đã chọn: {description}.')


def _bearer(request: Request):
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or " " in token:
        raise ControlError(401, "SESSION_INVALID", "Vui lòng đăng nhập.")
    return token


def require_authenticated_user(request: Request, store: ControlStore = Depends(get_store)):
    return store.authenticate(_bearer(request))


def require_user(user: dict = Depends(require_authenticated_user)):
    if user["must_change_password"]:
        raise ControlError(403, "PASSWORD_CHANGE_REQUIRED", "Vui lòng đổi mật khẩu tạm thời trước khi tiếp tục.")
    return user


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginBody(InputModel):
    username: str = Field(min_length=1, max_length=80)
    password: SecretStr = Field(min_length=1, max_length=1024)


class PasswordBody(InputModel):
    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: SecretStr = Field(min_length=12, max_length=1024)


class UserBody(InputModel):
    username: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=1, max_length=160)
    role: Literal["admin", "manager", "viewer"]
    terminals: list[Terminal] = Field(min_length=1, max_length=2)
    password: SecretStr | None = Field(default=None, min_length=12, max_length=1024)


class UserUpdate(InputModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    role: Literal["admin", "manager", "viewer"] | None = None
    terminals: list[Terminal] | None = Field(default=None, min_length=1, max_length=2)
    is_active: bool | None = None


class PlanBody(InputModel):
    terminal: Terminal
    period_type: Literal["month", "voyage"] = "month"
    month: str | None = None
    voyage_id: int | None = Field(default=None, ge=1, le=2147483647)
    metric: Literal["tonnage", "teu"]
    amount: Decimal = Field(ge=0, le=1000000000000, decimal_places=6)
    reference: str = Field(default="", max_length=500)
    note: str = Field(default="", max_length=4000)


class PlanImportBody(InputModel):
    rows: list[PlanBody] = Field(min_length=1, max_length=500)


class PlanUpdate(InputModel):
    expected_revision: int = Field(ge=1)
    terminal: Terminal | None = None
    period_type: Literal['month', 'voyage'] | None = None
    month: str | None = None
    voyage_id: int | None = Field(default=None, ge=1, le=2147483647)
    metric: Literal['tonnage', 'teu'] | None = None
    amount: Decimal | None = Field(default=None, ge=0, le=1000000000000, decimal_places=6)
    reference: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=4000)


class PlanRevision(InputModel):
    expected_revision: int = Field(ge=1)


class PlanCancel(PlanRevision):
    note: str = Field(min_length=1, max_length=4000)


class IssueBody(InputModel):
    terminal: Terminal
    namespace: str = Field(min_length=1, max_length=80)
    source_id: str | int
    issue: str = Field(min_length=1, max_length=100)
    status: Literal["open", "resolved", "ignored"]
    note: str = Field(min_length=1, max_length=4000)


@router.post("/auth/login")
def login(body: LoginBody, request: Request, store: ControlStore = Depends(get_store)):
    # Do not trust client-controlled X-Forwarded-For here. Deployment proxy trust
    # is configured by the ASGI server; the username throttle also applies.
    client = request.client.host if request.client else "unknown"
    return store.login(body.username, body.password.get_secret_value(), client)


@router.get("/auth/me")
def me(user: dict = Depends(require_authenticated_user)):
    return user


@router.post("/auth/logout")
def logout(request: Request, user: dict = Depends(require_authenticated_user), store: ControlStore = Depends(get_store)):
    store.logout(_bearer(request))
    return {"ok": True}


@router.post("/auth/password")
def change_password(body: PasswordBody, request: Request, user: dict = Depends(require_authenticated_user), store: ControlStore = Depends(get_store)):
    return store.change_password(_bearer(request), body.current_password.get_secret_value(), body.new_password.get_secret_value())


@router.get("/users")
def users(user: dict = Depends(require_user), store: ControlStore = Depends(get_store)):
    return store.list_users(user)


@router.post("/users", status_code=201)
def create_user(body: UserBody, user: dict = Depends(require_user), store: ControlStore = Depends(get_store)):
    values = body.model_dump(exclude={"password"})
    return store.create_user(user, **values, password=body.password.get_secret_value() if body.password else None)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, user: dict = Depends(require_user), store: ControlStore = Depends(get_store)):
    if any(value is None for value in body.model_dump(exclude_unset=True).values()):
        raise ControlError(422, "INVALID_USER_UPDATE", "Không được xóa trống thuộc tính tài khoản.")
    return store.update_user(user, user_id, **body.model_dump(exclude_unset=True))


@router.delete("/users/{user_id}")
def deactivate_user(user_id: int, user: dict = Depends(require_user), store: ControlStore = Depends(get_store)):
    # Preserve actor references and history; deletion means account deactivation.
    return store.update_user(user, user_id, is_active=False)


@router.post("/users/{user_id}/reset-password")
def reset_user_password(user_id: int, user: dict = Depends(require_user), store: ControlStore = Depends(get_store)):
    return store.reset_password(user, user_id)


@router.get("/plans")
def plans(terminal: Literal["all", "cua_lo", "ben_thuy"] | None = None,
          month: str | None = None, voyage_id: int | None = Query(default=None, ge=1),
          status: Literal["draft", "approved", "cancelled"] | None = None,
          period_type: Literal["month", "voyage"] | None = None,
          page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=100),
          user: dict = Depends(require_user), store: ControlStore = Depends(get_store)):
    return store.list_plans(user, terminal, month, voyage_id, status, page, page_size, period_type=period_type)


@router.post("/plans", status_code=201)
def create_plan(body: PlanBody, user: dict = Depends(require_user), store: ControlStore = Depends(get_store), repo=Depends(get_repository)):
    validate_plan_voyages(user, [body.model_dump()], repo)
    return store.create_plan(user, **body.model_dump())


@router.post("/plans/import", status_code=201)
def import_plans(body: PlanImportBody, user: dict = Depends(require_user), store: ControlStore = Depends(get_store), repo=Depends(get_repository)):
    rows = [row.model_dump() for row in body.rows]
    validate_plan_voyages(user, rows, repo)
    items = store.create_plans(user, rows)
    return {"items": items, "count": len(items)}


@router.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: int, body: PlanRevision, user: dict = Depends(require_user),
                 store: ControlStore = Depends(get_store), repo=Depends(get_repository)):
    require_editor(user)
    current = store.get_plan(user, plan_id)
    expected = body.expected_revision
    store._draft(current, expected)
    validate_plan_voyages(user, [{key: current[key] for key in PlanBody.model_fields}], repo)
    return store.approve_plan(user, plan_id, expected)


@router.get('/planning/voyages')
def planning_voyages(terminal: Terminal, search: str = Query(default='', max_length=100),
                    limit: int = Query(default=30, ge=1, le=100), user: dict = Depends(require_user), repo=Depends(get_repository)):
    require_editor(user)
    require_scope(user, terminal)
    return {'items': repo.search_voyages(terminal, search, limit), 'limit': limit}


@router.get('/plans/{plan_id:int}')
def get_plan(plan_id: int, user: dict = Depends(require_user), store: ControlStore = Depends(get_store)):
    return store.get_plan(user, plan_id)


@router.patch('/plans/{plan_id}')
def update_plan(plan_id: int, body: PlanUpdate, user: dict = Depends(require_user),
                store: ControlStore = Depends(get_store), repo=Depends(get_repository)):
    require_editor(user)
    current = store.get_plan(user, plan_id)
    store._draft(current, body.expected_revision)
    changes = body.model_dump(exclude_unset=True, exclude={'expected_revision'})
    merged = {**{key: current[key] for key in PlanBody.model_fields}, 'amount': current['amount_decimal'], **changes}
    validate_plan_voyages(user, [merged], repo)
    return store.update_plan(user, plan_id, body.expected_revision, **changes)


@router.post('/plans/{plan_id}/cancel')
def cancel_plan(plan_id: int, body: PlanCancel, user: dict = Depends(require_user), store: ControlStore = Depends(get_store)):
    return store.cancel_plan(user, plan_id, body.expected_revision, body.note)


@router.get("/issues")
def issues(terminal: Literal["all", "cua_lo", "ben_thuy"] | None = None, namespace: str | None = None,
           source_id: str | None = None, history: bool = False, page: int = Query(default=1, ge=1),
           page_size: int = Query(default=50, ge=1, le=100),
           user: dict = Depends(require_user), store: ControlStore = Depends(get_store)):
    return store.list_issues(user, terminal, namespace, source_id, history=history, page=page, page_size=page_size)


@router.post("/issues", status_code=201)
def set_issue(body: IssueBody, user: dict = Depends(require_user), store: ControlStore = Depends(get_store)):
    return store.set_issue(user, **body.model_dump())
