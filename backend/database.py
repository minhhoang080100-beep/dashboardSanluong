"""Bounded SQL connections; driver errors never escape with connection details."""

import logging
import re

import pyodbc

if __package__:
    from .config import settings
else:
    from config import settings

logger = logging.getLogger(__name__)
DATABASES = frozenset({"SmartTOS", "SmartTOS_BenThuy"})
_DIAGNOSTIC_SQLSTATES = frozenset({
    "01000", "08001", "08004", "08006", "08007", "08S01", "28000",
    "HY000", "HYT00", "HYT01", "IM002", "IM003", "IM004", "IM005", "IM006", "IM014",
})


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


def _connection_diagnostic(exc: Exception) -> tuple[str, str]:
    """Classify driver evidence in memory; return only fixed, safe log labels."""
    details = [value for value in exc.args if isinstance(value, str)]
    sqlstate = "unknown"
    for detail in details:
        match = re.match(r"(?:\[([A-Z0-9]{5})\]|([A-Z0-9]{5})$)", detail)
        candidate = next((group for group in match.groups() if group), None) if match else None
        if candidate in _DIAGNOSTIC_SQLSTATES:
            sqlstate = candidate
            break
    message = " ".join(details).lower()
    if any(marker in message for marker in (
        "certificate verify failed", "certificate chain was issued by an authority that is not trusted",
        "self-signed certificate", "self signed certificate", "unable to get local issuer certificate",
        "target principal name is incorrect", "subject name does not match host name",
        "certificate has expired", "certificate is not yet valid",
    )):
        category = "tls_certificate"
    elif any(marker in message for marker in ("ssl provider", "ssl routines", "tls handshake")):
        category = "tls_handshake"
    elif sqlstate == "28000":
        category = "authentication"
    elif sqlstate in {"HYT00", "HYT01"} or "timeout expired" in message or "timed out" in message:
        category = "timeout"
    elif sqlstate.startswith("IM") or "can't open lib" in message or "data source name not found" in message:
        category = "driver_configuration"
    elif sqlstate == "08S01" or any(marker in message for marker in (
        "tcp provider", "network-related", "communication link failure", "connection refused",
        "name or service not known",
    )):
        category = "network"
    elif sqlstate.startswith("08"):
        # 08001 alone also occurs for TLS errors, so it is not proof of a network failure.
        category = "connection"
    else:
        category = "unknown"
    return category, sqlstate


def get_db_connection(database: str | None = None):
    target = database or settings.DB_DATABASE
    if target not in DATABASES:
        raise ValueError("Unsupported database")
    missing_fields = [name for name in ("DB_SERVER", "DB_USERNAME", "DB_PASSWORD") if not getattr(settings, name)]
    if missing_fields:
        logger.error("Database connection failed; category=missing_configuration missing_fields=%s", ",".join(missing_fields))
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
    except Exception as exc:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        category, sqlstate = _connection_diagnostic(exc)
        logger.error(
            "Database connection failed; category=%s sqlstate=%s encrypt=%s trust_server_certificate=%s",
            category, sqlstate, settings.DB_ENCRYPT, settings.DB_TRUST_SERVER_CERTIFICATE,
        )
        raise DatabaseUnavailable() from None
