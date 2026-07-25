"""Phase 2a — GET /health."""

import pytest

pytest.importorskip("fastapi", reason="needs the [api] extra")

from conftest import make_client, make_settings


def test_health_reports_ok_and_configuration(client):
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["prompt_version"] == "v1"
    assert body["provider"] == "mock"
    assert body["model"] == "mock-model"
    assert body["version"]


def test_health_never_exposes_credentials(monkeypatch):
    """Only the presence of a key may be reported — never the key itself."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-value-should-never-appear")
    client = make_client(make_settings(provider="openrouter", model="google/gemini-3.1-flash-lite"))

    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["provider_configured"] is True
    assert "secret-value-should-never-appear" not in response.text
    assert not any("key" in field.lower() for field in body)


def test_health_reports_missing_credentials(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = make_client(make_settings(provider="openrouter"))

    assert client.get("/health").json()["provider_configured"] is False
