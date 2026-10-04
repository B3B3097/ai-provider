# language: Python, file: token_abuse_engine/token_sources.py, runtime: Python 3.10+

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

from pydantic import SecretStr

from .config import TokenFactoryConfig, TokenSource
from .models import Token, TokenOrigin, TokenStatus


class TokenSourceLoader:
    def load(self, config: TokenFactoryConfig) -> list[Token]:
        values: list[tuple[str, TokenOrigin]] = []

        if TokenSource.STATIC in config.sources:
            values.extend(
                (value.strip(), TokenOrigin.STATIC)
                for value in config.static_tokens
                if value.strip()
            )

        if TokenSource.ENV in config.sources:
            prefix = config.env_prefix
            values.extend(
                (value.strip(), TokenOrigin.ENV)
                for name, value in os.environ.items()
                if name.startswith(prefix) and value.strip()
            )

        if TokenSource.FILE in config.sources:
            values.extend(
                (value, TokenOrigin.FILE)
                for value in self._read_file(config.file_path)
            )

        tokens: list[Token] = []
        seen: set[str] = set()
        for value, origin in values:
            token = Token(
                value=SecretStr(value),
                provider=config.provider,
                origin=origin,
                status=TokenStatus.HEALTHY,
                metadata={"loaded_by": "token_factory"},
            )
            if token.fingerprint in seen:
                continue
            seen.add(token.fingerprint)
            tokens.append(token)
        return tokens

    @staticmethod
    def _read_file(path_value: str) -> Iterable[str]:
        if not path_value:
            return []
        path = Path(path_value).expanduser()
        if not path.is_file():
            return []

        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return []

        if content.startswith("["):
            try:
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON token file: {path}") from exc
            if not isinstance(payload, list):
                raise ValueError(f"Token JSON must contain a list: {path}")
            return [str(value).strip() for value in payload if str(value).strip()]

        values: list[str] = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            values.append(line)
        return values