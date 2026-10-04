# language: Python, file: token_abuse_engine/pool.py, runtime: Python 3.11+

from __future__ import annotations

import asyncio
import random
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx

from .adapters import BaseProviderAdapter, ProviderError, ProviderTimeout
from .config import LoadBalancerStrategy, ProviderConfig
from .models import Token, TokenStatus
from .storage import TokenStore


class SelectionStrategy(ABC):
    @abstractmethod
    def select(
        self,
        tokens: list[Token],
        config: ProviderConfig,
    ) -> Token | None:
        raise NotImplementedError


class LeastUsedStrategy(SelectionStrategy):
    def select(
        self,
        tokens: list[Token],
        config: ProviderConfig,
    ) -> Token | None:
        if not tokens:
            return None
        return min(
            tokens,
            key=lambda token: (
                token.rpm_used / max(config.rate_limit.rpm, 1),
                token.total_requests,
                token.avg_latency,
                token.last_used,
            ),
        )


class RoundRobinStrategy(SelectionStrategy):
    def __init__(self) -> None:
        self._positions: defaultdict[str, int] = defaultdict(int)

    def select(
        self,
        tokens: list[Token],
        config: ProviderConfig,
    ) -> Token | None:
        if not tokens:
            return None
        position = self._positions[config.name] % len(tokens)
        self._positions[config.name] += 1
        return tokens[position]


class WeightedRandomStrategy(SelectionStrategy):
    def select(
        self,
        tokens: list[Token],
        config: ProviderConfig,
    ) -> Token | None:
        if not tokens:
            return None
        weights = [
            max(
                0.1,
                (max(config.rate_limit.rpm, 1) - token.rpm_used)
                / max(config.rate_limit.rpm, 1),
            )
            for token in tokens
        ]
        return random.choices(tokens, weights=weights, k=1)[0]


class LatencyAwareStrategy(SelectionStrategy):
    def select(
        self,
        tokens: list[Token],
        config: ProviderConfig,
    ) -> Token | None:
        if not tokens:
            return None
        return min(
            tokens,
            key=lambda token: (
                token.avg_latency if token.total_requests else 0.0,
                token.consecutive_failures,
                token.last_used,
            ),
        )


class PriorityStrategy(SelectionStrategy):
    def select(
        self,
        tokens: list[Token],
        config: ProviderConfig,
    ) -> Token | None:
        if not tokens:
            return None
        return min(
            tokens,
            key=lambda token: (
                token.metadata.get("priority", 100),
                token.total_requests,
            ),
        )


def create_selection_strategy(name: LoadBalancerStrategy) -> SelectionStrategy:
    strategies: dict[LoadBalancerStrategy, type[SelectionStrategy]] = {
        LoadBalancerStrategy.LEAST_USED: LeastUsedStrategy,
        LoadBalancerStrategy.ROUND_ROBIN: RoundRobinStrategy,
        LoadBalancerStrategy.WEIGHTED_RANDOM: WeightedRandomStrategy,
        LoadBalancerStrategy.LATENCY_AWARE: LatencyAwareStrategy,
        LoadBalancerStrategy.PRIORITY: PriorityStrategy,
        LoadBalancerStrategy.GEO_AWARE: PriorityStrategy,
    }
    return strategies[name]()


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config.circuit_breaker
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.opened_at = 0.0
        self.half_open_in_flight = 0
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() >= self.opened_at + self.config.timeout:
                self.state = CircuitState.HALF_OPEN
                self.half_open_in_flight = 0
                return True
            return False
        return self.half_open_in_flight < self.config.half_open_requests

    async def enter(self) -> None:
        async with self._lock:
            if not self.available:
                raise ProviderError(
                    "circuit",
                    503,
                    "provider circuit breaker is open",
                    retry_after=max(
                        0.0,
                        self.opened_at + self.config.timeout - time.time(),
                    ),
                )
            if self.state == CircuitState.HALF_OPEN:
                self.half_open_in_flight += 1

    async def success(self) -> None:
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.half_open_in_flight = max(
                    0, self.half_open_in_flight - 1
                )
            self.success_count += 1
            self.failure_count = 0
            if (
                self.state == CircuitState.HALF_OPEN
                and self.success_count >= self.config.success_threshold
            ):
                self.state = CircuitState.CLOSED
                self.success_count = 0

    async def failure(self, status_code: int | None = None) -> None:
        if status_code in self.config.excluded_status_codes:
            return
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.half_open_in_flight = max(
                    0, self.half_open_in_flight - 1
                )
            self.failure_count += 1
            self.success_count = 0
            if self.failure_count >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
                self.opened_at = time.time()


@dataclass(frozen=True)
class PoolStats:
    total: int
    healthy: int
    degraded: int
    cooling_down: int
    invalid: int
    pending: int


class NoHealthyTokenError(RuntimeError):
    pass


class TokenPool:
    def __init__(
        self,
        config: ProviderConfig,
        adapter: BaseProviderAdapter,
        store: TokenStore,
        strategy: SelectionStrategy,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self.store = store
        self.strategy = strategy
        self.circuit = CircuitBreaker(config)
        self.tokens: dict[str, Token] = {}
        self._token_semaphores: dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()
        self._inflight = 0
        self._inflight_lock = asyncio.Lock()

    async def load(self) -> None:
        rows = await self.store.load_provider(self.config.name)
        async with self._lock:
            self.tokens = {token.fingerprint: token for token in rows}
            self._token_semaphores = {
                fingerprint: asyncio.Semaphore(
                    max(1, self.config.max_concurrent)
                )
                for fingerprint in self.tokens
            }

    async def add_token(self, token: Token) -> Token:
        if token.provider != self.config.name:
            raise ValueError(
                f"Token provider {token.provider!r} does not match {self.config.name!r}"
            )
        async with self._lock:
            if token.fingerprint not in self.tokens:
                self._token_semaphores[token.fingerprint] = asyncio.Semaphore(
                    max(1, self.config.max_concurrent)
                )
            self.tokens[token.fingerprint] = token
        await self.store.upsert(token)
        return token

    async def delete_token(self, fingerprint: str) -> bool:
        async with self._lock:
            token = self.tokens.pop(fingerprint, None)
            self._token_semaphores.pop(fingerprint, None)
        if token is None:
            return False
        return await self.store.delete(self.config.name, fingerprint)

    def available_tokens(self) -> list[Token]:
        now = time.time()
        result: list[Token] = []
        for token in list(self.tokens.values()):
            self._refresh_windows(token, now)
            if token.status in {
                TokenStatus.HEALTHY,
                TokenStatus.DEGRADED,
            }:
                if token.expires_at and token.expires_at <= now:
                    token.status = TokenStatus.EXHAUSTED
                    continue
                if (
                    token.quarantine_until
                    and token.quarantine_until > now
                ):
                    continue
                limits = self.config.rate_limit
                if self._limit_reached(token.rpm_used, limits.rpm):
                    continue
                if self._limit_reached(token.tpm_used, limits.tpm):
                    continue
                if self._limit_reached(token.rpd_used, limits.rpd):
                    continue
                result.append(token)
        return result

    def select(self) -> Token:
        if not self.circuit.available:
            raise ProviderError(
                self.config.name,
                503,
                "provider circuit breaker is open",
            )
        available = self.available_tokens()
        selected = self.strategy.select(available, self.config)
        if selected is None:
            raise NoHealthyTokenError(
                f"No healthy tokens available for {self.config.name}"
            )
        return selected

    async def execute(
        self,
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], Token]:
        attempts = max(1, self.config.retry.max_attempts)
        tried: set[str] = set()
        last_error: Exception | None = None

        for attempt in range(attempts):
            token = self._select_untried(tried)
            tried.add(token.fingerprint)
            semaphore = self._token_semaphores[token.fingerprint]
            try:
                async with semaphore:
                    await self._enter_inflight()
                    try:
                        await self.circuit.enter()
                        started = time.perf_counter()
                        result = await self.adapter.complete(token, payload)
                        duration = time.perf_counter() - started
                        await self.circuit.success()
                        self._record_success(token, result, duration)
                        await self.store.upsert(token)
                        return result, token
                    finally:
                        await self._leave_inflight()
            except ProviderError as exc:
                last_error = exc
                self._record_error(token, exc)
                await self.store.upsert(token)
                await self.circuit.failure(exc.status_code)
                if not self._should_retry(exc.status_code) or attempt + 1 >= attempts:
                    raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = ProviderTimeout(
                    self.config.name,
                    str(exc) or "network timeout",
                )
                self._record_error(token, last_error)
                await self.store.upsert(token)
                await self.circuit.failure()
                if attempt + 1 >= attempts:
                    raise last_error from exc

            await self._retry_delay(attempt)

        raise last_error or NoHealthyTokenError(
            f"No healthy tokens available for {self.config.name}"
        )

    async def stream(
        self,
        payload: Mapping[str, Any],
    ) -> AsyncIterator[str]:
        token = self.select()
        semaphore = self._token_semaphores[token.fingerprint]
        async with semaphore:
            await self._enter_inflight()
            try:
                await self.circuit.enter()
                async for line in self.adapter.stream(token, payload):
                    token.rpm_used += 1
                    token.total_requests += 1
                    token.last_used = time.time()
                    yield line
                await self.circuit.success()
                token.status = (
                    TokenStatus.DEGRADED
                    if token.consecutive_failures
                    else TokenStatus.HEALTHY
                )
            except ProviderError as exc:
                self._record_error(token, exc)
                await self.circuit.failure(exc.status_code)
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                wrapped = ProviderTimeout(self.config.name, str(exc))
                self._record_error(token, wrapped)
                await self.circuit.failure()
                raise wrapped from exc
            finally:
                await self.store.upsert(token)
                await self._leave_inflight()

    async def healthcheck_all(self) -> dict[str, bool]:
        candidates = list(self.tokens.values())

        async def check(token: Token) -> tuple[str, bool]:
            healthy = await self.adapter.healthcheck(token)
            token.last_health_check = time.time()
            if healthy:
                token.consecutive_failures = 0
                token.status = TokenStatus.HEALTHY
            else:
                token.consecutive_failures += 1
                if token.consecutive_failures >= self.config.health_check.failure_threshold:
                    token.status = TokenStatus.DEGRADED
                elif token.status == TokenStatus.PENDING_VALIDATION:
                    token.status = TokenStatus.INVALID
            await self.store.upsert(token)
            return token.fingerprint, healthy

        results = await asyncio.gather(
            *(check(token) for token in candidates),
            return_exceptions=True,
        )
        output: dict[str, bool] = {}
        for result in results:
            if isinstance(result, tuple):
                output[result[0]] = result[1]
            elif isinstance(result, BaseException):
                fingerprint = hashlib_fallback(result)
                output[fingerprint] = False
        return output

    async def stats(self) -> PoolStats:
        now = time.time()
        counts = defaultdict(int)
        for token in self.tokens.values():
            self._refresh_windows(token, now)
            if token.status == TokenStatus.HEALTHY:
                counts["healthy"] += 1
            elif token.status == TokenStatus.DEGRADED:
                counts["degraded"] += 1
            elif token.status == TokenStatus.INVALID:
                counts["invalid"] += 1
            elif token.status == TokenStatus.PENDING_VALIDATION:
                counts["pending"] += 1
            if token.quarantine_until > now or token.status in {
                TokenStatus.RATE_LIMITED,
                TokenStatus.QUARANTINED,
            }:
                counts["cooling_down"] += 1
        return PoolStats(
            total=len(self.tokens),
            healthy=counts["healthy"],
            degraded=counts["degraded"],
            cooling_down=counts["cooling_down"],
            invalid=counts["invalid"],
            pending=counts["pending"],
        )

    async def close(self) -> None:
        await self.adapter.close()

    def _select_untried(self, tried: set[str]) -> Token:
        available = [token for token in self.available_tokens() if token.fingerprint not in tried]
        if available:
            selected = self.strategy.select(available, self.config)
            if selected is not None:
                return selected
        selected = self.select()
        return selected

    def _record_success(
        self,
        token: Token,
        response: Mapping[str, Any],
        duration: float,
    ) -> None:
        usage_tokens = self.adapter._usage_tokens(response)
        token.total_requests += 1
        token.total_latency += duration
        token.last_used = time.time()
        token.rpm_used += 1
        token.tpm_used += usage_tokens
        token.rpd_used += 1
        token.total_tokens += usage_tokens
        token.consecutive_successes += 1
        token.consecutive_failures = 0
        token.status = TokenStatus.HEALTHY
        token.quarantine_until = 0

    def _record_error(self, token: Token, error: ProviderError) -> None:
        token.total_errors += 1
        token.total_requests += 1
        token.consecutive_failures += 1
        token.consecutive_successes = 0

        if error.status_code in {401, 403}:
            token.status = TokenStatus.INVALID
        elif error.status_code == 429:
            token.status = TokenStatus.RATE_LIMITED
            token.quarantine_until = time.time() + (
                error.retry_after if error.retry_after is not None else 60.0
            )
        elif error.status_code >= 500 or error.status_code == 408:
            token.status = TokenStatus.DEGRADED
            token.quarantine_until = time.time() + min(
                30.0,
                2.0 ** min(token.consecutive_failures, 5),
            )
        else:
            token.status = TokenStatus.DEGRADED

    @staticmethod
    def _limit_reached(used: int, limit: int) -> bool:
        return limit > 0 and used >= limit

    @staticmethod
    def _refresh_windows(token: Token, now: float) -> None:
        if token.rpm_reset <= now:
            token.rpm_used = 0
            token.rpm_reset = now + 60
        if token.tpm_reset <= now:
            token.tpm_used = 0
            token.tpm_reset = now + 60
        if token.rpd_reset <= now:
            token.rpd_used = 0
            token.rpd_reset = now + 86400
        if (
            token.status in {TokenStatus.RATE_LIMITED, TokenStatus.QUARANTINED}
            and token.quarantine_until
            and token.quarantine_until <= now
        ):
            token.status = TokenStatus.DEGRADED
            token.quarantine_until = 0

    def _should_retry(self, status_code: int) -> bool:
        return status_code in self.config.retry.retry_on

    async def _retry_delay(self, attempt: int) -> None:
        base = self.config.retry.base_delay * (
            self.config.retry.exponential_base**attempt
        )
        delay = min(base, self.config.retry.max_delay)
        if self.config.retry.jitter:
            delay *= random.uniform(0.75, 1.25)
        await asyncio.sleep(max(0.0, delay))

    async def _enter_inflight(self) -> None:
        async with self._inflight_lock:
            if self._inflight >= self.config.max_concurrent:
                raise NoHealthyTokenError(
                    f"Provider {self.config.name} concurrency limit reached"
                )
            self._inflight += 1

    async def _leave_inflight(self) -> None:
        async with self._inflight_lock:
            self._inflight = max(0, self._inflight - 1)


def hashlib_fallback(error: BaseException) -> str:
    return f"exception:{type(error).__name__}:{error}"