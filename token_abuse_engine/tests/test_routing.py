# language: Python, file: token_abuse_engine/tests/test_routing.py, runtime: Python 3.10+

from fastapi.testclient import TestClient

from token_abuse_engine.config import (
    EngineConfig,
    ModelConfig,
    ProviderConfig,
    StorageConfig,
)
from token_abuse_engine.gateway import create_app


def test_model_routing_matches_glob_patterns():
    config = EngineConfig(
        storage=StorageConfig(type="memory"),
        providers=[
            ProviderConfig(
                name="openai",
                base_url="https://openai.invalid",
                models=[
                    ModelConfig(id="gpt-known", provider_model="gpt-known")
                ],
            ),
            ProviderConfig(
                name="anthropic",
                base_url="https://anthropic.invalid",
                models=[
                    ModelConfig(
                        id="claude-known",
                        provider_model="claude-known",
                    )
                ],
            ),
        ],
    )
    config.model_routing = {
        "gpt-*": ["openai"],
        "claude-*": ["anthropic"],
    }
    app = create_app(config)

    with TestClient(app) as client:
        engine = client.app.state.engine
        assert engine.resolve_provider("claude-new", None).config.name == (
            "anthropic"
        )
        assert engine.resolve_provider("gpt-new", None).config.name == "openai"
        assert engine.resolve_provider(
            "claude-known", None
        ).config.name == "anthropic"