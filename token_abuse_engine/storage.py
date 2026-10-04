# language: Python, file: token_abuse_engine/storage.py, runtime: Python 3.11+

from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .config import StorageConfig
from .models import Token


class TokenStore(ABC):
    @abstractmethod
    async def load_provider(self, provider: str) -> list[Token]: ...

    @abstractmethod
    async def save_provider(self, provider: str, tokens: list[Token]) -> None: ...

    @abstractmethod
    async def upsert(self, token: Token) -> None: ...

    @abstractmethod
    async def delete(self, provider: str, fingerprint: str) -> bool: ...

    async def close(self) -> None:
        return None


class MemoryTokenStore(TokenStore):
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Token]] = {}
        self._lock = asyncio.Lock()

    async def load_provider(self, provider: str) -> list[Token]:
        async with self._lock:
            return copy.deepcopy(list(self._data.get(provider, {}).values()))

    async def save_provider(self, provider: str, tokens: list[Token]) -> None:
        async with self._lock:
            self._data[provider] = {
                token.fingerprint: copy.deepcopy(token) for token in tokens
            }

    async def upsert(self, token: Token) -> None:
        async with self._lock:
            self._data.setdefault(token.provider, {})[
                token.fingerprint
            ] = copy.deepcopy(token)

    async def delete(self, provider: str, fingerprint: str) -> bool:
        async with self._lock:
            return self._data.get(provider, {}).pop(fingerprint, None) is not None


class JsonTokenStore(TokenStore):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock = asyncio.Lock()

    async def load_provider(self, provider: str) -> list[Token]:
        async with self._lock:
            data = await asyncio.to_thread(self._read)
            return self._decode(data.get(provider, []))

    async def save_provider(self, provider: str, tokens: list[Token]) -> None:
        async with self._lock:
            data = await asyncio.to_thread(self._read)
            data[provider] = [self._encode(token) for token in tokens]
            await asyncio.to_thread(self._write, data)

    async def upsert(self, token: Token) -> None:
        async with self._lock:
            data = await asyncio.to_thread(self._read)
            rows = data.setdefault(token.provider, [])
            for index, row in enumerate(rows):
                if row.get("fingerprint") == token.fingerprint:
                    rows[index] = self._encode(token)
                    break
            else:
                rows.append(self._encode(token))
            await asyncio.to_thread(self._write, data)

    async def delete(self, provider: str, fingerprint: str) -> bool:
        async with self._lock:
            data = await asyncio.to_thread(self._read)
            rows = data.get(provider, [])
            kept = [
                row for row in rows
                if row.get("fingerprint") != fingerprint
            ]
            if len(kept) == len(rows):
                return False
            if kept:
                data[provider] = kept
            else:
                data.pop(provider, None)
            await asyncio.to_thread(self._write, data)
            return True

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"Invalid token store: {self.path}")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _encode(token: Token) -> dict[str, Any]:
        row = token.to_dict()
        row["fingerprint"] = token.fingerprint
        return row

    @staticmethod
    def _decode(rows: list[dict[str, Any]]) -> list[Token]:
        return [Token.from_dict(row) for row in rows]


class RedisTokenStore(TokenStore):
    def __init__(self, url: str, prefix: str = "token_engine") -> None:
        try:
            import redis.asyncio as redis
        except ImportError as exc:
            raise RuntimeError(
                "Install redis to use Redis storage"
            ) from exc
        self.client = redis.from_url(url, decode_responses=True)
        self.prefix = prefix.rstrip(":")

    async def load_provider(self, provider: str) -> list[Token]:
        rows = await self.client.lrange(self._key(provider), 0, -1)
        return self._decode(rows)

    async def save_provider(
        self,
        provider: str,
        tokens: list[Token],
    ) -> None:
        key = self._key(provider)
        rows = [json.dumps(self._encode(token)) for token in tokens]
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            if rows:
                pipe.rpush(key, *rows)
            await pipe.execute()

    async def upsert(self, token: Token) -> None:
        rows = await self.client.lrange(self._key(token.provider), 0, -1)
        fingerprint = token.fingerprint
        replacement = json.dumps(self._encode(token))
        changed = False
        decoded: list[str] = []
        for row in rows:
            try:
                if json.loads(row).get("fingerprint") == fingerprint:
                    row = replacement
                    changed = True
            except json.JSONDecodeError:
                pass
            decoded.append(row)
        if not changed:
            decoded.append(replacement)
        key = self._key(token.provider)
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            pipe.rpush(key, *decoded)
            await pipe.execute()

    async def delete(self, provider: str, fingerprint: str) -> bool:
        rows = await self.client.lrange(self._key(provider), 0, -1)
        kept = [
            row for row in rows
            if self._fingerprint(row) != fingerprint
        ]
        if len(kept) == len(rows):
            return False
        key = self._key(provider)
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            if kept:
                pipe.rpush(key, *kept)
            await pipe.execute()
        return True

    async def close(self) -> None:
        await self.client.aclose()

    def _key(self, provider: str) -> str:
        return f"{self.prefix}:tokens:{provider}"

    @staticmethod
    def _fingerprint(row: str) -> str | None:
        try:
            value = json.loads(row).get("fingerprint")
            return str(value) if value is not None else None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _encode(token: Token) -> dict[str, Any]:
        row = token.to_dict()
        row["fingerprint"] = token.fingerprint
        return row

    @staticmethod
    def _decode(rows: list[str]) -> list[Token]:
        result: list[Token] = []
        for row in rows:
            try:
                result.append(Token.from_dict(json.loads(row)))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return result


def create_token_store(config: StorageConfig) -> TokenStore:
    storage_type = config.type.lower()
    if storage_type == "memory":
        return MemoryTokenStore()
    if storage_type in {"file", "json", "sqlite"}:
        path = str(config.file.get("path", "tokens/tokens.json"))
        return JsonTokenStore(path)
    if storage_type == "redis":
        url = str(config.redis.get("url", "redis://localhost:6379/0"))
        prefix = str(config.redis.get("prefix", "token_engine"))
        return RedisTokenStore(url, prefix)
    raise ValueError(f"Unsupported storage type: {config.type}")