# language: Python, file: token_abuse_engine/tests/test_discovery.py, runtime: Python 3.10+

from collections.abc import AsyncIterator, Mapping
from typing import Any

from pydantic import SecretStr

from token_abuse_engine.config import (
    EngineConfig,
    ModelConfig,
    ProviderConfig,
    StorageConfig,
)
from token_abuse_engine.gateway import GatewayEngine
from token_abuse_engine.models import Token, TokenOrigin, TokenStatus
from token_abuse_engine.pool import LeastUsedStrategy, TokenPool
from token_abuse_engine.storage import MemoryTokenStore


class DiscoveryAdapter:
    async def complete(
        self, token: Token, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {}

    async def stream(
        self, token: Token, payload: Mapping[str, Any]
    ) -> AsyncIterator[str]:
        yield "data: {}"

    async def discover_models(self, token: Token) -> list[ModelConfig]:
        return [
            ModelConfig(id="configured", provider_model="configured"),
            ModelConfig(id="discovered", provider_model="discovered"),
        ]

    async def healthcheck(self, token: Token) -> bool:
        return True

    async def close(self) -> None:
        return None


async def test_discovery_merges_new_models_without_duplicates():
    provider = ProviderConfig(
        name="fake",
        base_url="https://example.invalid",
        model_discovery=False,
        models=[
            ModelConfig(id="configured", provider_model="configured")
        ],
    )
    pool = TokenPool(
        provider,
        DiscoveryAdapter(),
        MemoryTokenStore(),
        LeastUsedStrategy(),
    )
    await pool.add_token(
        Token(
            value=SecretStr("credential"),
            provider="fake",
            origin=TokenOrigin.IMPORTED,
            status=TokenStatus.HEALTHY,
        )
    )
    engine = GatewayEngine(EngineConfig(storage=StorageConfig(type="memory")))
    engine.pools["fake"] = pool

    first = await engine.discover_models("fake")
    second = await engine.discover_models("fake")

    assert first == {"added": {"fake": 1}, "errors": {}}
    assert second == {"added": {"fake": 0}, "errors": {}}
    assert [model.id for model in provider.models] == [
        "configured",
        "discovered",
    ]
    await engine.shutdown()