# language: Python, file: token_abuse_engine/gateway.py, runtime: Python 3.11+

from __future__ import annotations

import asyncio
import time
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
from .storage import TokenStore, create_token_store
from .token_sources import TokenSourceLoader


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


class AddTokenRequest(BaseModel):
    provider: str
    value: str = Field(min_length=1)
    labels: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GatewayEngine:
    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.store: TokenStore = create_token_store(config.storage)
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
                discovery_interval = max(
                    60, pool.config.discovery_interval
                )
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
        await self.store.close()

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
            self.config.fallback_models[0]
            if self.config.fallback_models
            else ""
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

    async def discover_models(
        self, provider_name: str | None = None
    ) -> dict[str, Any]:
        names = (
            [provider_name]
            if provider_name
            else list(self.pools)
        )
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
        known = {
            model.provider_model
            for model in pool.config.models
        }
        added = 0
        for model in discovered:
            if model.provider_model in known:
                continue
            pool.config.models.append(model)
            known.add(model.provider_model)
            added += 1
        return added

    async def _discovery_loop(
        self, pool: TokenPool, interval: int
    ) -> None:
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
        if request.url.path in public_paths:
            return await call_next(request)

        authorization = request.headers.get("Authorization", "")
        key = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
        valid_keys = set(config.gateway.api_keys) | set(config.gateway.admin_keys)
        if valid_keys and key not in valid_keys:
            return JSONResponse(
                status_code=401,
                content={"error": {"message": "Invalid API key"}},
            )
        request.state.api_key = key
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

    async def require_admin(request: Request) -> None:
        key = getattr(request.state, "api_key", "")
        if not config.gateway.admin_keys or key not in config.gateway.admin_keys:
            raise HTTPException(403, "Admin key required")

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
        for provider_name, pool in engine.pools.items():
            for model in pool.config.models:
                key = (provider_name, model.provider_model)
                if key in seen or model.deprecated:
                    continue
                seen.add(key)
                data.append(
                    {
                        "id": model.id,
                        "object": "model",
                        "created": int(engine._started_at),
                        "owned_by": provider_name,
                        "provider": provider_name,
                    }
                )
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions")
    async def chat_completions(payload: ChatRequest, request: Request):
        pool = engine.resolve_provider(payload.model, payload.provider)
        body = payload.model_dump(
            exclude={"stream", "provider", "model"},
            exclude_none=True,
        )
        body["model"] = payload.model
        engine._requests_total += 1

        if payload.stream:
            async def stream_response():
                async for line in pool.stream(body):
                    if line.startswith(("data:", "event:", "id:", "retry:")):
                        yield line
                    else:
                        yield f"data: {line}"
                    yield "\n\n"
                yield "data: [DONE]\n\n"

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
            return result
        except Exception:
            engine._requests_failed += 1
            raise

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