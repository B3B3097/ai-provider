# language: Python, file: tests/test_public_api.py, runtime: Python 3.10+
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from fastapi.testclient import TestClient

from token_abuse_engine.config import (
    EngineConfig,
    GatewayConfig,
    ModelConfig,
    ProviderConfig,
    PublicApiConfig,
    RateLimitConfig,
    StorageConfig,
    load_config,
)
from token_abuse_engine.gateway import _key_matches, create_app
from token_abuse_engine.public_api import (
    InvalidApiKey,
    MemoryApiKeyStore,
    PublicApiKeyManager,
    QuotaExceeded,
    RevokedApiKey,
)

ADMIN_KEY = "admin-secret"
CONFIGURED_CLIENT_KEY = "configured-client"


def manager_config(**overrides: Any) -> PublicApiConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "store_type": "memory",
        "default_rpm": 10,
        "default_rpd": 20,
        "default_token_limit": 100,
    }
    values.update(overrides)
    return PublicApiConfig(**values)


def gateway_config(public_api: PublicApiConfig | None = None) -> EngineConfig:
    public_config = public_api or manager_config()
    return EngineConfig(
        gateway=GatewayConfig(
            api_keys=[CONFIGURED_CLIENT_KEY],
            admin_keys=[ADMIN_KEY],
            public_api=public_config,
            rate_limit=RateLimitConfig(
                rpm=public_config.default_rpm,
                rpd=public_config.default_rpd,
            ),
            docs_enabled=False,
        ),
        storage=StorageConfig(type="memory"),
    )


def auth(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


@pytest.mark.asyncio
async def test_issue_key_is_hash_only() -> None:
    manager = PublicApiKeyManager(manager_config())
    raw_key, record = await manager.issue(
        "customer-a",
        metadata={"tier": "gold"},
    )

    assert raw_key.startswith("sk-live-")
    assert record.secret_hash == manager.hash_secret(raw_key)
    public = record.public_dict()
    assert "secret_hash" not in public
    assert raw_key not in json.dumps(public)
    stored = manager.store.records[record.id]
    assert raw_key not in stored.to_json()


@pytest.mark.asyncio
async def test_authenticate_counts_request() -> None:
    manager = PublicApiKeyManager(manager_config())
    raw_key, record = await manager.issue("customer-a")

    authenticated = await manager.authenticate(raw_key)

    assert authenticated.id == record.id
    assert (await manager.usage(record.id))["requests_total"] == 1


@pytest.mark.asyncio
async def test_rpm_quota_returns_429() -> None:
    manager = PublicApiKeyManager(manager_config())
    raw_key, _ = await manager.issue("rpm", rpm_limit=1)

    await manager.authenticate(raw_key)
    with pytest.raises(QuotaExceeded) as error:
        await manager.authenticate(raw_key)

    assert error.value.status_code == 429
    assert error.value.retry_after >= 1


@pytest.mark.asyncio
async def test_rpd_quota_returns_429() -> None:
    manager = PublicApiKeyManager(manager_config())
    raw_key, _ = await manager.issue("rpd", rpm_limit=0, rpd_limit=1)

    await manager.authenticate(raw_key)
    with pytest.raises(QuotaExceeded, match="Daily request quota"):
        await manager.authenticate(raw_key)


@pytest.mark.asyncio
async def test_token_budget_is_enforced() -> None:
    manager = PublicApiKeyManager(manager_config())
    raw_key, record = await manager.issue("tokens", token_limit=5)
    await manager.authenticate(raw_key)

    await manager.record_usage(record.id, 5)

    with pytest.raises(QuotaExceeded, match="Token budget"):
        await manager.authenticate(raw_key)


@pytest.mark.asyncio
async def test_zero_limits_mean_unlimited() -> None:
    manager = PublicApiKeyManager(manager_config())
    raw_key, _ = await manager.issue(
        "unlimited",
        rpm_limit=0,
        rpd_limit=0,
        token_limit=0,
    )

    for _ in range(5):
        await manager.authenticate(raw_key)


@pytest.mark.asyncio
async def test_revoke_removes_lookup_but_keeps_record() -> None:
    manager = PublicApiKeyManager(manager_config())
    raw_key, record = await manager.issue("revoke-me")

    assert await manager.revoke(record.id) is True
    stored = await manager.store.get(record.id)

    assert stored is not None
    assert stored.status.value == "revoked"
    with pytest.raises(InvalidApiKey):
        await manager.authenticate(raw_key)


@pytest.mark.asyncio
async def test_expired_key_is_rejected() -> None:
    manager = PublicApiKeyManager(manager_config())
    raw_key, record = await manager.issue("expired", ttl_days=1)
    record.expires_at = time.time() - 1
    await manager.store.save(record)

    with pytest.raises(RevokedApiKey, match="revoked"):
        await manager.authenticate(raw_key)


def test_manager_rejects_invalid_backend() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        PublicApiKeyManager(manager_config(store_type="database"))


def test_redis_backend_requires_url() -> None:
    with pytest.raises(ValueError, match="redis_url"):
        PublicApiKeyManager(manager_config(store_type="redis", redis_url=""))


def test_memory_store_starts_empty() -> None:
    store = MemoryApiKeyStore()

    assert store.records == {}
    assert store.hashes == {}


def test_empty_keys_never_match() -> None:
    assert _key_matches("", [""]) is False
    assert _key_matches("secret", ["", "secret"]) is True


def test_empty_admin_key_cannot_access_admin_endpoint() -> None:
    config = EngineConfig(
        gateway=GatewayConfig(
            api_keys=[],
            admin_keys=[""],
            public_api=PublicApiConfig(enabled=False),
            docs_enabled=False,
        ),
        storage=StorageConfig(type="memory"),
    )

    with TestClient(create_app(config)) as client:
        response = client.get("/admin/api-keys")

    assert response.status_code == 403


def test_admin_can_issue_but_public_key_cannot() -> None:
    app = create_app(gateway_config())
    with TestClient(app) as client:
        denied = client.post(
            "/admin/api-keys",
            headers=auth(CONFIGURED_CLIENT_KEY),
            json={"name": "denied"},
        )
        issued = client.post(
            "/admin/api-keys",
            headers=auth(ADMIN_KEY),
            json={"name": "customer-a", "rpm_limit": 5},
        )

    assert denied.status_code == 403
    assert issued.status_code == 201
    assert issued.json()["api_key"].startswith("sk-live-")
    assert "secret_hash" not in issued.json()


def test_admin_list_and_revoke_are_hash_only() -> None:
    app = create_app(gateway_config())
    with TestClient(app) as client:
        issued = client.post(
            "/admin/api-keys",
            headers=auth(ADMIN_KEY),
            json={"name": "customer-a"},
        ).json()
        raw_key = issued["api_key"]
        listed = client.get("/admin/api-keys", headers=auth(ADMIN_KEY))
        deleted = client.delete(
            f"/admin/api-keys/{issued['id']}",
            headers=auth(ADMIN_KEY),
        )
        after = client.get("/v1/models", headers=auth(raw_key))

    assert listed.status_code == 200
    assert raw_key not in listed.text
    assert "secret_hash" not in listed.text
    assert deleted.status_code == 204
    assert after.status_code == 401


def test_usage_endpoint_reports_principal_counters() -> None:
    app = create_app(gateway_config())
    with TestClient(app) as client:
        issued = client.post(
            "/admin/api-keys",
            headers=auth(ADMIN_KEY),
            json={"name": "usage", "rpm_limit": 5},
        ).json()

        response = client.get(
            "/v1/usage",
            headers=auth(issued["api_key"]),
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["key_id"] == issued["id"]
    assert payload["usage"]["requests_total"] == 1
    assert payload["limits"]["tokens_total"] == 100


def test_models_include_pricing_limits_and_metadata() -> None:
    app = create_app(gateway_config())
    with TestClient(app) as client:
        provider = ProviderConfig(
            name="test",
            rate_limit=RateLimitConfig(rpm=10, tpm=100, rpd=20),
            models=[
                ModelConfig(
                    id="test-model",
                    provider_model="test-model-v1",
                    aliases=["latest"],
                    max_tokens=4096,
                    context_window=8192,
                    supports_vision=True,
                    pricing={"input": 1.5, "output": 3.0},
                    tags=["test"],
                )
            ],
        )

        async def close() -> None:
            return None

        engine = client.app.state.engine
        engine.pools["test"] = SimpleNamespace(config=provider, close=close)
        response = client.get(
            "/v1/models",
            headers=auth(CONFIGURED_CLIENT_KEY),
        )

    model = response.json()["data"][0]
    assert model["pricing"] == {"input": 1.5, "output": 3.0}
    assert model["limits"]["context_window"] == 8192
    assert model["metadata"]["aliases"] == ["latest"]
    assert "vision" in model["metadata"]["capabilities"]


def test_non_streaming_completion_records_usage() -> None:
    app = create_app(gateway_config())

    class FakePool:
        async def execute(self, body: dict[str, Any]) -> tuple[dict[str, Any], object]:
            return {
                "choices": [{"message": {"content": "pong"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            }, object()

    with TestClient(app) as client:
        engine = client.app.state.engine
        engine.resolve_provider = lambda model, requested: FakePool()  # type: ignore[method-assign]
        response = client.post(
            "/v1/chat/completions",
            headers=auth(CONFIGURED_CLIENT_KEY),
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "ping"}],
            },
        )
        usage = client.get(
            "/v1/usage",
            headers=auth(CONFIGURED_CLIENT_KEY),
        ).json()

    assert response.status_code == 200
    assert usage["usage"]["tokens_total"] == 10


def test_streaming_completion_records_usage() -> None:
    app = create_app(gateway_config())

    class FakePool:
        async def stream(self, body: dict[str, Any]) -> AsyncIterator[str]:
            assert body["stream_options"]["include_usage"] is True
            yield 'data: {"choices":[{"delta":{"content":"pong"}}]}'
            yield 'data: {"usage":{"prompt_tokens":5,"completion_tokens":2}}'
            yield "data: [DONE]"

    with TestClient(app) as client:
        engine = client.app.state.engine
        engine.resolve_provider = lambda model, requested: FakePool()  # type: ignore[method-assign]
        response = client.post(
            "/v1/chat/completions",
            headers=auth(CONFIGURED_CLIENT_KEY),
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "ping"}],
                "stream": True,
            },
        )
        usage = client.get(
            "/v1/usage",
            headers=auth(CONFIGURED_CLIENT_KEY),
        ).json()

    assert response.status_code == 200
    assert "[DONE]" in response.text
    assert usage["usage"]["tokens_total"] == 7


def test_configured_client_key_uses_manager_quotas() -> None:
    app = create_app(gateway_config(manager_config(default_rpm=1)))
    with TestClient(app) as client:
        first = client.get("/v1/models", headers=auth(CONFIGURED_CLIENT_KEY))
        second = client.get("/v1/models", headers=auth(CONFIGURED_CLIENT_KEY))

    assert first.status_code == 200
    assert second.status_code == 429
    assert "Retry-After" in second.headers


def test_production_config_accepts_url_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://api.example.com")

    config = load_config("config.production.yaml")

    assert config.gateway.cors_origins == ["https://api.example.com"]


def test_cors_preflight_does_not_require_api_key() -> None:
    app = create_app(gateway_config())

    with TestClient(app) as client:
        response = client.options(
            "/v1/models",
            headers={
                "Origin": "https://client.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
