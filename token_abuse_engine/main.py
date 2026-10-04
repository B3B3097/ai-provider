# language: Python, file: token_abuse_engine/main.py, runtime: Python 3.11+

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import create_example_config, load_config
from .gateway import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m token_abuse_engine.main",
        description="Unified LLM API gateway",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init-config",
        help="Create a starter YAML configuration",
    )
    init_parser.add_argument("path", nargs="?", default="config.yaml")
    init_parser.add_argument("--force", action="store_true")

    validate_parser = subparsers.add_parser(
        "validate",
        help="Parse and validate a YAML configuration",
    )
    validate_parser.add_argument("path", nargs="?", default="config.yaml")

    serve_parser = subparsers.add_parser(
        "serve",
        help="Run the HTTP gateway",
    )
    serve_parser.add_argument("path", nargs="?", default="config.yaml")
    serve_parser.add_argument(
        "--host",
        help="Override gateway.bind_host",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        help="Override gateway.bind_port",
    )
    serve_parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload on source changes in development",
    )

    return parser


def command_init_config(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        print(
            f"error: {path} already exists; pass --force to replace it",
            file=sys.stderr,
        )
        return 2
    create_example_config(path)
    return 0


def command_validate(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.path)
    except Exception as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 1

    enabled = [provider.name for provider in config.providers if provider.enabled]
    model_count = sum(
        len(provider.models)
        for provider in config.providers
        if provider.enabled
    )
    summary = {
        "path": str(Path(args.path).resolve()),
        "environment": config.environment,
        "storage": config.storage.type,
        "providers": enabled,
        "configured_models": model_count,
        "gateway": {
            "host": config.gateway.bind_host,
            "port": config.gateway.bind_port,
            "workers": config.gateway.workers,
        },
        "api_keys_configured": any(
            key.strip() for key in config.gateway.api_keys
        ),
        "admin_keys_configured": any(
            key.strip() for key in config.gateway.admin_keys
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def command_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "error: install runtime dependencies with "
            "'pip install -r requirements.txt'",
            file=sys.stderr,
        )
        return 1

    try:
        config = load_config(args.path)
    except Exception as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 1

    host = args.host or config.gateway.bind_host
    port = args.port or config.gateway.bind_port

    if args.reload:
        os.environ["TOKEN_ENGINE_CONFIG"] = str(Path(args.path).resolve())
        uvicorn.run(
            "token_abuse_engine.main:app_from_env",
            factory=True,
            host=host,
            port=port,
            reload=True,
            log_level=config.gateway.log_level.lower(),
        )
        return 0

    workers = max(1, config.gateway.workers)
    if workers == 1:
        uvicorn.run(
            create_app(config),
            host=host,
            port=port,
            log_level=config.gateway.log_level.lower(),
        )
        return 0

    os.environ["TOKEN_ENGINE_CONFIG"] = str(Path(args.path).resolve())
    uvicorn.run(
        "token_abuse_engine.main:app_from_env",
        factory=True,
        host=host,
        port=port,
        workers=workers,
        log_level=config.gateway.log_level.lower(),
    )
    return 0


def app_from_env():
    path = os.environ.get("TOKEN_ENGINE_CONFIG", "config.yaml")
    return create_app(load_config(path))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "init-config": command_init_config,
        "validate": command_validate,
        "serve": command_serve,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())