import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    secret_path = tmp_path / "message"
    monkeypatch.setenv("SECRET_FILE_PATH", str(secret_path))
    monkeypatch.setenv("APP_ENV", "test")

    from src import main

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
