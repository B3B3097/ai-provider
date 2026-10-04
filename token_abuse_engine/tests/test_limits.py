# language: Python, file: token_abuse_engine/tests/test_limits.py, runtime: Python 3.10+

from collections.abc import AsyncIterator, Mapping
from typing import Any

from pydantic import SecretStr

from token_abuse_engine.config import ProviderConfig, RateLimitConfig
from token_abuse_engine.models import Token, TokenOrigin, TokenStatus
from token_abuse_engine.pool import (
    LeastUsedStrategy,
    NoHealthyTokenError,
    TokenPool,
)
from token_abuse_engine.storage import MemoryTokenStore


class SuccessfulAdapter:
    async def complete(
        self,
        token: Token,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {"usage": {"total_tokens": 3}}

    async def stream(
        self,
        token: Token,
        payload: Mapping[str, Any],
    ) -> AsyncIterator[str]:
        yield "data: {}"

    async def discover_models(self, token: Token) -> list[Any]:
        return []

    async def healthcheck(self, token: Token) -> bool:
        return True

    async def close(self) -> None:
        return None

    def _usage_tokens(self, payload: Mapping[str, Any]) -> int:
        return int(payload.get("usage", {}).get("total_tokens", 0))


async def test_pool_enforces_request_and_token_rate_limits():
    provider = ProviderConfig(
        name="limited",
        base_url="https://example.invalid",
        rate_limit=RateLimitConfig(rpm=1, tpm=5, rpd=10),
    )
    pool = TokenPool(
        provider,
        SuccessfulAdapter(),
        MemoryTokenStore(),
        LeastUsedStrategy(),
    )
    token = Token(
        value=SecretStr("credential"),
        provider="limited",
        origin=TokenOrigin.IMPORTED,
        status=TokenStatus.HEALTHY,
    )
    await pool.add_token(token)

    result, selected = await pool.execute(
        {"model": "test", "messages": [{"role": "user", "content": "x"}]}
    )
    assert result["usage"]["total_tokens"] == 3
    assert selected is token
    assert token.rpm_used == 1
    assert token.tpm_used == 3
    assert pool.available_tokens() == []

    try:
        await pool.execute(
            {
                "model": "test",
                "messages": [{"role": "user", "content": "x"}],
            }
        )
    except NoHealthyTokenError:
        pass
    else:
        raise AssertionError("rate-limited token was selected again")