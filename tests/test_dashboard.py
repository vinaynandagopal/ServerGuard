"""Tests for the Phase 5 dashboard."""

import pytest

from app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(
        {"TESTING": True, "DATABASE_PATH": str(tmp_path / "t.db")},
        start_collector=False,
    )
    return app.test_client()


def test_index_serves_dashboard_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert b"SERVERGUARD" in response.data
    assert b'id="cpu-chart"' in response.data


@pytest.mark.parametrize(
    "path",
    ["/static/css/style.css", "/static/js/dashboard.js", "/static/js/chart.umd.min.js"],
)
def test_static_assets_are_served(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert len(response.data) > 100  # catches an empty/failed Chart.js download
    response.close()
