# language: Python, file: token_abuse_engine/tests/test_gateway.py, runtime: Python 3.10+

import pytest
from fastapi.testclient import TestClient

from token_abuse_engine.config import (
    EngineConfig,
    ModelConfig,
    ProviderConfig,
    RateLimitConfig,
    StorageConfig,
)
from token_abuse_engine.gateway import create_app


@pytest.fixture
def app():
    provider = ProviderConfig(
        name="test",
        base_url="https://example.invalid",
        models=[
            ModelConfig(
                id="test-model",
                provider_model="test-model-v1",
            )
        ],
        rate_limit=RateLimitConfig(rpm=10, tpm=1000),
    )
    config = EngineConfig(
        storage=StorageConfig(type="memory"),
        providers=[provider],
    )
    config.gateway.api_keys = ["client-key"]
    config.gateway.admin_keys = ["admin-key"]
    return create_app(config)


def test_health_is_public_and_models_require_auth(app):
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/v1/models").status_code == 401
        response = client.get(
            "/v1/models",
            headers={"Authorization": "Bearer client-key"},
        )
        assert response.status_code == 200
        assert response.json()["data"][0]["id"] == "test-model"


def test_admin_can_import_and_delete_token(app):
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer admin-key"}
        response = client.post(
            "/admin/tokens",
            headers=headers,
            json={"provider": "test", "value": "local-test-token"},
        )
        assert response.status_code == 201
        fingerprint = response.json()["fingerprint"]

        stats = client.get(
            "/health",
        ).json()["providers"]["test"]
        assert stats["total"] == 1

        deleted = client.delete(
            f"/admin/tokens/test/{fingerprint}",
            headers=headers,
        )
        assert deleted.status_code == 204


def test_client_key_cannot_use_admin_route(app):
    with TestClient(app) as client:
        response = client.post(
            "/admin/tokens",
            headers={"Authorization": "Bearer client-key"},
            json={"provider": "test", "value": "not-allowed"},
        )
        assert response.status_code == 403