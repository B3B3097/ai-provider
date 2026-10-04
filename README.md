# Unified LLM Gateway

YAML-driven multi-provider API gateway with token pooling, failover, health checks, persistent storage, streaming, and an OpenAI-compatible HTTP API.

## Implemented

- YAML configuration with `${VAR}` and `${VAR:-default}` substitution
- OpenAI-compatible, Anthropic, Gemini, Azure, and custom providers
- Model aliases and provider routing
- Least-used, round-robin, weighted-random, latency-aware, and priority token selection
- Retry through another available credential after `429` and transient failures
- Cooldowns, concurrency limits, health checks, and per-provider circuit breakers
- Memory, atomic JSON, and Redis storage
- Import of operator-provided credentials from `STATIC`, `ENV`, and `FILE` sources
- FastAPI gateway with separate client and admin keys
- Operator-issued public API keys stored as SHA-256 hashes only
- Per-key RPM, RPD, and lifetime token limits
- Public key issue, list, and revoke admin endpoints
- Client usage reporting after normal and streaming completions
- OpenAI-compatible `GET /v1/models` with pricing, limits, capabilities, and metadata
- OpenAI-compatible `POST /v1/chat/completions` and SSE streaming responses
- Docker/Compose production stack with Redis and Caddy automatic TLS
- CLI commands for configuration generation, validation, and startup
- Tests for configuration, storage, failover, source loading, authorization, and public API behavior

Automatic account creation, disposable-mail registration, CAPTCHA solving, and third-party account farming are not part of this implementation. The gateway operates on credentials explicitly imported by the operator.

## Project Layout

```text README.md
token_abuse_engine/
  __init__.py
  __main__.py
  adapters.py
  config.py
  gateway.py
  main.py
  models.py
  pool.py
  storage.py
  token_sources.py
  public_api.py
tests/
config.yaml
requirements.txt
pyproject.toml
```

## Setup

```powershell README.md
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install pytest pytest-asyncio
```

## Configuration

Generate and validate the YAML configuration:

```powershell README.md
python -m token_abuse_engine.main init-config config.yaml --force
python -m token_abuse_engine.main validate config.yaml
```

Use local JSON storage:

```yaml README.md
storage:
  type: file
  file:
    path: ./tokens/tokens.json
```

Load operator-provided credentials from environment variables and a text file:

```yaml README.md
token_factories:
  openai:
    enabled: true
    provider: openai
    sources: [env, file]
    env_prefix: OPENAI_TOKEN_
    file_path: ./tokens/openai.txt
```

Text files accept one credential per line; empty lines and lines beginning with `#` are ignored. JSON lists are also accepted.

## Run

```powershell README.md
python -m token_abuse_engine.main serve config.yaml
python -m token_abuse_engine.main serve config.yaml --host 127.0.0.1 --port 8090
python -m token_abuse_engine.main serve config.yaml --reload
```

Install the console entrypoint:

```powershell README.md
python -m pip install -e .
token-engine validate config.yaml
token-engine serve config.yaml
```

## Import an Authorized Token

```powershell README.md
$headers = @{ Authorization = 'Bearer replace-with-admin-key' }
$body = @{ provider = 'openai'; value = 'replace-with-provider-token' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8080/admin/tokens' -Headers $headers -ContentType 'application/json' -Body $body
```

The endpoint returns a fingerprint and masked value. It does not return the full credential.

## Call the Gateway

```powershell README.md
$headers = @{ Authorization = 'Bearer replace-with-client-key' }
$body = @{
  model = 'gpt-4o'
  messages = @(@{ role = 'user'; content = 'ping' })
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8080/v1/chat/completions' -Headers $headers -ContentType 'application/json' -Body $body
```

Streaming request:

```powershell README.md
curl.exe -N http://127.0.0.1:8080/v1/chat/completions `
  -H "Authorization: Bearer replace-with-client-key" `
  -H "Content-Type: application/json" `
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"ping"}],"stream":true}'
```

## Public Client API Keys

Public keys are issued by an operator and stored only as SHA-256 hashes. The raw key is returned exactly once by `POST /admin/api-keys`; list responses never include the hash or secret.

Issue a key:

```powershell README.md
$body = @{
  name = 'customer-a'
  rpm_limit = 60
  rpd_limit = 10000
  token_limit = 1000000
  ttl_days = 30
  metadata = @{ plan = 'standard' }
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:8080/admin/api-keys' `
  -Headers @{ Authorization = 'Bearer replace-with-admin-key' } `
  -ContentType 'application/json' `
  -Body $body
```

List keys without secrets:

```powershell README.md
Invoke-RestMethod `
  -Uri 'http://127.0.0.1:8080/admin/api-keys' `
  -Headers @{ Authorization = 'Bearer replace-with-admin-key' }
```

Revoke a key:

```powershell README.md
Invoke-WebRequest -Method Delete `
  -Uri 'http://127.0.0.1:8080/admin/api-keys/key_replace-me' `
  -Headers @{ Authorization = 'Bearer replace-with-admin-key' }
```

Read current counters and limits:

```powershell README.md
Invoke-RestMethod `
  -Uri 'http://127.0.0.1:8080/v1/usage' `
  -Headers @{ Authorization = 'Bearer replace-with-issued-client-key' }
```

A limit of `0` means unlimited. Requests exceeding RPM/RPD return `429` with `Retry-After`. Token usage is recorded after successful normal and streaming completions when the upstream response includes usage metadata.

## Model Discovery

Configured models are always available immediately. When a provider has `model_discovery: true` and at least one available credential, the gateway refreshes its catalog in the background according to `discovery_interval`.

Trigger a refresh manually:

```powershell README.md
Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:8080/admin/models/discover?provider=openai' `
  -Headers @{ Authorization = 'Bearer replace-with-admin-key' }
```

Omit `provider` to refresh every enabled provider. Newly discovered model IDs are merged by `provider_model`, so repeated refreshes do not duplicate catalog entries.

## Production Deployment

The production stack runs the gateway as a non-root container, Redis with AOF persistence, and Caddy as the TLS reverse proxy. Caddy requires inbound TCP ports 80/443 and UDP 443 open, plus a DNS `A`/`AAAA` record for `PUBLIC_DOMAIN` pointing to the host.

Create a local `.env` file (it is gitignored) with at least:

```dotenv .env
PUBLIC_DOMAIN=api.example.com
PUBLIC_ORIGIN=https://api.example.com
GATEWAY_API_KEY=generated-random-client-bootstrap-key
ADMIN_API_KEY=generated-random-admin-key
OPENAI_TOKEN_PRIMARY=authorized-upstream-credential
ANTHROPIC_TOKEN_PRIMARY=authorized-upstream-credential
GOOGLE_TOKEN_PRIMARY=authorized-upstream-credential
SUPPORT_URL=https://example.com/support
TERMS_URL=https://example.com/terms
PRIVACY_URL=https://example.com/privacy
```

Generate management secrets with:

```powershell README.md
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Validate and start:

```powershell README.md
python -m token_abuse_engine.main validate config.production.yaml
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Caddy obtains and renews TLS certificates automatically. The gateway itself listens only on the internal Compose network and is exposed through Caddy. Redis is also internal-only and uses the `redis-data` volume.

Required external resources before accepting public traffic:

- a reachable production host or managed container platform;
- DNS plus TCP 80/443 and UDP 443 ingress/egress;
- Redis (the bundled service or managed Redis);
- authorized OpenAI, Anthropic, and/or Google upstream credentials;
- final billing policy, support, terms, and privacy URLs.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Public provider and pool status |
| `GET` | `/metrics` | Public JSON metrics |
| `GET` | `/v1/models` | Model catalog with pricing, limits, capabilities, and metadata |
| `GET` | `/v1/usage` | Current authenticated client key usage and quotas |
| `POST` | `/v1/chat/completions` | Completion or SSE stream |
| `POST` | `/admin/api-keys` | Issue a public client key (raw key returned once) |
| `GET` | `/admin/api-keys` | List public key metadata without secrets/hashes |
| `DELETE` | `/admin/api-keys/{id}` | Revoke a public client key |
| `POST` | `/admin/models/discover` | Refresh provider model catalogs |
| `POST` | `/admin/tokens` | Import an authorized token |
| `DELETE` | `/admin/tokens/{provider}/{fingerprint}` | Remove a token |

## Tests

```powershell README.md
python -m compileall -q token_abuse_engine
python -m pytest -q
```