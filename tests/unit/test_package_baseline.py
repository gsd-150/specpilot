import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from specpilot import __version__
from specpilot.api.app import create_app


def test_package_exposes_version() -> None:
    assert __version__ == "0.1.0"


def test_dev_extra_includes_bounded_archive_builder() -> None:
    project = tomllib.loads(
        Path("pyproject.toml").read_text(encoding="utf-8")
    )
    assert "build>=1.2,<2" in project["project"]["optional-dependencies"]["dev"]


def test_health_exposes_no_runtime_details() -> None:
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "postgres": "down",
        "mcp": "down",
    }
