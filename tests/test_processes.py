"""Tests for Phase 6 process monitoring."""

import os

import pytest

from app import create_app
from app.monitoring.processes import ProcessSampler, list_processes, query_processes

SAMPLE = [
    {"pid": 1, "name": "systemd", "username": "root",
     "cpu_percent": 1.0, "memory_percent": 0.5, "status": "sleeping"},
    {"pid": 20, "name": "python3", "username": "vinay",
     "cpu_percent": 12.0, "memory_percent": 4.0, "status": "running"},
    {"pid": 300, "name": "nginx", "username": "www-data",
     "cpu_percent": 2.0, "memory_percent": 1.0, "status": "sleeping"},
    {"pid": 4000, "name": "mysqld", "username": "mysql",
     "cpu_percent": 8.0, "memory_percent": 12.0, "status": "sleeping"},
]


class FakeSampler:
    def __init__(self, processes=None, updated="2026-01-01T00:00:00+00:00"):
        self._processes = processes if processes is not None else []
        self._updated = updated

    def get(self):
        return list(self._processes), self._updated


@pytest.fixture
def app(tmp_path):
    return create_app(
        {"TESTING": True, "DATABASE_PATH": str(tmp_path / "p.db"), "DEFAULT_SERVICES": []},
        start_collector=False,
    )


def test_list_processes_shape_and_includes_self():
    processes = list_processes()
    assert any(p["pid"] == os.getpid() for p in processes)
    for p in processes:
        assert set(p) == {"pid", "name", "username", "cpu_percent",
                          "memory_percent", "status"}
        assert p["cpu_percent"] >= 0
        assert 0 <= p["memory_percent"] <= 100


def test_query_sorts_by_cpu_descending():
    result = query_processes(SAMPLE, sort="cpu", order="desc")
    assert [p["name"] for p in result] == ["python3", "mysqld", "nginx", "systemd"]


def test_query_sorts_by_name_ascending_and_by_memory():
    by_name = query_processes(SAMPLE, sort="name", order="asc")
    assert [p["name"] for p in by_name] == ["mysqld", "nginx", "python3", "systemd"]
    by_mem = query_processes(SAMPLE, sort="memory", order="desc")
    assert by_mem[0]["name"] == "mysqld"


def test_query_search_by_name_user_and_pid():
    assert [p["name"] for p in query_processes(SAMPLE, search="NGINX")] == ["nginx"]
    assert [p["name"] for p in query_processes(SAMPLE, search="vinay")] == ["python3"]
    assert [p["name"] for p in query_processes(SAMPLE, search="4000")] == ["mysqld"]
    assert query_processes(SAMPLE, search="no-such-thing") == []
    with pytest.raises(ValueError):
        query_processes(SAMPLE, sort="bogus")


def test_processes_api_sort_search_limit(app):
    app.extensions["process_sampler"] = FakeSampler(SAMPLE)
    client = app.test_client()

    body = client.get("/api/processes?sort=cpu&order=desc&limit=2").get_json()
    assert body["total_processes"] == 4
    assert body["matched"] == 4
    assert body["returned"] == 2
    assert [p["name"] for p in body["processes"]] == ["python3", "mysqld"]

    searched = client.get("/api/processes?search=nginx").get_json()
    assert searched["matched"] == 1 and searched["returned"] == 1
    assert searched["processes"][0]["pid"] == 300


def test_processes_api_validates_input(app):
    app.extensions["process_sampler"] = FakeSampler(SAMPLE)
    client = app.test_client()
    bad_queries = ["sort=bogus", "order=sideways", "limit=0", "limit=abc",
                   "limit=9999", "search=" + "x" * 101]
    for query in bad_queries:
        response = client.get("/api/processes?" + query)
        assert response.status_code == 400, query
        assert response.get_json()["error"] == "bad_request"


def test_processes_api_returns_503_before_first_sample(app):
    # Real sampler, never refreshed: no data yet.
    response = app.test_client().get("/api/processes")
    assert response.status_code == 503
    assert response.get_json()["error"] == "warming_up"


def test_sampler_refresh_populates_data():
    sampler = ProcessSampler()
    assert sampler.get() == ([], None)
    sampler.refresh()
    processes, updated = sampler.get()
    assert processes and updated is not None
