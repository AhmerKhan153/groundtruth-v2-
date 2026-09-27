"""Endpoint auth and wiring; the flows themselves are covered in test_handlers."""

import pytest
from fastapi.testclient import TestClient

import main
from groundtruth import handlers


@pytest.fixture
def client(monkeypatch):
    calls = []
    monkeypatch.setattr(handlers, "source_job", lambda force=False: calls.append(("source", force)) or {"status": "sent"})
    monkeypatch.setattr(handlers, "handle_update", lambda update: calls.append(("update", update)))
    c = TestClient(main.app)
    c.calls = calls
    return c


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_source_requires_job_secret(client):
    assert client.post("/jobs/source").status_code == 401
    assert client.post("/jobs/source", headers={"X-Job-Secret": "wrong"}).status_code == 401
    assert client.calls == []


def test_source_with_secret(client):
    r = client.post("/jobs/source?force=true", headers={"X-Job-Secret": "test-job-secret"})
    assert r.status_code == 200 and client.calls == [("source", True)]


def test_telegram_requires_webhook_secret(client):
    update = {"update_id": 1}
    assert client.post("/telegram", json=update).status_code == 401
    r = client.post("/telegram", json=update,
                    headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret"})
    assert r.status_code == 200 and client.calls == [("update", update)]


def test_unset_secret_refuses_everything(monkeypatch, client):
    monkeypatch.setattr(main, "JOB_SECRET", None)
    assert client.post("/jobs/source", headers={"X-Job-Secret": ""}).status_code == 401
