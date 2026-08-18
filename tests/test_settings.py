import pytest

from alignment_memory.settings import Settings


def test_settings_read_environment_without_credentials(monkeypatch) -> None:
    monkeypatch.setenv("APP_NAME", "Alignment Memory Test")
    monkeypatch.setenv("APP_MODE", "live")

    settings = Settings(_env_file=None)

    assert settings.app_name == "Alignment Memory Test"
    assert settings.app_mode == "live"


def test_fixture_runtime_accepts_no_external_credentials() -> None:
    Settings(app_mode="fixture", _env_file=None).validate_runtime()


def test_live_runtime_fails_fast_with_named_missing_credentials() -> None:
    with pytest.raises(RuntimeError) as error:
        Settings(app_mode="live", _env_file=None).validate_runtime()

    message = str(error.value)
    assert "DATABASE_URL" in message
    assert "INTERNAL_HMAC_SECRET" in message
    assert "GITHUB_APP_PRIVATE_KEY" in message
    assert "SUPABASE_JWKS_URL or SUPABASE_JWT_SECRET" in message


@pytest.mark.parametrize("origins", ["*", "localhost:5173", "file:///tmp/demo"])
def test_cors_requires_explicit_http_origins(origins: str) -> None:
    with pytest.raises(RuntimeError, match="explicit http"):
        Settings(
            app_mode="fixture",
            cors_allowed_origins=origins,
            _env_file=None,
        ).validate_runtime()
