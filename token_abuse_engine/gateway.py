# language: Python, file: token_abuse_engine/gateway.py, runtime: Python 3.11+

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict
from fnmatch import fnmatchcase
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .adapters import AdapterFactory, ProviderError
from .config import EngineConfig
from .models import Token, TokenOrigin, TokenStatus
from .pool import NoHealthyTokenError, TokenPool, create_selection_strategy
from .public_api import (
    ApiKeyRecord,
    PublicApiError,
    PublicApiKeyManager,
)
from .storage import TokenStore, create_token_store
from .token_sources import TokenSourceLoader

logger = logging.getLogger(__name__)


def _update_stream_usage(state: dict[str, int], value: Any) -> None:
    """Merge cumulative usage from provider-compatible JSON payloads."""
    if isinstance(value, list):
        for item in value:
            _update_stream_usage(state, item)
        return
    if not isinstance(value, Mapping):
        return

    for nested_key in ("usage", "usageMetadata", "message"):
        nested = value.get(nested_key)
        if isinstance(nested, (Mapping, list)):
            _update_stream_usage(state, nested)

    usage = value.get("usage")
    if not isinstance(usage, Mapping):
        usage = value.get("usageMetadata")
    if not isinstance(usage, Mapping):
        return

    prompt = usage.get(
        "prompt_tokens",
        usage.get("input_tokens", usage.get("promptTokenCount", 0)),
    )
    completion = usage.get(
        "completion_tokens",
        usage.get("output_tokens", usage.get("candidatesTokenCount", 0)),
    )
    total = usage.get("total_tokens", usage.get("totalTokenCount", 0))
    for key, raw_value in (
        ("prompt", prompt),
        ("completion", completion),
        ("total", total),
    ):
        try:
            state[key] = max(state[key], int(raw_value or 0))
        except (TypeError, ValueError):
            continue


def _update_stream_usage_line(state: dict[str, int], line: str) -> None:
    raw = line.strip()
    if raw.startswith("data:"):
        raw = raw[5:].strip()
    if not raw or raw == "[DONE]":
        return
    try:
        _update_stream_usage(state, json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return


def _completion_usage_tokens(result: Mapping[str, Any]) -> int:
    usage = result.get("usage")
    if not isinstance(usage, Mapping):
        return 0
    try:
        return int(
            usage.get("total_tokens")
            or (
                int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                + int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            )
        )
    except (TypeError, ValueError):
        return 0


def _key_matches(key: str, candidates: list[str]) -> bool:
    if not key:
        return False
    return any(
        bool(candidate) and hmac.compare_digest(key, candidate)
        for candidate in candidates
    )


def _public_api_error_response(exc: PublicApiError) -> JSONResponse:
    headers = None
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        headers = {"Retry-After": str(retry_after)}
    lowered = str(exc).lower()
    if exc.status_code == 429:
        error_type = "quota_exceeded"
    elif "revoked" in lowered or "expired" in lowered:
        error_type = "revoked_api_key"
    else:
        error_type = "invalid_api_key"
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "message": str(exc),
                "type": error_type,
            }
        },
        headers=headers,
    )


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    top_p: float | None = None
    stop: str | list[str] | None = None
    tools: list[dict[str, Any]] | None = None
    provider: str | None = None


class IssueApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rpm_limit: int | None = Field(default=None, ge=0)
    rpd_limit: int | None = Field(default=None, ge=0)
    token_limit: int | None = Field(default=None, ge=0)
    ttl_days: int | None = Field(default=None, ge=0, le=3650)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AddTokenRequest(BaseModel):
    provider: str
    value: str = Field(min_length=1)
    labels: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GatewayEngine:
    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.store: TokenStore = create_token_store(config.storage)
        self.public_keys = PublicApiKeyManager(config.gateway.public_api)
        self.token_source_loader = TokenSourceLoader()
        self.pools: dict[str, TokenPool] = {}
        self._health_tasks: list[asyncio.Task[None]] = []
        self._discovery_tasks: list[asyncio.Task[None]] = []
        self._started_at = time.time()
        self._requests_total = 0
        self._requests_failed = 0

    async def startup(self) -> None:
        for provider in self.config.providers:
            if not provider.enabled:
                continue
            adapter = AdapterFactory.create(provider)
            pool = TokenPool(
                provider,
                adapter,
                self.store,
                create_selection_strategy(self.config.load_balancer),
            )
            await pool.load()
            for factory_name, factory in self.config.token_factories.items():
                factory_provider = factory.provider or factory_name
                if not factory.enabled or factory_provider != provider.name:
                    continue
                for token in self.token_source_loader.load(factory):
                    await pool.add_token(token)
            self.pools[provider.name] = pool

        if self.config.gateway.public_api.enabled:
            for raw_key in self.config.gateway.api_keys:
                if raw_key.strip():
                    await self.public_keys.register_existing(
                        raw_key,
                        rpm_limit=self.config.gateway.rate_limit.rpm,
                        rpd_limit=self.config.gateway.rate_limit.rpd,
                    )

        for pool in self.pools.values():
            if pool.config.health_check.enabled:
                interval = max(5, pool.config.health_check.interval)
                self._health_tasks.append(
                    asyncio.create_task(
                        self._health_loop(pool, interval),
                        name=f"health-{pool.config.name}",
                    )
                )
            if pool.config.model_discovery:
                discovery_interval = max(60, pool.config.discovery_interval)
                self._discovery_tasks.append(
                    asyncio.create_task(
                        self._discovery_loop(pool, discovery_interval),
                        name=f"discovery-{pool.config.name}",
                    )
                )

    async def shutdown(self) -> None:
        tasks = [*self._health_tasks, *self._discovery_tasks]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(
            *(pool.close() for pool in self.pools.values()),
            return_exceptions=True,
        )
        await asyncio.gather(
            self.store.close(),
            self.public_keys.close(),
            return_exceptions=True,
        )

    async def health(self) -> dict[str, Any]:
        providers: dict[str, Any] = {}
        for name, pool in self.pools.items():
            stats = await pool.stats()
            providers[name] = {
                **asdict(stats),
                "circuit": pool.circuit.state.value,
                "available": len(pool.available_tokens()),
            }
        return {
            "status": "ok" if providers else "degraded",
            "uptime_seconds": round(time.time() - self._started_at, 3),
            "providers": providers,
        }

    async def metrics(self) -> dict[str, Any]:
        rows: dict[str, Any] = {}
        for name, pool in self.pools.items():
            stats = await pool.stats()
            rows[name] = asdict(stats)
        return {
            "requests_total": self._requests_total,
            "requests_failed": self._requests_failed,
            "providers": rows,
        }

    def resolve_provider(self, model: str, requested: str | None) -> TokenPool:
        if requested:
            pool = self.pools.get(requested)
            if pool is None:
                raise HTTPException(404, f"Unknown provider: {requested}")
            return pool

        aliases: dict[str, str] = {}
        for name, pool in self.pools.items():
            for item in pool.config.models:
                for alias in (item.id, item.provider_model, *item.aliases):
                    aliases[alias] = name
        if model in aliases:
            return self.pools[aliases[model]]

        for pattern, provider_names in self.config.model_routing.items():
            if not fnmatchcase(model, pattern):
                continue
            for provider_name in provider_names:
                if provider_name in self.pools:
                    return self.pools[provider_name]

        if not self.pools:
            raise HTTPException(503, "No providers are enabled")

        fallback_model = (
            self.config.fallback_models[0] if self.config.fallback_models else ""
        )
        if fallback_model and fallback_model in aliases:
            return self.pools[aliases[fallback_model]]
        return next(iter(self.pools.values()))

    async def add_token(self, request: AddTokenRequest) -> dict[str, str]:
        pool = self.pools.get(request.provider)
        if pool is None:
            raise HTTPException(404, f"Unknown provider: {request.provider}")
        token = Token(
            value=SecretStr(request.value),
            provider=request.provider,
            origin=TokenOrigin.IMPORTED,
            status=TokenStatus.HEALTHY,
            labels=dict(request.labels),
            metadata=dict(request.metadata),
        )
        await pool.add_token(token)
        return {
            "provider": request.provider,
            "fingerprint": token.fingerprint,
            "masked": token.masked,
        }

    async def delete_token(self, provider: str, fingerprint: str) -> None:
        pool = self.pools.get(provider)
        if pool is None:
            raise HTTPException(404, f"Unknown provider: {provider}")
        if not await pool.delete_token(fingerprint):
            raise HTTPException(404, "Token not found")

    async def discover_models(self, provider_name: str | None = None) -> dict[str, Any]:
        names = [provider_name] if provider_name else list(self.pools)
        added: dict[str, int] = {}
        errors: dict[str, str] = {}
        for name in names:
            pool = self.pools.get(name)
            if pool is None:
                errors[name] = "Unknown provider"
                continue
            try:
                added[name] = await self._discover_pool(pool)
            except Exception as exc:
                errors[name] = str(exc)
        return {"added": added, "errors": errors}

    async def _discover_pool(self, pool: TokenPool) -> int:
        token = pool.select()
        discovered = await pool.adapter.discover_models(token)
        known = {model.provider_model for model in pool.config.models}
        added = 0
        for model in discovered:
            if model.provider_model in known:
                continue
            pool.config.models.append(model)
            known.add(model.provider_model)
            added += 1
        return added

    async def _discovery_loop(self, pool: TokenPool, interval: int) -> None:
        while True:
            try:
                await self._discover_pool(pool)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(interval)

    async def _health_loop(self, pool: TokenPool, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await pool.healthcheck_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                continue


def create_app(config: EngineConfig) -> FastAPI:
    engine = GatewayEngine(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await engine.startup()
        app.state.engine = engine
        try:
            yield
        finally:
            await engine.shutdown()

    public_paths = {
        config.gateway.health_path,
        config.gateway.metrics_path,
        "/docs",
        "/openapi.json",
        "/redoc",
    }
    app = FastAPI(
        title="Unified LLM Gateway",
        version="2.0.0",
        docs_url="/docs" if config.gateway.docs_enabled else None,
        redoc_url="/redoc" if config.gateway.docs_enabled else None,
        openapi_url="/openapi.json" if config.gateway.docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.gateway.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in public_paths:
            return await call_next(request)

        authorization = request.headers.get("Authorization", "")
        key = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
        request.state.api_key = key
        request.state.is_admin = False
        request.state.api_key_record = None

        if config.gateway.admin_keys and _key_matches(key, config.gateway.admin_keys):
            request.state.is_admin = True
            return await call_next(request)

        if config.gateway.public_api.enabled:
            try:
                record = await engine.public_keys.authenticate(key)
            except PublicApiError as exc:
                return _public_api_error_response(exc)
            request.state.api_key_record = record
            return await call_next(request)

        if config.gateway.api_keys and not _key_matches(key, config.gateway.api_keys):
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "message": "Invalid API key",
                        "type": "invalid_api_key",
                    }
                },
            )
        return await call_next(request)

    @app.exception_handler(NoHealthyTokenError)
    async def no_healthy_token_handler(
        request: Request,
        exc: NoHealthyTokenError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": {"message": str(exc), "type": "pool_unavailable"}},
        )

    @app.exception_handler(ProviderError)
    async def provider_error_handler(
        request: Request,
        exc: ProviderError,
    ) -> JSONResponse:
        status = exc.status_code if exc.status_code >= 400 else 502
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "message": str(exc),
                    "type": "provider_error",
                    "provider": exc.provider,
                    "request_id": exc.request_id,
                }
            },
            headers={"Retry-After": str(int(exc.retry_after))}
            if exc.retry_after is not None
            else None,
        )

    @app.exception_handler(PublicApiError)
    async def public_api_error_handler(
        request: Request,
        exc: PublicApiError,
    ) -> JSONResponse:
        return _public_api_error_response(exc)

    async def require_admin(request: Request) -> None:
        if not getattr(request.state, "is_admin", False):
            raise HTTPException(403, "Admin key required")

    async def require_public_api(request: Request) -> None:
        if not config.gateway.public_api.enabled:
            raise HTTPException(404, "Public API is disabled")

    async def require_client_key(request: Request) -> ApiKeyRecord:
        record = getattr(request.state, "api_key_record", None)
        if not isinstance(record, ApiKeyRecord):
            raise HTTPException(403, "Client API key required")
        return record

    @app.get(config.gateway.health_path)
    async def health() -> dict[str, Any]:
        return await engine.health()

    @app.get(config.gateway.metrics_path)
    async def metrics() -> dict[str, Any]:
        return await engine.metrics()

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        data: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        public_config = config.gateway.public_api
        for provider_name, pool in engine.pools.items():
            for model in pool.config.models:
                key = (provider_name, model.provider_model)
                if key in seen or model.deprecated:
                    continue
                seen.add(key)
                capabilities = list(model.capabilities)
                for supported, capability in (
                    (model.supports_streaming, "streaming"),
                    (model.supports_functions, "functions"),
                    (model.supports_vision, "vision"),
                    (model.supports_audio, "audio"),
                ):
                    if supported and capability not in capabilities:
                        capabilities.append(capability)
                data.append(
                    {
                        "id": model.id,
                        "object": "model",
                        "created": int(engine._started_at),
                        "owned_by": provider_name,
                        "provider": provider_name,
                        "pricing": dict(model.pricing),
                        "limits": {
                            "max_tokens": model.max_tokens,
                            "context_window": model.context_window,
                            "rpm": pool.config.rate_limit.rpm,
                            "tpm": pool.config.rate_limit.tpm,
                            "rpd": pool.config.rate_limit.rpd,
                        },
                        "metadata": {
                            "provider_model": model.provider_model,
                            "aliases": list(model.aliases),
                            "capabilities": capabilities,
                            "supports_streaming": model.supports_streaming,
                            "supports_functions": model.supports_functions,
                            "supports_vision": model.supports_vision,
                            "supports_audio": model.supports_audio,
                            "tags": list(model.tags),
                            "priority": model.priority,
                        },
                    }
                )
        return {
            "object": "list",
            "data": data,
            "metadata": {
                "issuer": public_config.issuer_name,
                "support_url": public_config.support_url,
                "terms_url": public_config.terms_url,
                "privacy_url": public_config.privacy_url,
            },
        }

    @app.get("/v1/usage")
    async def usage(request: Request) -> dict[str, Any]:
        await require_public_api(request)
        record = await require_client_key(request)
        usage_data = await engine.public_keys.usage(record.id)
        return {
            "object": "usage",
            "key_id": record.id,
            "key": {
                "name": record.name,
                "prefix": record.prefix,
                "status": record.status.value,
                "created_at": record.created_at,
                "expires_at": record.expires_at,
                "metadata": dict(record.metadata),
            },
            "usage": usage_data,
            "limits": {
                "rpm": record.rpm_limit,
                "rpd": record.rpd_limit,
                "tokens_total": record.token_limit,
            },
            "remaining_tokens": (
                max(0, record.token_limit - usage_data["tokens_total"])
                if record.token_limit > 0
                else None
            ),
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(payload: ChatRequest, request: Request):
        pool = engine.resolve_provider(payload.model, payload.provider)
        body = payload.model_dump(
            exclude={"stream", "provider", "model"},
            exclude_none=True,
        )
        body["model"] = payload.model
        engine._requests_total += 1
        key_record = getattr(request.state, "api_key_record", None)

        if payload.stream:
            stream_options = body.get("stream_options")
            if not isinstance(stream_options, dict):
                stream_options = {}
            stream_options["include_usage"] = True
            body["stream_options"] = stream_options

            async def stream_response():
                usage_state = {"prompt": 0, "completion": 0, "total": 0}
                done_sent = False
                try:
                    async for line in pool.stream(body):
                        _update_stream_usage_line(usage_state, line)
                        if line.strip() == "data: [DONE]":
                            done_sent = True
                        if line.startswith(("data:", "event:", "id:", "retry:")):
                            yield line
                        else:
                            yield f"data: {line}"
                        yield "\n\n"
                    if not done_sent:
                        yield "data: [DONE]\n\n"
                except Exception:
                    engine._requests_failed += 1
                    raise
                finally:
                    tokens = max(
                        usage_state["total"],
                        usage_state["prompt"] + usage_state["completion"],
                    )
                    if isinstance(key_record, ApiKeyRecord) and tokens > 0:
                        try:
                            await engine.public_keys.record_usage(
                                key_record.id,
                                tokens,
                            )
                        except Exception:
                            logger.exception(
                                "Failed to record public API streaming usage"
                            )

            return StreamingResponse(
                stream_response(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        try:
            result, _token = await pool.execute(body)
            if isinstance(key_record, ApiKeyRecord):
                await engine.public_keys.record_usage(
                    key_record.id,
                    _completion_usage_tokens(result),
                )
            return result
        except Exception:
            engine._requests_failed += 1
            raise

    @app.post("/admin/api-keys", status_code=201)
    async def issue_api_key(
        payload: IssueApiKeyRequest,
        request: Request,
    ) -> dict[str, Any]:
        await require_admin(request)
        await require_public_api(request)
        raw_key, record = await engine.public_keys.issue(
            payload.name,
            rpm_limit=payload.rpm_limit,
            rpd_limit=payload.rpd_limit,
            token_limit=payload.token_limit,
            ttl_days=payload.ttl_days,
            metadata=payload.metadata,
        )
        return {"api_key": raw_key, **record.public_dict()}

    @app.get("/admin/api-keys")
    async def list_api_keys(request: Request) -> dict[str, Any]:
        await require_admin(request)
        await require_public_api(request)
        records = sorted(
            await engine.public_keys.store.all(),
            key=lambda item: (item.created_at, item.id),
        )
        return {
            "object": "list",
            "data": [record.public_dict() for record in records],
        }

    @app.delete("/admin/api-keys/{key_id}", status_code=204)
    async def revoke_api_key(key_id: str, request: Request) -> None:
        await require_admin(request)
        await require_public_api(request)
        if not await engine.public_keys.revoke(key_id):
            raise HTTPException(404, "API key not found")

    @app.post("/admin/models/discover")
    async def discover_models_route(
        request: Request, provider: str | None = None
    ) -> dict[str, Any]:
        await require_admin(request)
        return await engine.discover_models(provider)

    @app.post("/admin/tokens", status_code=201)
    async def add_token(payload: AddTokenRequest, request: Request):
        await require_admin(request)
        return await engine.add_token(payload)

    @app.delete("/admin/tokens/{provider}/{fingerprint}", status_code=204)
    async def delete_token(
        provider: str,
        fingerprint: str,
        request: Request,
    ) -> None:
        await require_admin(request)
        await engine.delete_token(provider, fingerprint)

    return app
