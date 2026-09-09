"""Environment configuration shared by the API and read-only audit commands."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DB_SERVER: str = ""
    DB_DATABASE: str = "SmartTOS"
    DB_USERNAME: str = ""
    DB_PASSWORD: str = ""
    DB_DRIVER: str = "ODBC Driver 17 for SQL Server"
    DB_CONNECT_TIMEOUT_SECONDS: int = Field(default=5, ge=1, le=60)
    DB_QUERY_TIMEOUT_SECONDS: int = Field(default=20, ge=1, le=120)
    DB_ENCRYPT: bool = True
    DB_TRUST_SERVER_CERTIFICATE: bool = False

    # Compatibility only: these fields do not enable API authentication.
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_HOURS: int = 8
    API_HOST: str = "127.0.0.1"
    API_PORT: int = Field(default=8000, ge=1, le=65535)
    # Keep the deployed frontend separate from additional development origins.
    # An older CORS_ORIGINS environment override must not remove the main UI.
    FRONTEND_ORIGIN: str = "https://dashboard-sanluong.vercel.app"
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @field_validator("DB_DATABASE")
    @classmethod
    def known_database(cls, value: str) -> str:
        if value not in {"SmartTOS", "SmartTOS_BenThuy"}:
            raise ValueError("DB_DATABASE must be SmartTOS or SmartTOS_BenThuy")
        return value

    @field_validator("CORS_ORIGINS")
    @classmethod
    def exact_origins(cls, values: list[str]) -> list[str]:
        from urllib.parse import urlsplit

        for value in values:
            parsed = urlsplit(value)
            if (
                "*" in value or parsed.scheme not in {"http", "https"}
                or not parsed.netloc or parsed.path or parsed.query or parsed.fragment
                or parsed.username or parsed.password
            ):
                raise ValueError("CORS_ORIGINS must contain exact HTTP(S) origins without paths")
        return values

    @field_validator("FRONTEND_ORIGIN")
    @classmethod
    def exact_frontend_origin(cls, value: str) -> str:
        return cls.exact_origins([value])[0]

    @property
    def allowed_cors_origins(self) -> list[str]:
        """The main frontend plus explicitly configured additional origins."""
        return list(dict.fromkeys([self.FRONTEND_ORIGIN, *self.CORS_ORIGINS]))

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
