"""Complete YAML-driven configuration system"""

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Union

import yaml

from .defaults import create_example_config


class AuthType(Enum):
    BEARER = "bearer"
    API_KEY = "api_key"
    CUSTOM_HEADER = "custom_header"
    COOKIE = "cookie"
    QUERY_PARAM = "query_param"
    OAUTH2 = "oauth2"
    JWT = "jwt"
    NONE = "none"


class LoadBalancerStrategy(Enum):
    LEAST_USED = "least_used"
    ROUND_ROBIN = "round_robin"
    WEIGHTED_RANDOM = "weighted_random"
    LATENCY_AWARE = "latency_aware"
    PRIORITY = "priority"
    GEO_AWARE = "geo_aware"


class TokenSource(Enum):
    STATIC = "static"
    ENV = "env"
    FILE = "file"
    VAULT = "vault"
    GENERATED = "generated"
    SCRAPED = "scraped"
    MARKETPLACE = "marketplace"
    OAUTH_FLOW = "oauth_flow"


@dataclass
class AuthConfig:
    type: AuthType = AuthType.BEARER
    header_name: str = "Authorization"
    prefix: str = "Bearer "
    query_param: str = ""
    cookie_name: str = ""
    custom_headers: Dict[str, str] = field(default_factory=dict)
    oauth2: Dict[str, Any] = field(default_factory=dict)
    jwt: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RateLimitConfig:
    rpm: int = 60
    tpm: int = 100_000
    rpd: int = 10_000
    burst_allowance: float = 1.2
    headers: Dict[str, str] = field(default_factory=dict)
    dynamic: bool = True


@dataclass
class RetryConfig:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    retry_on: List[int] = field(default_factory=lambda: [429, 500, 502, 503, 504])
    retry_on_exceptions: List[str] = field(
        default_factory=lambda: ["timeout", "connection"]
    )


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    success_threshold: int = 2
    timeout: int = 60
    half_open_requests: int = 3
    excluded_status_codes: List[int] = field(default_factory=lambda: [401, 403])


@dataclass
class EvasionConfig:
    enabled: bool = True
    rotate_user_agent: bool = True
    user_agents: List[str] = field(default_factory=list)
    rotate_ip: bool = False
    proxy_pool: List[str] = field(default_factory=list)
    proxy_rotation_interval: int = 300
    header_randomization: bool = True
    request_fingerprinting: bool = True
    tls_fingerprint: str = "chrome_120"
    ja3_spoofing: bool = False
    http2_prior_knowledge: bool = True
    connection_reuse: bool = True
    keep_alive_timeout: int = 30
    request_padding: bool = False
    timing_jitter: float = 0.5


@dataclass
class HealthCheckConfig:
    enabled: bool = True
    interval: int = 30
    timeout: float = 10.0
    endpoint: str = "/v1/models"
    method: str = "GET"
    expected_status: int = 200
    expected_keys: List[str] = field(default_factory=list)
    failure_threshold: int = 3
    recovery_threshold: int = 2


@dataclass
class ModelConfig:
    id: str
    provider_model: str
    aliases: List[str] = field(default_factory=list)
    max_tokens: int = 4096
    context_window: int = 8192
    supports_streaming: bool = True
    supports_functions: bool = False
    supports_vision: bool = False
    supports_audio: bool = False
    pricing: Dict[str, float] = field(default_factory=dict)
    capabilities: List[str] = field(default_factory=list)
    deprecated: bool = False
    priority: int = 100
    tags: List[str] = field(default_factory=list)


@dataclass
class ProviderConfig:
    name: str
    display_name: str = ""
    base_url: str = ""
    api_version: str = "v1"
    auth: AuthConfig = field(default_factory=AuthConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    evasion: EvasionConfig = field(default_factory=EvasionConfig)
    health_check: HealthCheckConfig = field(default_factory=HealthCheckConfig)
    models: List[ModelConfig] = field(default_factory=list)
    model_discovery: bool = True
    discovery_interval: int = 3600
    custom_endpoints: Dict[str, str] = field(default_factory=dict)
    request_transform: str = ""
    response_transform: str = ""
    error_mapping: Dict[int, str] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    cookies: Dict[str, str] = field(default_factory=dict)
    timeout: float = 120.0
    connect_timeout: float = 10.0
    max_concurrent: int = 10
    weight: int = 100
    region: str = "global"
    tags: List[str] = field(default_factory=list)
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenFactoryConfig:
    enabled: bool = False
    provider: str = ""
    sources: List[TokenSource] = field(default_factory=list)
    static_tokens: List[str] = field(default_factory=list)
    env_prefix: str = "TOKEN_"
    file_path: str = ""
    vault_config: Dict[str, Any] = field(default_factory=dict)
    generation: Dict[str, Any] = field(default_factory=dict)
    scraping: Dict[str, Any] = field(default_factory=dict)
    marketplace: Dict[str, Any] = field(default_factory=dict)
    oauth_flows: List[Dict[str, Any]] = field(default_factory=list)
    validation: Dict[str, Any] = field(default_factory=dict)
    auto_refresh: bool = True
    refresh_interval: int = 3600
    min_pool_size: int = 5
    max_pool_size: int = 100
    quarantine_on_failure: bool = True
    distribute_across_regions: bool = False


@dataclass
class AutoRegistrationConfig:
    enabled: bool = False
    provider: str = ""
    email_providers: List[str] = field(default_factory=list)
    captcha_provider: str = "none"
    phone_provider: str = "none"
    captcha_api_key: str = ""
    phone_api_key: str = ""
    browser_config: Dict[str, Any] = field(default_factory=dict)
    registration_config: Dict[str, Any] = field(default_factory=dict)
    profile_generator: Dict[str, Any] = field(default_factory=dict)
    min_pool_size: int = 5
    max_pool_size: int = 50
    replenish_interval: int = 300
    max_concurrent_registrations: int = 3
    retry_attempts: int = 3
    account_lifetime: int = 86400


@dataclass
class PublicApiConfig:
    enabled: bool = True
    store_type: str = "memory"
    redis_url: str = ""
    redis_prefix: str = "public_api"
    key_prefix: str = "sk-live"
    default_rpm: int = 60
    default_rpd: int = 10_000
    default_token_limit: int = 1_000_000
    default_ttl_days: int = 0
    issuer_name: str = "Unified LLM Gateway"
    support_url: str = ""
    terms_url: str = ""
    privacy_url: str = ""


@dataclass
class GatewayConfig:
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    workers: int = 1
    api_keys: List[str] = field(default_factory=list)
    admin_keys: List[str] = field(default_factory=list)
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expiry: int = 3600
    cors_origins: List[str] = field(default_factory=lambda: ["*"])
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    public_api: PublicApiConfig = field(default_factory=PublicApiConfig)
    request_timeout: float = 300.0
    max_request_size: int = 50 * 1024 * 1024
    enable_compression: bool = True
    enable_metrics: bool = True
    metrics_path: str = "/metrics"
    health_path: str = "/health"
    docs_enabled: bool = True
    log_level: str = "INFO"
    log_format: str = "json"
    access_log: bool = True
    tls_cert: str = ""
    tls_key: str = ""


@dataclass
class StorageConfig:
    type: str = "redis"
    redis: Dict[str, Any] = field(default_factory=dict)
    file: Dict[str, Any] = field(default_factory=dict)
    postgres: Dict[str, Any] = field(default_factory=dict)
    sqlite: Dict[str, Any] = field(default_factory=dict)
    encryption_key: str = ""
    key_rotation_days: int = 30


@dataclass
class ClusterConfig:
    enabled: bool = False
    node_id: str = ""
    discovery: Dict[str, Any] = field(default_factory=dict)
    sync_interval: int = 60
    token_sharing: bool = True
    model_sharing: bool = True
    metric_sharing: bool = True
    leader_election: bool = True


@dataclass
class MonitoringConfig:
    prometheus: Dict[str, Any] = field(default_factory=dict)
    grafana: Dict[str, Any] = field(default_factory=dict)
    alerting: Dict[str, Any] = field(default_factory=dict)
    logging: Dict[str, Any] = field(default_factory=dict)
    tracing: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineConfig:
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    providers: List[ProviderConfig] = field(default_factory=list)
    token_factories: Dict[str, TokenFactoryConfig] = field(default_factory=dict)
    auto_registration: Dict[str, AutoRegistrationConfig] = field(default_factory=dict)
    load_balancer: LoadBalancerStrategy = LoadBalancerStrategy.LEAST_USED
    global_rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    default_model: str = ""
    fallback_models: List[str] = field(default_factory=list)
    model_routing: Dict[str, List[str]] = field(default_factory=dict)
    custom_routes: List[Dict[str, Any]] = field(default_factory=list)
    middleware: List[Dict[str, Any]] = field(default_factory=list)
    plugins: List[str] = field(default_factory=list)
    environment: str = "production"
    debug: bool = False


def load_config(path: Union[str, Path]) -> EngineConfig:
    """Load full configuration from YAML with env var substitution"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    with open(path, encoding="utf-8") as f:
        content = f.read()

    # Environment variable substitution ${VAR:-default}
    def replace_env(match):
        var_expr = match.group(1)
        if ":-" in var_expr:
            var, default = var_expr.split(":-", 1)
            return os.getenv(var, default)
        return os.getenv(var_expr, match.group(0))

    content = re.sub(r"\$\{([^}]+)\}", replace_env, content)

    data = yaml.safe_load(content) or {}

    # Parse providers
    providers = []
    for p in data.get("providers", []):
        models = [ModelConfig(**m) for m in p.pop("models", [])]
        auth_data = p.pop("auth", {})
        auth_type = AuthType(auth_data.pop("type", "bearer"))
        auth = AuthConfig(type=auth_type, **auth_data)

        rate_limit = RateLimitConfig(**p.pop("rate_limit", {}))
        retry = RetryConfig(**p.pop("retry", {}))
        circuit_breaker = CircuitBreakerConfig(**p.pop("circuit_breaker", {}))
        evasion = EvasionConfig(**p.pop("evasion", {}))
        health_check = HealthCheckConfig(**p.pop("health_check", {}))

        providers.append(
            ProviderConfig(
                models=models,
                auth=auth,
                rate_limit=rate_limit,
                retry=retry,
                circuit_breaker=circuit_breaker,
                evasion=evasion,
                health_check=health_check,
                **p,
            )
        )

    # Parse token factories
    token_factories = {}
    for name, tf in data.get("token_factories", {}).items():
        sources = [TokenSource(s) for s in tf.pop("sources", [])]
        token_factories[name] = TokenFactoryConfig(sources=sources, **tf)

    # Parse auto registration
    auto_registration = {}
    for name, ar in data.get("auto_registration", {}).items():
        auto_registration[name] = AutoRegistrationConfig(**ar)

    # Parse gateway
    gateway_data = data.get("gateway", {})
    gateway_rate_limit = RateLimitConfig(**gateway_data.pop("rate_limit", {}))
    public_api = PublicApiConfig(**gateway_data.pop("public_api", {}))
    gateway = GatewayConfig(
        rate_limit=gateway_rate_limit,
        public_api=public_api,
        **gateway_data,
    )

    # Parse storage
    storage = StorageConfig(**data.get("storage", {}))

    # Parse cluster
    cluster = ClusterConfig(**data.get("cluster", {}))

    # Parse monitoring
    monitoring = MonitoringConfig(**data.get("monitoring", {}))

    # Global rate limit
    global_rl = RateLimitConfig(**data.get("global_rate_limit", {}))

    # Load balancer strategy
    lb_str = data.get("load_balancer", "least_used")
    load_balancer = LoadBalancerStrategy(lb_str)

    return EngineConfig(
        gateway=gateway,
        storage=storage,
        cluster=cluster,
        monitoring=monitoring,
        providers=providers,
        token_factories=token_factories,
        auto_registration=auto_registration,
        load_balancer=load_balancer,
        global_rate_limit=global_rl,
        default_model=data.get("default_model", ""),
        fallback_models=data.get("fallback_models", []),
        model_routing=data.get("model_routing", {}),
        custom_routes=data.get("custom_routes", []),
        middleware=data.get("middleware", []),
        plugins=data.get("plugins", []),
        environment=data.get("environment", "production"),
        debug=data.get("debug", False),
    )


def _legacy_create_example_config(path: Union[str, Path] = "config.yaml"):
    """Generate comprehensive example configuration"""
    config = {
        "environment": "production",
        "debug": False,
        "default_model": "gpt-4o",
        "fallback_models": ["gpt-4-turbo", "claude-3-5-sonnet", "gpt-3.5-turbo"],
        "load_balancer": "latency_aware",
        "model_routing": {
            "gpt-4*": ["openai", "azure"],
            "claude*": ["anthropic"],
            "gemini*": ["google"],
            "*": ["openai", "anthropic", "google", "custom"],
        },
        "gateway": {
            "bind_host": "0.0.0.0",
            "bind_port": 8080,
            "workers": 1,
            "api_keys": ["${GATEWAY_API_KEY:-sk-gateway-dev-key}"],
            "admin_keys": ["${ADMIN_API_KEY:-sk-admin-dev-key}"],
            "jwt_secret": "${JWT_SECRET:-change-me-in-production}",
            "cors_origins": ["*"],
            "rate_limit": {"rpm": 1000, "tpm": 500000},
            "request_timeout": 300,
            "log_level": "INFO",
            "log_format": "json",
            "enable_metrics": True,
            "docs_enabled": True,
        },
        "storage": {"type": "file", "file": {"path": "./tokens/tokens.json"}},
        "cluster": {
            "enabled": False,
            "node_id": "${NODE_ID:-}",
            "discovery": {"type": "static", "nodes": []},
            "sync_interval": 60,
            "token_sharing": True,
        },
        "monitoring": {
            "prometheus": {"enabled": True, "port": 9090},
            "grafana": {"enabled": True, "dashboards_path": "./dashboards"},
            "alerting": {
                "enabled": True,
                "webhook_url": "${ALERT_WEBHOOK:-}",
                "rules": [
                    {
                        "name": "high_error_rate",
                        "expr": "rate(errors[5m]) > 0.1",
                        "severity": "critical",
                    },
                    {
                        "name": "low_token_pool",
                        "expr": "healthy_tokens < 3",
                        "severity": "warning",
                    },
                    {
                        "name": "high_latency",
                        "expr": "p99_latency > 30",
                        "severity": "warning",
                    },
                ],
            },
            "tracing": {
                "enabled": True,
                "jaeger_endpoint": "${JAEGER_ENDPOINT:-http://localhost:14268/api/traces}",
            },
        },
        "providers": [
            {
                "name": "openai",
                "display_name": "OpenAI",
                "base_url": "https://api.openai.com",
                "api_version": "v1",
                "enabled": True,
                "weight": 100,
                "region": "us-east",
                "tags": ["official", "premium"],
                "auth": {
                    "type": "bearer",
                    "header_name": "Authorization",
                    "prefix": "Bearer ",
                },
                "rate_limit": {"rpm": 3000, "tpm": 1000000, "dynamic": True},
                "retry": {"max_attempts": 3, "retry_on": [429, 500, 502, 503, 504]},
                "circuit_breaker": {"failure_threshold": 10, "timeout": 60},
                "evasion": {
                    "enabled": True,
                    "rotate_user_agent": True,
                    "header_randomization": True,
                    "tls_fingerprint": "chrome_120",
                    "http2_prior_knowledge": True,
                },
                "health_check": {
                    "enabled": True,
                    "interval": 30,
                    "endpoint": "/v1/models",
                },
                "model_discovery": True,
                "discovery_interval": 3600,
                "timeout": 120,
                "max_concurrent": 50,
                "models": [
                    {
                        "id": "gpt-4o",
                        "provider_model": "gpt-4o",
                        "aliases": ["gpt-4o-latest"],
                        "max_tokens": 16384,
                        "context_window": 128000,
                        "supports_streaming": True,
                        "supports_vision": True,
                        "pricing": {"input": 5.0, "output": 15.0},
                        "priority": 10,
                        "tags": ["flagship", "vision"],
                    },
                    {
                        "id": "gpt-4o-mini",
                        "provider_model": "gpt-4o-mini",
                        "max_tokens": 16384,
                        "context_window": 128000,
                        "supports_streaming": True,
                        "supports_vision": True,
                        "pricing": {"input": 0.15, "output": 0.6},
                        "priority": 20,
                        "tags": ["cheap", "vision"],
                    },
                    {
                        "id": "gpt-4-turbo",
                        "provider_model": "gpt-4-turbo-preview",
                        "aliases": ["gpt-4-turbo"],
                        "max_tokens": 4096,
                        "context_window": 128000,
                        "supports_streaming": True,
                        "pricing": {"input": 10.0, "output": 30.0},
                        "priority": 30,
                    },
                    {
                        "id": "gpt-3.5-turbo",
                        "provider_model": "gpt-3.5-turbo",
                        "max_tokens": 4096,
                        "context_window": 16384,
                        "supports_streaming": True,
                        "pricing": {"input": 0.5, "output": 1.5},
                        "priority": 50,
                    },
                    {
                        "id": "o1-preview",
                        "provider_model": "o1-preview",
                        "max_tokens": 32768,
                        "context_window": 128000,
                        "supports_streaming": False,
                        "pricing": {"input": 15.0, "output": 60.0},
                        "priority": 5,
                        "tags": ["reasoning"],
                    },
                    {
                        "id": "o1-mini",
                        "provider_model": "o1-mini",
                        "max_tokens": 65536,
                        "context_window": 128000,
                        "supports_streaming": False,
                        "pricing": {"input": 3.0, "output": 12.0},
                        "priority": 15,
                        "tags": ["reasoning", "cheap"],
                    },
                ],
            },
            {
                "name": "anthropic",
                "display_name": "Anthropic",
                "base_url": "https://api.anthropic.com",
                "enabled": True,
                "weight": 90,
                "region": "us-east",
                "tags": ["official", "premium"],
                "auth": {"type": "api_key", "header_name": "x-api-key", "prefix": ""},
                "rate_limit": {"rpm": 1000, "tpm": 500000, "dynamic": True},
                "health_check": {
                    "enabled": True,
                    "interval": 30,
                    "endpoint": "/v1/models",
                },
                "model_discovery": True,
                "models": [
                    {
                        "id": "claude-3-5-sonnet",
                        "provider_model": "claude-3-5-sonnet-20241022",
                        "max_tokens": 8192,
                        "context_window": 200000,
                        "supports_streaming": True,
                        "supports_vision": True,
                        "pricing": {"input": 3.0, "output": 15.0},
                        "priority": 10,
                        "tags": ["flagship", "vision"],
                    },
                    {
                        "id": "claude-3-5-haiku",
                        "provider_model": "claude-3-5-haiku-20241022",
                        "max_tokens": 8192,
                        "context_window": 200000,
                        "supports_streaming": True,
                        "supports_vision": True,
                        "pricing": {"input": 0.8, "output": 4.0},
                        "priority": 20,
                        "tags": ["cheap", "vision", "fast"],
                    },
                    {
                        "id": "claude-3-opus",
                        "provider_model": "claude-3-opus-20240229",
                        "max_tokens": 4096,
                        "context_window": 200000,
                        "supports_streaming": True,
                        "supports_vision": True,
                        "pricing": {"input": 15.0, "output": 75.0},
                        "priority": 5,
                        "tags": ["flagship", "vision"],
                    },
                ],
            },
            {
                "name": "google",
                "display_name": "Google AI",
                "base_url": "https://generativelanguage.googleapis.com",
                "enabled": True,
                "weight": 80,
                "region": "global",
                "tags": ["official"],
                "auth": {"type": "query_param", "query_param": "key"},
                "rate_limit": {"rpm": 1500, "tpm": 1000000},
                "health_check": {
                    "enabled": True,
                    "interval": 60,
                    "endpoint": "/v1/models",
                },
                "models": [
                    {
                        "id": "gemini-1.5-pro",
                        "provider_model": "gemini-1.5-pro",
                        "max_tokens": 8192,
                        "context_window": 2000000,
                        "supports_streaming": True,
                        "supports_vision": True,
                        "pricing": {"input": 3.5, "output": 10.5},
                        "priority": 10,
                        "tags": ["flagship", "vision", "long-context"],
                    },
                    {
                        "id": "gemini-1.5-flash",
                        "provider_model": "gemini-1.5-flash",
                        "max_tokens": 8192,
                        "context_window": 1000000,
                        "supports_streaming": True,
                        "supports_vision": True,
                        "pricing": {"input": 0.075, "output": 0.3},
                        "priority": 20,
                        "tags": ["cheap", "vision", "fast"],
                    },
                    {
                        "id": "gemini-1.0-pro",
                        "provider_model": "gemini-1.0-pro",
                        "max_tokens": 2048,
                        "context_window": 32768,
                        "supports_streaming": True,
                        "pricing": {"input": 0.5, "output": 1.5},
                        "priority": 40,
                    },
                ],
            },
            {
                "name": "azure",
                "display_name": "Azure OpenAI",
                "base_url": "${AZURE_OPENAI_ENDPOINT:-https://your-resource.openai.azure.com}",
                "enabled": True,
                "weight": 95,
                "region": "us-east",
                "tags": ["enterprise", "private"],
                "auth": {"type": "api_key", "header_name": "api-key", "prefix": ""},
                "rate_limit": {"rpm": 2000, "tpm": 800000},
                "custom_endpoints": {
                    "chat": "/openai/deployments/{model}/chat/completions?api-version=2024-02-15-preview",
                    "models": "/openai/deployments?api-version=2024-02-15-preview",
                },
                "models": [
                    {
                        "id": "gpt-4o",
                        "provider_model": "gpt-4o",
                        "max_tokens": 16384,
                        "context_window": 128000,
                        "supports_streaming": True,
                        "supports_vision": True,
                        "priority": 10,
                    },
                    {
                        "id": "gpt-4",
                        "provider_model": "gpt-4",
                        "max_tokens": 8192,
                        "context_window": 128000,
                        "supports_streaming": True,
                        "priority": 20,
                    },
                    {
                        "id": "gpt-35-turbo",
                        "provider_model": "gpt-35-turbo",
                        "max_tokens": 4096,
                        "context_window": 16384,
                        "supports_streaming": True,
                        "priority": 40,
                    },
                ],
            },
            {
                "name": "custom",
                "display_name": "Custom Providers",
                "base_url": "${CUSTOM_PROVIDER_URL:-http://localhost:11434}",
                "enabled": True,
                "weight": 50,
                "region": "local",
                "tags": ["local", "ollama", "vllm", "tgi"],
                "auth": {"type": "none"},
                "rate_limit": {"rpm": 10000, "tpm": 5000000},
                "health_check": {
                    "enabled": True,
                    "interval": 15,
                    "endpoint": "/api/tags",
                },
                "model_discovery": True,
                "discovery_interval": 60,
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
                "sources": ["env", "file", "generated"],
                "env_prefix": "OPENAI_TOKEN_",
                "file_path": "./tokens/openai.txt",
                "generation": {
                    "method": "api_key_rotation",
                    "account_credentials": "${OPENAI_ACCOUNT_CREDS:-}",
                    "max_keys_per_account": 5,
                },
                "validation": {"test_model": "gpt-3.5-turbo", "test_prompt": "ping"},
                "auto_refresh": True,
                "refresh_interval": 1800,
                "min_pool_size": 10,
                "max_pool_size": 100,
            },
            "anthropic": {
                "enabled": True,
                "provider": "anthropic",
                "sources": ["env", "file"],
                "env_prefix": "ANTHROPIC_TOKEN_",
                "file_path": "./tokens/anthropic.txt",
                "validation": {"test_model": "claude-3-haiku", "test_prompt": "ping"},
                "auto_refresh": True,
                "refresh_interval": 1800,
                "min_pool_size": 5,
                "max_pool_size": 50,
            },
            "google": {
                "enabled": True,
                "provider": "google",
                "sources": ["env", "file"],
                "env_prefix": "GOOGLE_TOKEN_",
                "file_path": "./tokens/google.txt",
                "validation": {"test_model": "gemini-1.5-flash", "test_prompt": "ping"},
                "auto_refresh": True,
                "refresh_interval": 1800,
                "min_pool_size": 5,
                "max_pool_size": 50,
            },
            "scraped": {
                "enabled": False,
                "provider": "custom",
                "sources": ["scraped"],
                "scraping": {
                    "targets": [
                        {
                            "url": "https://platform.openai.com/api-keys",
                            "selectors": {"token": "[data-testid='api-key']"},
                            "login_flow": "manual",
                        },
                        {
                            "url": "https://console.anthropic.com/settings/keys",
                            "selectors": {"token": ".api-key-value"},
                            "login_flow": "manual",
                        },
                    ],
                    "headless": True,
                    "browser": "chromium",
                    "interval": 3600,
                },
                "validation": {"test_prompt": "ping"},
                "min_pool_size": 3,
                "max_pool_size": 20,
            },
        },
        "auto_registration": {
            "openai": {
                "enabled": False,
                "provider": "openai",
                "email_providers": ["mail_tm", "temp_mail", "onesec_mail"],
                "captcha_provider": "2captcha",
                "phone_provider": "5sim",
                "captcha_api_key": "${CAPTCHA_API_KEY:-}",
                "phone_api_key": "${PHONE_API_KEY:-}",
                "browser_config": {
                    "headless": True,
                    "proxy": "${REG_PROXY:-}",
                    "block_resources": ["image", "font", "media", "stylesheet"],
                },
                "registration_config": {
                    "register_url": "https://platform.openai.com/signup",
                    "login_url": "https://platform.openai.com/login",
                    "email_selector": "input[type='email']",
                    "password_selector": "input[type='password']",
                    "submit_selector": "button[type='submit']",
                    "captcha_selectors": {
                        "recaptcha": ".g-recaptcha",
                        "hcaptcha": ".h-captcha",
                    },
                    "verification_selectors": {
                        "email_code": "input[name='code']",
                        "phone_code": "input[name='phone_code']",
                    },
                    "token_extractors": [
                        {"type": "cookie", "name": "__Secure-session"},
                        {"type": "local_storage", "key": "accessToken"},
                        {
                            "type": "regex",
                            "pattern": "sk-[a-zA-Z0-9]{48}",
                            "source": "response",
                        },
                    ],
                },
                "profile_generator": {
                    "locale": "en_US",
                    "name_format": "first_last",
                    "password_length": 16,
                },
                "min_pool_size": 5,
                "max_pool_size": 20,
                "replenish_interval": 300,
                "max_concurrent_registrations": 2,
                "retry_attempts": 3,
                "account_lifetime": 86400,
            },
            "anthropic": {
                "enabled": False,
                "provider": "anthropic",
                "email_providers": ["mail_tm", "temp_mail"],
                "captcha_provider": "2captcha",
                "phone_provider": "none",
                "captcha_api_key": "${CAPTCHA_API_KEY:-}",
                "browser_config": {"headless": True},
                "registration_config": {
                    "register_url": "https://console.anthropic.com/login",
                    "email_selector": "input[type='email']",
                    "password_selector": "input[type='password']",
                    "submit_selector": "button[type='submit']",
                    "token_extractors": [
                        {"type": "cookie", "name": "session"},
                        {"type": "local_storage", "key": "apiKey"},
                    ],
                },
                "min_pool_size": 3,
                "max_pool_size": 15,
            },
        },
    }

    Path(path).write_text(
        yaml.dump(config, sort_keys=False, allow_unicode=True, width=200),
        encoding="utf-8",
    )
    print(f"Created comprehensive config at {path}")


if __name__ == "__main__":
    import sys

    create_example_config(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
