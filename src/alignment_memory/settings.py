from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Process settings with credential-free local defaults."""

    app_name: str = "Alignment Memory"
    app_mode: Literal["fixture", "live"] = "fixture"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str | None = None
    cors_allowed_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    supabase_jwt_issuer: str = "https://fixture.supabase.co/auth/v1"
    supabase_jwt_audience: str = "authenticated"
    supabase_jwks_url: str | None = None
    supabase_jwt_secret: str | None = None
    fixture_jwt_secret: str = "alignment-memory-fixture-jwt-signing-secret"
    fixture_test_auth_enabled: bool = False

    internal_hmac_secret: str | None = None
    fixture_hmac_secret: str = "alignment-memory-fixture-hmac-secret"
    internal_hmac_replay_window_seconds: int = 300

    github_app_id: str | None = None
    github_app_private_key: str | None = None
    github_sync_workflow: str = "alignment-analyze.yml"
    github_api_base_url: str = "https://api.github.com"
    github_api_timeout_seconds: float = 15.0
    github_api_max_retries: int = 2

    openrouter_api_key: str | None = None
    openrouter_primary_model: str = "openai/gpt-4.1-mini"
    openrouter_fallback_model: str = "google/gemini-2.5-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: float = 30.0
    openrouter_max_retries: int = 2

    model_config = SettingsConfigDict(
        env_file=_BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def parsed_cors_allowed_origins(self) -> tuple[str, ...]:
        origins = tuple(
            dict.fromkeys(
                origin.strip().rstrip("/")
                for origin in self.cors_allowed_origins.split(",")
                if origin.strip()
            )
        )
        for origin in origins:
            parsed = urlparse(origin)
            if (
                origin == "*"
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.params
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise RuntimeError(
                    "CORS_ALLOWED_ORIGINS must contain explicit http(s) origins"
                )
        return origins

    def validate_runtime(self) -> None:
        """Fail before startup when live-only credentials or origins are incomplete."""

        origins = self.parsed_cors_allowed_origins
        if not origins:
            raise RuntimeError("CORS_ALLOWED_ORIGINS must contain at least one origin")
        if self.app_mode == "fixture":
            return

        def is_missing(value: str | None) -> bool:
            return value is None or not value.strip() or "<" in value or ">" in value

        missing = [
            name
            for name, value in (
                ("DATABASE_URL", self.database_url),
                ("INTERNAL_HMAC_SECRET", self.internal_hmac_secret),
                ("GITHUB_APP_ID", self.github_app_id),
                ("GITHUB_APP_PRIVATE_KEY", self.github_app_private_key),
                ("SUPABASE_JWT_ISSUER", self.supabase_jwt_issuer),
            )
            if is_missing(value)
        ]
        if is_missing(self.supabase_jwt_secret) and is_missing(self.supabase_jwks_url):
            missing.append("SUPABASE_JWKS_URL or SUPABASE_JWT_SECRET")
        if missing:
            raise RuntimeError(
                "live mode configuration is incomplete: " + ", ".join(missing)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
