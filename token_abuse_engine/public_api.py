# language: Python, file: token_abuse_engine/public_api.py, runtime: Python 3.10+
# Public API key lifecycle and quota accounting for operator-issued client keys.

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import secrets
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .config import PublicApiConfig


class ApiKeyStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class PublicApiError(RuntimeError):
    def __init__(self, message: str, status_code: int = 401) -> None:
        self.status_code = status_code
        super().__init__(message)


class InvalidApiKey(PublicApiError):
    def __init__(self) -> None:
        super().__init__("Invalid API key", 401)


class RevokedApiKey(PublicApiError):
    def __init__(self) -> None:
        super().__init__("API key has been revoked", 401)


class QuotaExceeded(PublicApiError):
    def __init__(self, message: str, retry_after: int = 60) -> None:
        self.retry_after = retry_after
        super().__init__(message, 429)


@dataclass
class ApiKeyRecord:
    id: str
    name: str
    prefix: str
    secret_hash: str
    status: ApiKeyStatus = ApiKeyStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    rpm_limit: int = 60
    rpd_limit: int = 10_000
    token_limit: int = 1_000_000
    expires_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("secret_hash", None)
        data["status"] = self.status.value
        return data

    def to_json(self) -> str:
        data = asdict(self)
        data["status"] = self.status.value
        return json.dumps(data, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> "ApiKeyRecord":
        data = json.loads(value)
        data["status"] = ApiKeyStatus(data.get("status", "active"))
        return cls(**data)


class ApiKeyStore(ABC):
    @abstractmethod
    async def save(self, record: ApiKeyRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get(self, key_id: str) -> ApiKeyRecord | None:
        raise NotImplementedError

    @abstractmethod
    async def get_by_hash(self, secret_hash: str) -> ApiKeyRecord | None:
        raise NotImplementedError

    @abstractmethod
    async def delete_hash(self, secret_hash: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def all(self) -> list[ApiKeyRecord]:
        raise NotImplementedError

    @abstractmethod
    async def increment(self, key: str, amount: int, ttl: int) -> int:
        raise NotImplementedError

    @abstractmethod
    async def counter(self, key: str) -> int:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class MemoryApiKeyStore(ApiKeyStore):
    def __init__(self) -> None:
        self.records: dict[str, ApiKeyRecord] = {}
        self.hashes: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.lock = asyncio.Lock()

    async def save(self, record: ApiKeyRecord) -> None:
        async with self.lock:
            self.records[record.id] = copy.deepcopy(record)
            self.hashes[record.secret_hash] = record.id

    async def get(self, key_id: str) -> ApiKeyRecord | None:
        async with self.lock:
            record = self.records.get(key_id)
            return copy.deepcopy(record) if record else None

    async def get_by_hash(self, secret_hash: str) -> ApiKeyRecord | None:
        async with self.lock:
            key_id = self.hashes.get(secret_hash)
            record = self.records.get(key_id or "")
            return copy.deepcopy(record) if record else None

    async def delete_hash(self, secret_hash: str) -> None:
        async with self.lock:
            self.hashes.pop(secret_hash, None)

    async def all(self) -> list[ApiKeyRecord]:
        async with self.lock:
            return [copy.deepcopy(record) for record in self.records.values()]

    async def increment(self, key: str, amount: int, ttl: int) -> int:
        async with self.lock:
            self.counters[key] = self.counters.get(key, 0) + amount
            return self.counters[key]

    async def counter(self, key: str) -> int:
        async with self.lock:
            return self.counters.get(key, 0)


class RedisApiKeyStore(ApiKeyStore):
    def __init__(self, url: str, prefix: str) -> None:
        try:
            import redis.asyncio as redis
        except ImportError as exc:
            raise RuntimeError(
                "Install redis to use the public API Redis store"
            ) from exc
        self.client = redis.from_url(url, decode_responses=True)
        self.prefix = prefix.rstrip(":")

    def _record_key(self, key_id: str) -> str:
        return f"{self.prefix}:key:{key_id}"

    def _hash_key(self, secret_hash: str) -> str:
        return f"{self.prefix}:hash:{secret_hash}"

    def _index_key(self) -> str:
        return f"{self.prefix}:keys"

    def _counter_key(self, key: str) -> str:
        return f"{self.prefix}:counter:{key}"

    async def save(self, record: ApiKeyRecord) -> None:
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.set(self._record_key(record.id), record.to_json())
            pipe.set(self._hash_key(record.secret_hash), record.id)
            pipe.sadd(self._index_key(), record.id)
            await pipe.execute()

    async def get(self, key_id: str) -> ApiKeyRecord | None:
        value = await self.client.get(self._record_key(key_id))
        return ApiKeyRecord.from_json(value) if value else None

    async def get_by_hash(self, secret_hash: str) -> ApiKeyRecord | None:
        key_id = await self.client.get(self._hash_key(secret_hash))
        return await self.get(key_id) if key_id else None

    async def delete_hash(self, secret_hash: str) -> None:
        await self.client.delete(self._hash_key(secret_hash))

    async def all(self) -> list[ApiKeyRecord]:
        ids = await self.client.smembers(self._index_key())
        if not ids:
            return []
        values = await self.client.mget([self._record_key(key_id) for key_id in ids])
        return [ApiKeyRecord.from_json(value) for value in values if value]

    async def increment(self, key: str, amount: int, ttl: int) -> int:
        counter_key = self._counter_key(key)
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.incrby(counter_key, amount)
            pipe.expire(counter_key, ttl)
            result = await pipe.execute()
        return int(result[0])

    async def counter(self, key: str) -> int:
        value = await self.client.get(self._counter_key(key))
        return int(value or 0)

    async def close(self) -> None:
        await self.client.aclose()


class PublicApiKeyManager:
    def __init__(self, config: PublicApiConfig) -> None:
        self.config = config
        store_type = config.store_type.lower()
        if store_type == "redis":
            if not config.redis_url:
                raise ValueError("public_api.redis_url is required for Redis")
            self.store: ApiKeyStore = RedisApiKeyStore(
                config.redis_url,
                config.redis_prefix,
            )
        elif store_type == "memory":
            self.store = MemoryApiKeyStore()
        else:
            raise ValueError(f"Unsupported public API store type: {config.store_type}")

    @staticmethod
    def hash_secret(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _limit(value: int | None, default: int) -> int:
        return default if value is None else value

    async def register_existing(
        self,
        raw_key: str,
        *,
        name: str = "configured-client",
        rpm_limit: int | None = None,
        rpd_limit: int | None = None,
        token_limit: int | None = None,
    ) -> ApiKeyRecord:
        key_hash = self.hash_secret(raw_key)
        key_id = f"static_{key_hash[:16]}"
        existing = await self.store.get(key_id)
        if existing:
            existing.name = name
            existing.rpm_limit = self._limit(rpm_limit, self.config.default_rpm)
            existing.rpd_limit = self._limit(rpd_limit, self.config.default_rpd)
            existing.token_limit = self._limit(
                token_limit,
                self.config.default_token_limit,
            )
            await self.store.save(existing)
            return existing
        record = ApiKeyRecord(
            id=key_id,
            name=name,
            prefix=f"static-{key_hash[:8]}",
            secret_hash=key_hash,
            rpm_limit=self._limit(rpm_limit, self.config.default_rpm),
            rpd_limit=self._limit(rpd_limit, self.config.default_rpd),
            token_limit=self._limit(
                token_limit,
                self.config.default_token_limit,
            ),
        )
        await self.store.save(record)
        return record

    async def issue(
        self,
        name: str,
        *,
        rpm_limit: int | None = None,
        rpd_limit: int | None = None,
        token_limit: int | None = None,
        ttl_days: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, ApiKeyRecord]:
        if not name.strip():
            raise ValueError("API key name is required")
        key_id = f"key_{uuid.uuid4().hex[:16]}"
        secret = secrets.token_urlsafe(32)
        raw_key = f"{self.config.key_prefix}-{key_id[4:]}-{secret}"
        ttl = self.config.default_ttl_days if ttl_days is None else ttl_days
        record = ApiKeyRecord(
            id=key_id,
            name=name.strip(),
            prefix=f"{self.config.key_prefix}-{key_id[-8:]}",
            secret_hash=self.hash_secret(raw_key),
            rpm_limit=self._limit(rpm_limit, self.config.default_rpm),
            rpd_limit=self._limit(rpd_limit, self.config.default_rpd),
            token_limit=self._limit(
                token_limit,
                self.config.default_token_limit,
            ),
            expires_at=(time.time() + ttl * 86400) if ttl > 0 else None,
            metadata=dict(metadata or {}),
        )
        await self.store.save(record)
        return raw_key, record

    async def authenticate(self, raw_key: str) -> ApiKeyRecord:
        record = await self.store.get_by_hash(self.hash_secret(raw_key))
        if record is None:
            raise InvalidApiKey()
        if record.status != ApiKeyStatus.ACTIVE:
            raise RevokedApiKey()
        if record.expires_at and time.time() >= record.expires_at:
            raise RevokedApiKey()

        usage = await self.usage(record.id)
        if record.token_limit > 0 and usage["tokens_total"] >= record.token_limit:
            raise QuotaExceeded("Token budget exhausted", 86400)

        minute = int(time.time() // 60)
        day = time.strftime("%Y-%m-%d", time.gmtime())
        rpm = await self.store.increment(
            f"{record.id}:rpm:{minute}",
            1,
            120,
        )
        if record.rpm_limit > 0 and rpm > record.rpm_limit:
            raise QuotaExceeded(
                "Requests per minute exceeded",
                60 - int(time.time() % 60),
            )
        rpd = await self.store.increment(
            f"{record.id}:rpd:{day}",
            1,
            172800,
        )
        if record.rpd_limit > 0 and rpd > record.rpd_limit:
            raise QuotaExceeded("Daily request quota exceeded", 86400)
        await self.store.increment(
            f"{record.id}:requests_total",
            1,
            31536000,
        )
        return record

    async def revoke(self, key_id: str) -> bool:
        record = await self.store.get(key_id)
        if record is None:
            return False
        record.status = ApiKeyStatus.REVOKED
        await self.store.save(record)
        await self.store.delete_hash(record.secret_hash)
        return True

    async def usage(self, key_id: str) -> dict[str, int]:
        minute = int(time.time() // 60)
        day = time.strftime("%Y-%m-%d", time.gmtime())
        return {
            "rpm_current": await self.store.counter(f"{key_id}:rpm:{minute}"),
            "rpd_current": await self.store.counter(f"{key_id}:rpd:{day}"),
            "requests_total": await self.store.counter(f"{key_id}:requests_total"),
            "tokens_total": await self.store.counter(f"{key_id}:tokens_total"),
        }

    async def record_usage(self, key_id: str, tokens: int) -> None:
        if tokens > 0:
            await self.store.increment(
                f"{key_id}:tokens_total",
                tokens,
                31536000,
            )

    async def close(self) -> None:
        await self.store.close()
