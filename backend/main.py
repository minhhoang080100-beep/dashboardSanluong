from datetime import date
import logging
from time import perf_counter
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

if __package__:
    from .config import settings
    from .database import DatabaseUnavailable, get_db_connection, log_database_failure
    from .repository import VoyageNotFound, dashboard_repo, date_range
    from .control_api import router as control_router, require_user
    from .control_store import require_scope, require_admin
    from .integration import router as integration_router, get_reporting, report_scope
    from .reporting import ReportSnapshotNotFound
else:
    from config import settings
    from database import DatabaseUnavailable, get_db_connection, log_database_failure
    from repository import VoyageNotFound, dashboard_repo, date_range
    from control_api import router as control_router, require_user
    from control_store import require_scope, require_admin
    from integration import router as integration_router, get_reporting, report_scope
    from reporting import ReportSnapshotNotFound

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Cảng Nghệ Tĩnh - Dashboard API",
    description="Báo cáo sản xuất từ SmartTOS và SmartTOS_BenThuy; định nghĩa nghiệp vụ đang chờ đối soát.",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Accept", "Content-Type", "Authorization"],
)

app.include_router(control_router)
app.include_router(integration_router)


@app.exception_handler(ReportSnapshotNotFound)
async def report_expired(request: Request, exc: ReportSnapshotNotFound):
    return JSONResponse(status_code=410, content={"detail": {"code": exc.code, "message": "Phiên dữ liệu đã hết hạn. Hãy tải lại báo cáo rồi mở chi tiết."}})


@app.exception_handler(VoyageNotFound)
async def voyage_not_found(request: Request, exc: VoyageNotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.middleware("http")
async def no_cache_reports(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(DatabaseUnavailable)
async def database_error_handler(request: Request, exc: DatabaseUnavailable):
    return JSONResponse(status_code=503, content={"detail": {"code": exc.code, "message": exc.message}})


def filters(
    start_date: date | None = None,
    end_date: date | None = None,
    terminal: Literal["all", "cua_lo", "ben_thuy"] = "all",
    production_scope: Literal["nghe_tinh", "vietsun", "unclassified"] = "nghe_tinh",
    comparison: Literal['previous_period', 'previous_year'] = 'previous_period',
):
    try:
        start, end = date_range(start_date, end_date, terminal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"start_date": start, "end_date": end, "terminal": terminal, "production_scope": production_scope,
            **({'comparison': comparison} if comparison != 'previous_period' else {})}


@app.get("/")
def read_root():
    return {"message": "Cảng Nghệ Tĩnh Dashboard API", "version": "2.0.0"}


@app.get("/api/health/live")
def liveness():
    return {"status": "ok"}


@app.get("/api/health")
def health_check(user: dict = Depends(require_user)):
    require_admin(user)
    # Both sources must be reachable: one default connection is insufficient.
    for name in ("SmartTOS", "SmartTOS_BenThuy"):
        connection = get_db_connection(name)
        if connection is None:
            raise DatabaseUnavailable()
        cursor = None
        started_at = perf_counter()
        phase = "cursor"
        try:
            cursor = connection.cursor()
            phase = "execute"
            cursor.execute("SELECT TOP (1) shiftDate FROM dbo.TallyShift")
            phase = "fetch"
            cursor.fetchone()
        except Exception as exc:
            log_database_failure(exc, phase=phase, started_at=started_at, operation="health", log=logger)
            raise DatabaseUnavailable() from None
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    logger.warning("Database health cursor close failed.")
            try:
                connection.close()
            except Exception:
                logger.warning("Database health connection close failed.")
    return {"status": "ok", "database": "connected", "sources": ["cua_lo", "ben_thuy"]}


@app.get("/api/dashboard")
def get_dashboard(selected: dict = Depends(filters), refresh: bool = False,
                  user: dict = Depends(require_user), service=Depends(get_reporting)):
    require_scope(user, selected['terminal'])
    return service.get_report(**selected, refresh=refresh)


@app.get("/api/voyages/{terminal}/{voyage_id}")
def get_voyage_detail(
    terminal: Literal["cua_lo", "ben_thuy"],
    voyage_id: int = Path(ge=1, le=2147483647),
    start_date: date | None = None,
    end_date: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    operation_filter: Literal["all", "with_values", "missing_weight"] = "all",
    report_id: str | None = Query(default=None, max_length=128),
    production_scope: Literal["nghe_tinh", "vietsun", "unclassified"] = "nghe_tinh",
    user: dict = Depends(require_user), service=Depends(get_reporting),
):
    require_scope(user, terminal)
    try:
        if report_id:
            report = report_scope(service, user, report_id)
            selected_start, selected_end = date_range(start_date, end_date, terminal)
            if report['meta']['filters']['start_date'] != selected_start.isoformat() or report['meta']['filters']['end_date'] != selected_end.isoformat():
                raise ValueError('Kỳ chi tiết không khớp phiên báo cáo.')
            if report['meta']['filters'].get('production_scope') != production_scope:
                raise ValueError('Phạm vi sản lượng chi tiết không khớp phiên báo cáo.')
            return service.get_voyage_from_report(report_id, terminal, voyage_id, page, page_size, operation_filter)
        options = {} if operation_filter == "all" else {"operation_filter": operation_filter}
        return dashboard_repo.get_voyage_detail(
            terminal, voyage_id, start_date, end_date, page, page_size, production_scope=production_scope, **options
        )
    except VoyageNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def legacy_endpoint(section: str):
    def endpoint(selected: dict = Depends(filters), user: dict = Depends(require_user), service=Depends(get_reporting)):
        require_scope(user, selected['terminal'])
        return service.get_report(**selected)[section]
    return endpoint


for path, section in {
    "overview": "overview", "cargo-breakdown": "cargo", "throughput-history": "history",
    "terminal-breakdown": "terminals", "direction-breakdown": "directions",
    "top-customers": "customers", "efficiency": "efficiency", "yard-occupancy": "yard",
}.items():
    app.add_api_route(f"/api/{path}", legacy_endpoint(section), methods=["GET"], name=f"get_{path}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.API_HOST, port=settings.API_PORT)
