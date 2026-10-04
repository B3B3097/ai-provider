# language: Python, file: token_abuse_engine/adapters.py, runtime: Python 3.11+
# Provides OpenAI-compatible, Anthropic, Gemini, and Azure adapters over authorized API credentials.

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import quote

import httpx

from .config import AuthType, ModelConfig, ProviderConfig
from .models import Token


class ProviderError(RuntimeError):
    def __init__(
        self,
        provider: str,
        status_code: int,
        message: str,
        *,
        retry_after: float | None = None,
        request_id: str | None = None,
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        self.retry_after = retry_after
        self.request_id = request_id
        super().__init__(f"{provider} returned {status_code}: {message}")


class ProviderTimeout(ProviderError):
    def __init__(self, provider: str, message: str = "request timed out") -> None:
        super().__init__(provider, 408, message)


class BaseProviderAdapter(ABC):
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(config.timeout, connect=config.connect_timeout),
            follow_redirects=True,
            headers={"Accept": "application/json", **config.headers},
            cookies=config.cookies.copy(),
        )

    async def close(self) -> None:
        await self.client.aclose()

    @abstractmethod
    async def complete(self, token: Token, payload: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def stream(
        self,
        token: Token,
        payload: Mapping[str, Any],
    ) -> AsyncIterator[str]:
        raise NotImplementedError
        yield ""

    @abstractmethod
    async def discover_models(self, token: Token) -> list[ModelConfig]:
        raise NotImplementedError

    async def healthcheck(self, token: Token) -> bool:
        try:
            await self.discover_models(token)
            return True
        except ProviderError:
            return False

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return path.lstrip("/")

    def _auth(
        self,
        token: Token,
        *,
        query: dict[str, Any] | None = None,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        headers = {**self.config.auth.custom_headers}
        params = dict(query or {})
        value = token.value.get_secret_value()
        auth_type = self.config.auth.type

        if auth_type == AuthType.NONE:
            pass
        elif auth_type == AuthType.BEARER:
            headers[self.config.auth.header_name] = (
                f"{self.config.auth.prefix}{value}"
            ).strip()
        elif auth_type == AuthType.API_KEY:
            headers[self.config.auth.header_name] = value
        elif auth_type == AuthType.CUSTOM_HEADER:
            headers[self.config.auth.header_name] = value
        elif auth_type == AuthType.COOKIE:
            headers["Cookie"] = (
                f"{self.config.auth.cookie_name}={value}"
            )
        elif auth_type == AuthType.QUERY_PARAM:
            params[self.config.auth.query_param or "key"] = value
        elif auth_type == AuthType.OAUTH2:
            headers["Authorization"] = f"Bearer {value}"
        elif auth_type == AuthType.JWT:
            headers["Authorization"] = f"Bearer {value}"
        else:
            raise ValueError(f"Unsupported auth type: {auth_type}")

        return headers, params

    def _model_name(self, model_id: str) -> str:
        for model in self.config.models:
            if model_id in {model.id, model.provider_model, *model.aliases}:
                return model.provider_model
        return model_id

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            message = response.text[:1000]
        except httpx.ResponseNotRead:
            message = response.reason_phrase or "streaming request failed"
        try:
            payload = response.json()
            error = payload.get("error", payload)
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("code") or message)
        except (ValueError, httpx.ResponseNotRead):
            pass

        retry_after: float | None = None
        raw_retry_after = response.headers.get("retry-after")
        if raw_retry_after:
            try:
                retry_after = float(raw_retry_after)
            except ValueError:
                retry_after = None

        raise ProviderError(
            self.config.name,
            response.status_code,
            message,
            retry_after=retry_after,
            request_id=response.headers.get("x-request-id"),
        )

    @staticmethod
    def _usage_tokens(payload: Mapping[str, Any]) -> int:
        usage = payload.get("usage", {})
        if not isinstance(usage, Mapping):
            return 0
        return int(
            usage.get("total_tokens")
            or (
                int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
                + int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            )
        )


class OpenAICompatibleAdapter(BaseProviderAdapter):
    chat_path = "/v1/chat/completions"
    models_path = "/v1/models"

    async def complete(
        self,
        token: Token,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        body = dict(payload)
        body["model"] = self._model_name(str(body.get("model", "")))
        headers, params = self._auth(token)
        response = await self.client.post(
            self._url(self.config.custom_endpoints.get("chat", self.chat_path)),
            headers=headers,
            params=params,
            json=body,
        )
        self._raise_for_status(response)
        return response.json()

    async def stream(
        self,
        token: Token,
        payload: Mapping[str, Any],
    ) -> AsyncIterator[str]:
        body = dict(payload)
        body["model"] = self._model_name(str(body.get("model", "")))
        body["stream"] = True
        headers, params = self._auth(token)
        headers["Accept"] = "text/event-stream"

        async with self.client.stream(
            "POST",
            self._url(self.config.custom_endpoints.get("chat", self.chat_path)),
            headers=headers,
            params=params,
            json=body,
        ) as response:
            self._raise_for_status(response)
            async for line in response.aiter_lines():
                if line:
                    yield line

    async def discover_models(self, token: Token) -> list[ModelConfig]:
        known = {model.id: model for model in self.config.models}
        headers, params = self._auth(token)
        response = await self.client.get(
            self._url(self.config.custom_endpoints.get("models", self.models_path)),
            headers=headers,
            params=params,
        )
        if not response.is_success:
            if self.config.models:
                return list(self.config.models)
            self._raise_for_status(response)

        payload = response.json()
        rows = payload.get("data", payload.get("models", []))
        discovered: list[ModelConfig] = list(self.config.models)
        known_remote = {item.provider_model for item in discovered}

        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                remote_id = str(row.get("id") or row.get("name") or "")
                if not remote_id or remote_id in known_remote:
                    continue
                discovered.append(
                    ModelConfig(
                        id=remote_id,
                        provider_model=remote_id,
                    )
                )
                known_remote.add(remote_id)

        return discovered or list(known.values())


class AnthropicAdapter(BaseProviderAdapter):
    async def complete(
        self,
        token: Token,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        source = dict(payload)
        source["model"] = self._model_name(str(source.get("model", "")))
        messages = list(source.pop("messages", []))
        system_parts: list[str] = []
        normalized_messages: list[dict[str, Any]] = []

        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            if role == "system":
                system_parts.append(str(content))
            else:
                normalized_messages.append(
                    {"role": role, "content": content}
                )

        body: dict[str, Any] = {
            "model": source["model"],
            "messages": normalized_messages,
            "max_tokens": int(source.get("max_tokens", 4096)),
        }
        for key in ("temperature", "top_p", "stop_sequences", "tools"):
            if key in source:
                body[key] = source[key]
        if system_parts:
            body["system"] = "\n\n".join(system_parts)

        headers = {
            "x-api-key": token.value.get_secret_value(),
            "anthropic-version": "2023-06-01",
            **self.config.headers,
        }
        response = await self.client.post(
            self._url(self.config.custom_endpoints.get("chat", "/v1/messages")),
            headers=headers,
            json=body,
        )
        self._raise_for_status(response)
        return self.normalize(response.json())

    async def stream(
        self,
        token: Token,
        payload: Mapping[str, Any],
    ) -> AsyncIterator[str]:
        source = dict(payload)
        source["model"] = self._model_name(str(source.get("model", "")))
        body: dict[str, Any] = {
            "model": source["model"],
            "messages": list(source.pop("messages", [])),
            "max_tokens": int(source.get("max_tokens", 4096)),
            "stream": True,
        }
        headers = {
            "x-api-key": token.value.get_secret_value(),
            "anthropic-version": "2023-06-01",
            **self.config.headers,
        }
        async with self.client.stream(
            "POST",
            self._url(self.config.custom_endpoints.get("chat", "/v1/messages")),
            headers=headers,
            json=body,
        ) as response:
            self._raise_for_status(response)
            async for line in response.aiter_lines():
                if line:
                    yield line

    async def discover_models(self, token: Token) -> list[ModelConfig]:
        headers = {
            "x-api-key": token.value.get_secret_value(),
            "anthropic-version": "2023-06-01",
        }
        response = await self.client.get(
            self._url(self.config.custom_endpoints.get("models", "/v1/models")),
            headers=headers,
        )
        if not response.is_success:
            if self.config.models:
                return list(self.config.models)
            self._raise_for_status(response)

        rows = response.json().get("data", [])
        discovered = list(self.config.models)
        known_remote = {model.provider_model for model in discovered}
        for row in rows:
            remote_id = str(row.get("id", ""))
            if remote_id and remote_id not in known_remote:
                discovered.append(
                    ModelConfig(id=remote_id, provider_model=remote_id)
                )
        return discovered

    @staticmethod
    def normalize(payload: dict[str, Any]) -> dict[str, Any]:
        text_parts: list[str] = []
        for block in payload.get("content", []):
            if isinstance(block, Mapping) and block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
        usage = payload.get("usage", {})
        return {
            "id": payload.get("id"),
            "object": "chat.completion",
            "created": payload.get("created"),
            "model": payload.get("model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "".join(text_parts)},
                    "finish_reason": payload.get("stop_reason"),
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": int(usage.get("input_tokens", 0))
                + int(usage.get("output_tokens", 0)),
            },
        }


class GeminiAdapter(BaseProviderAdapter):
    async def complete(
        self,
        token: Token,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        source = dict(payload)
        model = self._model_name(str(source.pop("model", ""))).removeprefix("models/")
        messages = list(source.pop("messages", []))
        contents: list[dict[str, Any]] = []
        system_parts: list[str] = []

        for message in messages:
            role = str(message.get("role", "user"))
            if role == "system":
                system_parts.append(str(message.get("content", "")))
                continue
            contents.append(
                {
                    "role": "model" if role == "assistant" else "user",
                    "parts": [{"text": str(message.get("content", ""))}],
                }
            )

        generation_config: dict[str, Any] = {}
        for source_key, target_key in (
            ("temperature", "temperature"),
            ("top_p", "topP"),
            ("max_tokens", "maxOutputTokens"),
            ("stop", "stopSequences"),
        ):
            if source_key in source:
                generation_config[target_key] = source.pop(source_key)

        body: dict[str, Any] = {"contents": contents}
        if generation_config:
            body["generationConfig"] = generation_config
        if system_parts:
            body["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_parts)}]
            }

        headers, params = self._auth(token)
        endpoint = self._url(
            self.config.custom_endpoints.get(
                "chat",
                f"/v1beta/models/{quote(model, safe='')}:generateContent",
            )
        )
        response = await self.client.post(
            endpoint,
            headers=headers,
            params=params,
            json=body,
        )
        self._raise_for_status(response)
        return self.normalize(response.json(), model)

    async def stream(
        self,
        token: Token,
        payload: Mapping[str, Any],
    ) -> AsyncIterator[str]:
        source = dict(payload)
        model = self._model_name(str(source.pop("model", ""))).removeprefix("models/")
        headers, params = self._auth(token)
        endpoint = self._url(
            self.config.custom_endpoints.get(
                "chat",
                f"/v1beta/models/{quote(model, safe='')}:streamGenerateContent",
            )
        )
        async with self.client.stream(
            "POST",
            endpoint,
            headers=headers,
            params=params,
            json={"contents": list(source.get("messages", []))},
        ) as response:
            self._raise_for_status(response)
            async for line in response.aiter_lines():
                if line:
                    yield line

    async def discover_models(self, token: Token) -> list[ModelConfig]:
        headers, params = self._auth(token)
        response = await self.client.get(
            self._url(self.config.custom_endpoints.get("models", "/v1beta/models")),
            headers=headers,
            params=params,
        )
        if not response.is_success:
            if self.config.models:
                return list(self.config.models)
            self._raise_for_status(response)

        rows = response.json().get("models", [])
        discovered = list(self.config.models)
        known_remote = {model.provider_model for model in discovered}
        for row in rows:
            remote_id = str(row.get("name", "")).removeprefix("models/")
            if remote_id and remote_id not in known_remote:
                discovered.append(
                    ModelConfig(id=remote_id, provider_model=remote_id)
                )
        return discovered

    @staticmethod
    def normalize(payload: dict[str, Any], model: str) -> dict[str, Any]:
        candidates = payload.get("candidates", [])
        content_text = ""
        finish_reason = "stop"
        if candidates:
            candidate = candidates[0]
            parts = candidate.get("content", {}).get("parts", [])
            content_text = "".join(str(part.get("text", "")) for part in parts)
            finish_reason = str(candidate.get("finishReason", "stop")).lower()

        usage = payload.get("usageMetadata", {})
        prompt_tokens = int(usage.get("promptTokenCount", 0))
        completion_tokens = int(usage.get("candidatesTokenCount", 0))
        return {
            "id": payload.get("responseId"),
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content_text},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": int(
                    usage.get("totalTokenCount", prompt_tokens + completion_tokens)
                ),
            },
        }


class AzureOpenAIAdapter(OpenAICompatibleAdapter):
    async def complete(
        self,
        token: Token,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        body = dict(payload)
        model = self._model_name(str(body.get("model", "")))
        body["model"] = model
        template = self.config.custom_endpoints.get(
            "chat",
            "/openai/deployments/{model}/chat/completions",
        )
        headers, params = self._auth(token)
        headers["api-key"] = token.value.get_secret_value()
        response = await self.client.post(
            template.format(model=quote(model, safe="")),
            headers=headers,
            params=params,
            json=body,
        )
        self._raise_for_status(response)
        return response.json()

    async def stream(
        self,
        token: Token,
        payload: Mapping[str, Any],
    ) -> AsyncIterator[str]:
        body = dict(payload)
        model = self._model_name(str(body.get("model", "")))
        body["model"] = model
        body["stream"] = True
        template = self.config.custom_endpoints.get(
            "chat",
            "/openai/deployments/{model}/chat/completions",
        )
        headers, params = self._auth(token)
        headers["api-key"] = token.value.get_secret_value()
        headers["Accept"] = "text/event-stream"
        async with self.client.stream(
            "POST",
            template.format(model=quote(model, safe="")),
            headers=headers,
            params=params,
            json=body,
        ) as response:
            self._raise_for_status(response)
            async for line in response.aiter_lines():
                if line:
                    yield line


class AdapterFactory:
    @staticmethod
    def create(config: ProviderConfig) -> BaseProviderAdapter:
        name = config.name.lower()
        if name == "anthropic":
            return AnthropicAdapter(config)
        if name in {"google", "gemini"}:
            return GeminiAdapter(config)
        if name == "azure":
            return AzureOpenAIAdapter(config)
        return OpenAICompatibleAdapter(config)