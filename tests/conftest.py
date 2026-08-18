from pytest import Config


def pytest_configure(config: Config) -> None:
    config.addinivalue_line(
        "markers",
        "postgres: requires TEST_DATABASE_URL and exercises PostgreSQL migrations",
    )
