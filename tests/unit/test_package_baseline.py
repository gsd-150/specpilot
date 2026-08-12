from fastapi.testclient import TestClient

from specpilot import __version__
from specpilot.api.app import create_app


def test_package_exposes_version() -> None:
    assert __version__ == "0.1.0"


def test_health_exposes_no_runtime_details() -> None:
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "postgres": "down",
        "mcp": "down",
    }
