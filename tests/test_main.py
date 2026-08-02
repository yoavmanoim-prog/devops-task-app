import importlib

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY


def _unregister_http_collectors() -> None:
    """Drop the instrumentator's collectors from the global registry.

    prometheus_client registers into a process-wide default registry, but the
    fixture below reloads src.main for every test to pick up patched env vars.
    Without this, the second reload finds the metric names already taken, the
    new app's middleware silently ends up with no collectors, and /metrics
    serves HELP/TYPE headers with zero samples - which looks like "the endpoint
    works" while measuring nothing.
    """
    for collector in list(getattr(REGISTRY, "_collector_to_names", {})):
        names = REGISTRY._collector_to_names.get(collector, [])
        if any(name.startswith("http_") for name in names):
            REGISTRY.unregister(collector)


@pytest.fixture
def client(monkeypatch, tmp_path):
    secret_path = tmp_path / "message"
    monkeypatch.setenv("SECRET_FILE_PATH", str(secret_path))
    monkeypatch.setenv("APP_ENV", "test")

    from src import main

    _unregister_http_collectors()
    importlib.reload(main)
    return TestClient(main.app), secret_path


def test_healthz(client):
    test_client, _ = client
    response = test_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz(client):
    test_client, _ = client
    response = test_client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_without_secret_file(client):
    test_client, secret_path = client
    assert not secret_path.exists()

    response = test_client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["app_env"] == "test"
    assert body["secret_message"] is None


def test_root_with_secret_file(client):
    test_client, secret_path = client
    secret_path.write_text("hello from secrets manager\n")

    response = test_client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["app_env"] == "test"
    assert body["secret_message"] == "hello from secrets manager"


def test_docs_available(client):
    test_client, _ = client
    response = test_client.get("/docs")
    assert response.status_code == 200


def test_metrics_endpoint_serves_prometheus_data(client):
    """The gitops chart's ServiceMonitor scrapes /metrics.

    Guards the pairing, not just the endpoint: a ServiceMonitor pointing at a
    path that 404s is a scrape job that silently collects nothing, so the
    monitor is only worth having while this test passes.
    """
    test_client, _ = client
    test_client.get("/")

    response = test_client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    # the real request above must be counted, with its handler label
    assert 'http_requests_total{handler="/",method="GET",status="2xx"}' in response.text


def test_metrics_excludes_probe_endpoints(client):
    """Probes must not pollute the metrics.

    kubelet hits /healthz and /readyz every few seconds. Counted, they would
    dominate the request totals and the latency histograms would describe the
    probes rather than real traffic.
    """
    test_client, _ = client
    test_client.get("/healthz")
    test_client.get("/readyz")

    body = test_client.get("/metrics").text

    assert "/healthz" not in body
    assert "/readyz" not in body
