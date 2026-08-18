from fastapi.testclient import TestClient

from alignment_memory.interfaces.api.main import create_app
from alignment_memory.settings import Settings


def test_healthz_reports_local_mode() -> None:
    app = create_app(Settings(app_mode="fixture", _env_file=None))

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "alignment-memory",
        "mode": "fixture",
    }


def test_cors_preflight_allows_only_configured_web_origin() -> None:
    app = create_app(
        Settings(
            app_mode="fixture",
            cors_allowed_origins="https://web.example.com",
            _env_file=None,
        )
    )

    with TestClient(app) as client:
        allowed = client.options(
            "/api/v1/repositories",
            headers={
                "Origin": "https://web.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        rejected = client.options(
            "/api/v1/repositories",
            headers={
                "Origin": "https://other.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://web.example.com"
    assert rejected.status_code == 400
