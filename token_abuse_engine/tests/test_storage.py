# language: Python, file: token_abuse_engine/tests/test_storage.py, runtime: Python 3.10+

from pydantic import SecretStr

from token_abuse_engine.models import Token, TokenOrigin, TokenStatus
from token_abuse_engine.storage import JsonTokenStore, MemoryTokenStore


async def test_memory_store_round_trip():
    store = MemoryTokenStore()
    token = Token(
        value=SecretStr("test-value"),
        provider="openai",
        origin=TokenOrigin.IMPORTED,
        status=TokenStatus.HEALTHY,
    )
    await store.upsert(token)
    loaded = await store.load_provider("openai")
    assert len(loaded) == 1
    assert loaded[0].fingerprint == token.fingerprint
    assert await store.delete("openai", token.fingerprint)
    assert await store.load_provider("openai") == []


async def test_json_store_round_trip(tmp_path):
    path = tmp_path / "tokens.json"
    store = JsonTokenStore(path)
    token = Token(
        value=SecretStr("persisted-value"),
        provider="anthropic",
        origin=TokenOrigin.FILE,
        status=TokenStatus.DEGRADED,
    )
    await store.upsert(token)
    loaded = await JsonTokenStore(path).load_provider("anthropic")
    assert len(loaded) == 1
    assert loaded[0].value.get_secret_value() == "persisted-value"
    assert loaded[0].status == TokenStatus.DEGRADED
    assert await store.delete("anthropic", token.fingerprint)
    assert not (await JsonTokenStore(path).load_provider("anthropic"))