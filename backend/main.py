from datetime import date
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

if __package__:
    from .config import settings
    from .database import DatabaseUnavailable, get_db_connection
    from .repository import VoyageNotFound, dashboard_repo, date_range
else:
    from config import settings
    from database import DatabaseUnavailable, get_db_connection
    from repository import VoyageNotFound, dashboard_repo, date_range

app = FastAPI(
    title="Cảng Nghệ Tĩnh - Dashboard API",
    description="Báo cáo sản xuất từ SmartTOS và SmartTOS_BenThuy; định nghĩa nghiệp vụ đang chờ đối soát.",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_cors_origins,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Accept", "Content-Type"],
)


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
):
    try:
        start, end = date_range(start_date, end_date, terminal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"start_date": start, "end_date": end, "terminal": terminal}


@app.get("/")
def read_root():
    return {"message": "Cảng Nghệ Tĩnh Dashboard API", "version": "2.0.0"}


@app.get("/api/health")
def health_check():
    # Both sources must be reachable: one default connection is insufficient.
    for name in ("SmartTOS", "SmartTOS_BenThuy"):
        connection = get_db_connection(name)
        if connection is None:
            raise DatabaseUnavailable()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT TOP (1) shiftDate FROM dbo.TallyShift")
                cursor.fetchone()
            finally:
                cursor.close()
        except Exception:
            raise DatabaseUnavailable() from None
        finally:
            try:
                connection.close()
            except Exception:
                pass
    return {"status": "ok", "database": "connected", "sources": ["cua_lo", "ben_thuy"]}


@app.get("/api/dashboard")
def get_dashboard(selected: dict = Depends(filters)):
    return dashboard_repo.get_dashboard(**selected)


@app.get("/api/voyages/{terminal}/{voyage_id}")
def get_voyage_detail(
    terminal: Literal["cua_lo", "ben_thuy"],
    voyage_id: int = Path(ge=1, le=2147483647),
    start_date: date | None = None,
    end_date: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    try:
        return dashboard_repo.get_voyage_detail(
            terminal, voyage_id, start_date, end_date, page, page_size
        )
    except VoyageNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def legacy_endpoint(section: str):
    def endpoint(selected: dict = Depends(filters)):
        return dashboard_repo.get_dashboard(**selected)[section]
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
