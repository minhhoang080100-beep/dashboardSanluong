"""Bounded SQL connections; driver errors never escape with connection details."""

import logging

import pyodbc

if __package__:
    from .config import settings
else:
    from config import settings

logger = logging.getLogger(__name__)
DATABASES = frozenset({"SmartTOS", "SmartTOS_BenThuy"})


class DatabaseUnavailable(RuntimeError):
    code = "DATABASE_UNAVAILABLE"
    message = "Không thể kết nối nguồn dữ liệu. Vui lòng thử lại hoặc liên hệ quản trị."

    def __init__(self):
        super().__init__(self.message)


class DatabaseQueryError(DatabaseUnavailable):
    code = "DATABASE_QUERY_FAILED"
    message = "Không thể đọc dữ liệu báo cáo. Vui lòng liên hệ quản trị để kiểm tra truy vấn."


def _odbc_value(value: str) -> str:
    # Braced values prevent semicolons/braces in credentials becoming options.
    return "{" + value.replace("}", "}}") + "}"


def get_db_connection(database: str | None = None):
    target = database or settings.DB_DATABASE
    if target not in DATABASES:
        raise ValueError("Unsupported database")
    if not all((settings.DB_SERVER, settings.DB_USERNAME, settings.DB_PASSWORD)):
        raise DatabaseUnavailable()
    parts = {
        "DRIVER": settings.DB_DRIVER,
        "SERVER": settings.DB_SERVER,
        "DATABASE": target,
        "UID": settings.DB_USERNAME,
        "PWD": settings.DB_PASSWORD,
    }
    conn_str = ";".join(f"{key}={_odbc_value(value)}" for key, value in parts.items())
    conn_str += (
        f";Encrypt={'yes' if settings.DB_ENCRYPT else 'no'};"
        f"TrustServerCertificate={'yes' if settings.DB_TRUST_SERVER_CERTIFICATE else 'no'};"
        "ApplicationIntent=ReadOnly;"
    )
    connection = None
    try:
        connection = pyodbc.connect(
            conn_str, timeout=settings.DB_CONNECT_TIMEOUT_SECONDS, autocommit=True
        )
        connection.timeout = settings.DB_QUERY_TIMEOUT_SECONDS
        return connection
    except Exception:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        logger.error("Database connection failed; inspect server connectivity and configuration.")
        raise DatabaseUnavailable() from None
