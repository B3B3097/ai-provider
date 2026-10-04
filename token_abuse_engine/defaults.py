# language: Python, file: token_abuse_engine/defaults.py, runtime: Python 3.10+

from pathlib import Path

import yaml


def create_example_config(path: str | Path = "config.yaml") -> None:
    config = {
        "environment": "development",
        "debug": True,
        "default_model": "gpt-4o-mini",
        "fallback_models": ["gpt-4o", "claude-3-5-haiku"],
        "load_balancer": "latency_aware",
        "model_routing": {
            "gpt-*": ["openai", "azure"],
            "claude-*": ["anthropic"],
            "gemini-*": ["google"],
        },
        "gateway": {
            "bind_host": "127.0.0.1",
            "bind_port": 8080,
            "workers": 1,
            "api_keys": ["${GATEWAY_API_KEY:-local-client-key}"],
            "admin_keys": ["${ADMIN_API_KEY:-local-admin-key}"],
            "cors_origins": ["http://127.0.0.1", "http://localhost"],
            "rate_limit": {"rpm": 1000, "tpm": 500_000},
            "public_api": {
                "enabled": True,
                "store_type": "memory",
                "key_prefix": "sk-live",
                "default_rpm": 60,
                "default_rpd": 10_000,
                "default_token_limit": 1_000_000,
                "issuer_name": "Unified LLM Gateway",
            },
            "request_timeout": 300,
            "log_level": "INFO",
            "enable_metrics": True,
            "docs_enabled": True,
        },
        "storage": {
            "type": "file",
            "file": {"path": "./tokens/tokens.json"},
        },
        "providers": [
            {
                "name": "openai",
                "display_name": "OpenAI",
                "base_url": "https://api.openai.com",
                "enabled": True,
                "auth": {
                    "type": "bearer",
                    "header_name": "Authorization",
                    "prefix": "Bearer ",
                },
                "rate_limit": {"rpm": 3000, "tpm": 1_000_000},
                "retry": {
                    "max_attempts": 3,
                    "retry_on": [429, 500, 502, 503, 504],
                },
                "health_check": {
                    "enabled": True,
                    "interval": 30,
                    "endpoint": "/v1/models",
                },
                "models": [
                    {
                        "id": "gpt-4o-mini",
                        "provider_model": "gpt-4o-mini",
                        "max_tokens": 16_384,
                        "context_window": 128_000,
                        "supports_vision": True,
                        "tags": ["cheap", "vision"],
                    },
                    {
                        "id": "gpt-4o",
                        "provider_model": "gpt-4o",
                        "max_tokens": 16_384,
                        "context_window": 128_000,
                        "supports_vision": True,
                        "tags": ["flagship", "vision"],
                    },
                ],
            },
            {
                "name": "anthropic",
                "display_name": "Anthropic",
                "base_url": "https://api.anthropic.com",
                "enabled": True,
                "auth": {
                    "type": "api_key",
                    "header_name": "x-api-key",
                    "prefix": "",
                },
                "rate_limit": {"rpm": 1000, "tpm": 500_000},
                "health_check": {
                    "enabled": True,
                    "interval": 30,
                    "endpoint": "/v1/models",
                },
                "models": [
                    {
                        "id": "claude-3-5-haiku",
                        "provider_model": "claude-3-5-haiku-20241022",
                        "max_tokens": 8192,
                        "context_window": 200_000,
                        "supports_vision": True,
                        "tags": ["cheap", "fast"],
                    },
                    {
                        "id": "claude-3-5-sonnet",
                        "provider_model": "claude-3-5-sonnet-20241022",
                        "max_tokens": 8192,
                        "context_window": 200_000,
                        "supports_vision": True,
                        "tags": ["flagship", "vision"],
                    },
                ],
            },
            {
                "name": "google",
                "display_name": "Google AI",
                "base_url": "https://generativelanguage.googleapis.com",
                "enabled": True,
                "auth": {"type": "query_param", "query_param": "key"},
                "rate_limit": {"rpm": 1500, "tpm": 1_000_000},
                "health_check": {
                    "enabled": True,
                    "interval": 60,
                    "endpoint": "/v1beta/models",
                },
                "models": [
                    {
                        "id": "gemini-1.5-flash",
                        "provider_model": "gemini-1.5-flash",
                        "max_tokens": 8192,
                        "context_window": 1_000_000,
                        "supports_vision": True,
                        "tags": ["cheap", "fast"],
                    },
                    {
                        "id": "gemini-1.5-pro",
                        "provider_model": "gemini-1.5-pro",
                        "max_tokens": 8192,
                        "context_window": 2_000_000,
                        "supports_vision": True,
                        "tags": ["flagship", "long-context"],
                    },
                ],
            },
            {
                "name": "custom",
                "display_name": "OpenAI-compatible local provider",
                "base_url": "${CUSTOM_PROVIDER_URL:-http://localhost:11434}",
                "enabled": False,
                "auth": {"type": "none"},
                "rate_limit": {"rpm": 10_000, "tpm": 5_000_000},
                "health_check": {
                    "enabled": True,
                    "interval": 15,
                    "endpoint": "/v1/models",
                },
                "custom_endpoints": {
                    "chat": "/v1/chat/completions",
                    "models": "/v1/models",
                },
                "models": [],
            },
        ],
        "token_factories": {
            "openai": {
                "enabled": True,
                "provider": "openai",
                "sources": ["env", "file"],
                "env_prefix": "OPENAI_TOKEN_",
                "file_path": "./tokens/openai.txt",
            },
            "anthropic": {
                "enabled": True,
                "provider": "anthropic",
                "sources": ["env", "file"],
                "env_prefix": "ANTHROPIC_TOKEN_",
                "file_path": "./tokens/anthropic.txt",
            },
            "google": {
                "enabled": True,
                "provider": "google",
                "sources": ["env", "file"],
                "env_prefix": "GOOGLE_TOKEN_",
                "file_path": "./tokens/google.txt",
            },
        },
    }
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Created starter configuration at {output}")
