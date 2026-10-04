# language: Python, file: token_abuse_engine/tests/test_pool.py, runtime: Python 3.10+

from collections.abc import AsyncIterator, Mapping
from typing import Any

from pydantic import SecretStr

from token_abuse_engine.adapters import ProviderError
from token_abuse_engine.config import ProviderConfig, RetryConfig
from token_abuse_engine.models import Token, TokenOrigin, TokenStatus
from token_abuse_engine.pool import RoundRobinStrategy, TokenPool
from token_abuse_engine.storage import MemoryTokenStore


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(
        self,
        token: Token,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        value = token.value.get_secret_value()
        self.calls.append(value)
        if value == "limited":
            raise ProviderError(
                "test",
                429,
                "rate limited",
                retry_after=0,
            )
        return {
            "id": "ok",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                }
            ],
            "usage": {"total_tokens": 4},
        }

    async def stream(
        self,
        token: Token,
        payload: Mapping[str, Any],
    ) -> AsyncIterator[str]:
        yield "data: {\"content\":\"ok\"}"

    async def discover_models(self, token: Token) -> list[Any]:
        return []

    async def healthcheck(self, token: Token) -> bool:
        return True

    async def close(self) -> None:
        return None

    def _usage_tokens(self, payload: Mapping[str, Any]) -> int:
        return int(payload.get("usage", {}).get("total_tokens", 0))


async def test_pool_retries_with_another_token():
    adapter = FakeAdapter()
    provider = ProviderConfig(
        name="test",
        base_url="https://example.invalid",
        retry=RetryConfig(max_attempts=2, base_delay=0, max_delay=0),
    )
    pool = TokenPool(provider, adapter, MemoryTokenStore(), RoundRobinStrategy())
    await pool.add_token(
        Token(
            value=SecretStr("limited"),
            provider="test",
            origin=TokenOrigin.IMPORTED,
            status=TokenStatus.HEALTHY,
        )
    )
    await pool.add_token(
        Token(
            value=SecretStr("healthy"),
            provider="test",
            origin=TokenOrigin.IMPORTED,
            status=TokenStatus.HEALTHY,
        )
    )

    result, selected = await pool.execute(
        {"model": "test", "messages": [{"role": "user", "content": "ping"}]}
    )
    assert result["id"] == "ok"
    assert selected.value.get_secret_value() == "healthy"
    assert adapter.calls == ["limited", "healthy"]