# language: Python, file: token_abuse_engine/tests/test_token_sources.py, runtime: Python 3.10+

import json

from token_abuse_engine.config import TokenFactoryConfig, TokenSource
from token_abuse_engine.models import TokenOrigin
from token_abuse_engine.token_sources import TokenSourceLoader


def test_loader_reads_env_and_file_and_deduplicates(
    tmp_path,
    monkeypatch,
):
    token_file = tmp_path / "keys.json"
    token_file.write_text(
        json.dumps(["file-token", "shared-token"]),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_PROVIDER_TOKEN_1", "env-token")
    monkeypatch.setenv("TEST_PROVIDER_TOKEN_2", "shared-token")
    config = TokenFactoryConfig(
        enabled=True,
        provider="test",
        sources=[TokenSource.ENV, TokenSource.FILE],
        env_prefix="TEST_PROVIDER_TOKEN_",
        file_path=str(token_file),
    )

    tokens = TokenSourceLoader().load(config)
    values = [token.value.get_secret_value() for token in tokens]
    origins = {token.value.get_secret_value(): token.origin for token in tokens}

    assert set(values) == {"env-token", "file-token", "shared-token"}
    assert len(tokens) == 3
    assert origins["env-token"] == TokenOrigin.ENV
    assert origins["file-token"] == TokenOrigin.FILE
    assert origins["shared-token"] == TokenOrigin.ENV