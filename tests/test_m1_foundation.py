"""Tests for auth, health, and hook utilities."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.sqlite"))
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-jwt-signing-32chars")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    # Reset settings cache
    from app.config import get_settings
    get_settings.cache_clear()

    with patch("app.services.pattern_seed.seed_canonical_patterns", return_value=None), \
         patch("app.workflows.checkpointer.init_async_checkpointer", return_value=None), \
         patch("app.workflows.checkpointer.close_async_checkpointer", return_value=None):
        from app.main import app
        with TestClient(app) as c:
            yield c

    get_settings.cache_clear()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert "checks" in body


def test_login(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body


def test_node_hook_emits_on_success():
    from app.workflows.hooks import node_hook

    @node_hook("demo_node")
    def demo(state):
        return {"ok": True}

    with patch("app.workflows.hooks._emit_sse"), patch("app.workflows.hooks._persist_event"):
        out = demo({"run_id": "r1", "project_id": "p1"})
    assert out["ok"] is True


def test_compute_cost():
    from app.services.token_tracer import compute_cost_usd
    cost = compute_cost_usd("gpt-4o", 1_000_000, 1_000_000)
    assert cost > 0
