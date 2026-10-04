# language: Python, file: token_abuse_engine/tests/test_adapters.py, runtime: Python 3.10+

import json

import httpx
from pydantic import SecretStr

from token_abuse_engine.adapters import (
    AnthropicAdapter,
    GeminiAdapter,
    OpenAICompatibleAdapter,
)
from token_abuse_engine.config import AuthConfig, AuthType, ModelConfig, ProviderConfig
from token_abuse_engine.models import Token, TokenStatus


def token() -> Token:
    return Token(
        value=SecretStr("secret"),
        provider="test",
        status=TokenStatus.HEALTHY,
    )


async def replace_client(adapter, handler):
    await adapter.client.aclose()
    adapter.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=adapter.config.base_url.rstrip("/") + "/",
    )


async def test_openai_adapter_uses_v1_path_and_maps_model():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "response"})

    config = ProviderConfig(
        name="openai",
        base_url="https://api.openai.com",
        models=[
            ModelConfig(id="alias", provider_model="provider-model")
        ],
    )
    adapter = OpenAICompatibleAdapter(config)
    await replace_client(adapter, handler)

    result = await adapter.complete(
        token(),
        {"model": "alias", "messages": [{"role": "user", "content": "x"}]},
    )
    await adapter.close()

    assert result["id"] == "response"
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret"
    assert captured["body"]["model"] == "provider-model"


async def test_anthropic_adapter_normalizes_messages_and_response():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("x-api-key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg-1",
                "model": "claude-test",
                "content": [{"type": "text", "text": "answer"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 3},
            },
        )

    config = ProviderConfig(
        name="anthropic",
        base_url="https://api.anthropic.com",
        auth=AuthConfig(type=AuthType.API_KEY, header_name="x-api-key"),
    )
    adapter = AnthropicAdapter(config)
    await replace_client(adapter, handler)

    result = await adapter.complete(
        token(),
        {
            "model": "claude-test",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "question"},
            ],
            "max_tokens": 64,
        },
    )
    await adapter.close()

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["key"] == "secret"
    assert captured["body"]["system"] == "system"
    assert result["choices"][0]["message"]["content"] == "answer"
    assert result["usage"]["total_tokens"] == 5


async def test_gemini_adapter_uses_v1beta_and_normalizes_response():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "responseId": "gemini-response",
                "candidates": [
                    {
                        "content": {"parts": [{"text": "answer"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 4,
                    "candidatesTokenCount": 5,
                    "totalTokenCount": 9,
                },
            },
        )

    config = ProviderConfig(
        name="google",
        base_url="https://generativelanguage.googleapis.com",
        auth=AuthConfig(type=AuthType.QUERY_PARAM, query_param="key"),
    )
    adapter = GeminiAdapter(config)
    await replace_client(adapter, handler)

    result = await adapter.complete(
        token(),
        {
            "model": "gemini-test",
            "messages": [{"role": "user", "content": "question"}],
        },
    )
    await adapter.close()

    assert captured["url"].startswith(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-test:generateContent?key=secret"
    )
    assert captured["body"]["contents"][0]["role"] == "user"
    assert result["choices"][0]["message"]["content"] == "answer"
    assert result["usage"]["total_tokens"] == 9