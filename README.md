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
- OpenAI-compatible `GET /v1/models` and `POST /v1/chat/completions`
- SSE streaming responses
- CLI commands for configuration generation, validation, and startup
- Tests for configuration, storage, failover, source loading, and authorization

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

## Model Discovery

Configured models are always available immediately. When a provider has `model_discovery: true` and at least one available credential, the gateway refreshes its catalog in the background according to `discovery_interval`.

Trigger a refresh manually:

```powershell README.md
Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:8080/admin/models/discover?provider=openai' `
  -Headers @{ Authorization = 'Bearer replace-with-admin-key' }
```

Omit `provider` to refresh every enabled provider. Newly discovered model IDs are merged by `provider_model`, so repeated refreshes do not duplicate catalog entries.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Public provider and pool status |
| `GET` | `/metrics` | Public JSON metrics |
| `GET` | `/v1/models` | Configured model catalog |
| `POST` | `/v1/chat/completions` | Completion or SSE stream |
| `POST` | `/admin/models/discover` | Refresh provider model catalogs |
| `POST` | `/admin/tokens` | Import an authorized token |
| `DELETE` | `/admin/tokens/{provider}/{fingerprint}` | Remove a token |

## Tests

```powershell README.md
python -m compileall -q token_abuse_engine
python -m pytest -q token_abuse_engine\tests
```