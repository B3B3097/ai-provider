# Использование Unified LLM Gateway в VS Code

Это отдельная русская инструкция для запуска шлюза, выпуска client API key и подключения к расширению VS Code, поддерживающему OpenAI-compatible API.

> **Важно о безопасности.** Реальный ключ нельзя добавлять в README, описание GitHub, YAML-коммит или issues. Рабочий секрет должен храниться только в локальном `.env`, переменной окружения или secret storage расширения. В документации используется маскированный пример `sk-live-…`; этот ключ не является рабочим.

## Как получить hosted client key

1. Напишите владельцу репозитория через доступный вам контакт.
2. Укажите:
   - имя или псевдоним;
   - для чего нужен ключ;
   - нужные модели;
   - желаемые RPM/RPD и token budget;
   - срок действия.
3. Владелец выпускает отдельный ключ формата `sk-live-…` через admin endpoint и отправляет его вам по защищённому каналу.
4. Клиент сохраняет ключ в Continue secret storage или локальном `.env`.
5. Для отзыва владелец вызывает `DELETE /admin/api-keys/{id}`.

Готовый шаблон запроса:

```text docs/KEY_REQUEST_RU.md
Имя: <имя или псевдоним>
Назначение: <например, Continue в VS Code>
Модели: <gpt-4o-mini, claude-3-5-haiku, ...>
RPM: <например, 60>
RPD: <например, 10000>
Token budget: <например, 1000000>
Срок действия: <например, 30 дней>
```

Один и тот же ключ не должен передаваться нескольким пользователям: индивидуальный ключ позволяет применять отдельные квоты и отозвать доступ только у конкретного клиента.

## Возможности

- единый OpenAI-compatible адрес для OpenAI, Anthropic, Gemini и custom providers;
- выпуск и revoke клиентских API keys;
- RPM, RPD и lifetime token limits;
- локальный просмотр usage каждого ключа;
- streaming completions;
- health и metrics без отдельного API key.

Подробный API-контракт находится в [README.md](../README.md).

## 1. Подготовить upstream-токены

Проект работает только с авторизованными оператором credentials. Локальные Compose-профили читают их из корневого `.env` по префиксам из `config.production.yaml`:

```dotenv .env
OPENAI_TOKEN_PRIMARY=ваш-авторизованный-openai-api-key
ANTHROPIC_TOKEN_PRIMARY=ваш-авторизованный-anthropic-api-key
GOOGLE_TOKEN_PRIMARY=ваш-авторизованный-google-api-key
```

Можно указать несколько credentials для одного provider, например `OPENAI_TOKEN_PRIMARY`, `OPENAI_TOKEN_SECONDARY` и так далее. Аналогично используются prefixes `ANTHROPIC_TOKEN_` и `GOOGLE_TOKEN_`.

Файл `.env` уже находится в `.gitignore`. Никогда не добавляйте его в commit. Если provider не используется, его credential можно не задавать.

## 2. Локально запустить gateway (для владельца)

Если вам уже выдан hosted client key, этот раздел выполнять не нужно: перейдите к моделям, Continue и просмотру usage. Разделы 2–3 нужны владельцу сервиса или локальному разработчику.

Есть два YAML-профиля:

- `examples/vscode/run-token-engine.yaml` — локальный запуск шлюза;
- `examples/vscode/show-key-usage.yaml` — запрос usage по ключу и вывод счётчиков.

### Запуск из VS Code

1. Откройте репозиторий в VS Code.
2. Откройте **Terminal → New Terminal**.
3. Установите локальные admin и bootstrap client keys только на время текущего terminal-сеанса:

```powershell examples/vscode/run-token-engine.yaml
$env:ADMIN_API_KEY = "local-admin-$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
$env:GATEWAY_API_KEY = "local-client-$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

4. Запустите gateway:

```powershell examples/vscode/run-token-engine.yaml
docker compose -f examples/vscode/run-token-engine.yaml up --build -d
```

5. Проверьте состояние:

```powershell examples/vscode/run-token-engine.yaml
docker compose -f examples/vscode/run-token-engine.yaml ps
Invoke-RestMethod http://127.0.0.1:8080/health
```

Остановить gateway:

```powershell examples/vscode/run-token-engine.yaml
docker compose -f examples/vscode/run-token-engine.yaml down
```

## 3. Выпустить client API key

Client key создаётся admin endpoint. Raw key возвращается **только один раз** и затем хранится шлюзом в виде SHA-256 hash.

```powershell examples/vscode/run-token-engine.yaml
$adminHeaders = @{ Authorization = "Bearer $env:ADMIN_API_KEY" }
$body = @{
  name = 'vscode-local'
  rpm_limit = 60
  rpd_limit = 10000
  token_limit = 1000000
  metadata = @{ purpose = 'vscode' }
} | ConvertTo-Json -Depth 4
$issued = Invoke-RestMethod `
  -Method Post `
  -Uri 'http://127.0.0.1:8080/admin/api-keys' `
  -Headers $adminHeaders `
  -ContentType 'application/json' `
  -Body $body

$issued.api_key
```

Скопируйте значение в менеджер паролей или secret storage расширения. Не вставляйте его в:

- GitHub description;
- issue или pull request;
- committed YAML/JSON;
- скриншот без маскирования;
- chat/log, доступный посторонним.

Если ключ утерян или опубликован, немедленно отзовите его:

```powershell examples/vscode/run-token-engine.yaml
Invoke-WebRequest -Method Delete `
  -Uri "http://127.0.0.1:8080/admin/api-keys/$($issued.id)" `
  -Headers $adminHeaders
```

## 4. Доступные модели

Production-конфигурация предоставляет следующие baseline-модели:

| Model ID | Provider | Назначение |
|---|---|---|
| `gpt-4o-mini` | OpenAI | быстрый и экономичный универсальный вариант |
| `gpt-4o` | OpenAI | мультимодальная модель общего назначения |
| `claude-3-5-haiku` | Anthropic | быстрый и экономичный вариант Claude |
| `claude-3-5-sonnet` | Anthropic | более мощный универсальный вариант Claude |
| `gemini-1.5-flash` | Google | быстрый вариант с большим context window |
| `gemini-1.5-pro` | Google | более мощный вариант с длинным контекстом |

Фактическая доступность модели зависит от выданного upstream credential и состояния token pool. Model discovery может добавить дополнительные IDs. Актуальный список всегда возвращает:

```powershell examples/vscode/show-key-usage.yaml
Invoke-RestMethod `
  -Uri 'http://127.0.0.1:8080/v1/models' `
  -Headers @{ Authorization = "Bearer $env:GATEWAY_API_KEY" }
```

Для hosted gateway замените `127.0.0.1:8080` на домен, который вам выдал владелец.

## 5. Подключить hosted key к Continue

Расширение называется **Continue**. Оно использует OpenAI-compatible provider, поэтому все модели gateway подключаются к одному endpoint.

### Local config

Создайте `%USERPROFILE%\.continue\config.yaml`:

```yaml ~/.continue/config.yaml
name: Unified LLM Gateway
version: 1.0.0
schema: v1
models:
  - name: GPT-4o mini
    provider: openai
    model: gpt-4o-mini
    apiBase: "http://127.0.0.1:8080/v1"
    apiKey: "${{ secrets.GATEWAY_API_KEY }}"
    roles: [chat, edit]
  - name: GPT-4o
    provider: openai
    model: gpt-4o
    apiBase: "http://127.0.0.1:8080/v1"
    apiKey: "${{ secrets.GATEWAY_API_KEY }}"
    roles: [chat, edit]
  - name: Claude 3.5 Haiku
    provider: openai
    model: claude-3-5-haiku
    apiBase: "http://127.0.0.1:8080/v1"
    apiKey: "${{ secrets.GATEWAY_API_KEY }}"
    roles: [chat, edit]
  - name: Claude 3.5 Sonnet
    provider: openai
    model: claude-3-5-sonnet
    apiBase: "http://127.0.0.1:8080/v1"
    apiKey: "${{ secrets.GATEWAY_API_KEY }}"
    roles: [chat, edit]
  - name: Gemini 1.5 Flash
    provider: openai
    model: gemini-1.5-flash
    apiBase: "http://127.0.0.1:8080/v1"
    apiKey: "${{ secrets.GATEWAY_API_KEY }}"
    roles: [chat, edit]
  - name: Gemini 1.5 Pro
    provider: openai
    model: gemini-1.5-pro
    apiBase: "http://127.0.0.1:8080/v1"
    apiKey: "${{ secrets.GATEWAY_API_KEY }}"
    roles: [chat, edit]
```

Для hosted deployment во всех шести блоках замените:

```yaml ~/.continue/config.yaml
apiBase: "https://<gateway-domain>/v1"
```

### Secret key

Полученный от владельца ключ не записывайте в `config.yaml`. Создайте `%USERPROFILE%\.continue\.env`:

```dotenv ~/.continue/.env
GATEWAY_API_KEY=sk-live-полученный-от-владельца-ключ
```

Либо вставьте ключ через Continue UI в поле secret/API key. Файл `.env` не должен попадать в Git.

После перезапуска VS Code выберите нужную модель в списке Continue и отправьте тестовый chat/edit prompt.

Gateway предоставляет `/v1/chat/completions`; функции autocomplete, которым нужен отдельный FIM endpoint, могут работать не у всех моделей.

## 6. Показать количество токенов на ключе

Второй YAML-профиль запрашивает `GET /v1/usage` и выводит:

- количество использованных токенов;
- оставшийся token budget;
- текущие RPM/RPD;
- общее количество запросов;
- лимиты ключа.

```powershell examples/vscode/show-key-usage.yaml
$env:GATEWAY_URL = 'http://host.docker.internal:8080'
$secureKey = Read-Host 'Вставьте выданный hosted client key' -AsSecureString
$env:GATEWAY_API_KEY = [System.Net.NetworkCredential]::new('', $secureKey).Password
docker compose -f examples/vscode/show-key-usage.yaml run --rm usage
```

Пример вывода:

```text examples/vscode/show-key-usage.yaml
Ключ: vscode-local (key_1234567890abcdef)
Использовано токенов: 12 345
Осталось токенов: 987 655
Текущий RPM: 2
Текущий RPD: 7
Всего запросов: 8
Лимиты: 60 RPM, 10000 RPD, 1000000 токенов
```

Это динамическое локальное отображение. Реальное количество токенов не публикуется в описании GitHub, потому что оно постоянно меняется и может раскрыть активность клиента.

## 7. Быстрая проверка из PowerShell

Список моделей:

```powershell examples/vscode/show-key-usage.yaml
Invoke-RestMethod `
  -Uri 'http://127.0.0.1:8080/v1/models' `
  -Headers @{ Authorization = "Bearer $env:GATEWAY_API_KEY" }
```

Обычный completion:

```powershell examples/vscode/show-key-usage.yaml
$headers = @{ Authorization = "Bearer $env:GATEWAY_API_KEY" }
$payload = @{
  model = 'gpt-4o-mini'
  messages = @(@{ role = 'user'; content = 'Ответь одним словом: работает?' })
} | ConvertTo-Json -Depth 5
Invoke-RestMethod `
  -Method Post `
  -Uri 'http://127.0.0.1:8080/v1/chat/completions' `
  -Headers $headers `
  -ContentType 'application/json' `
  -Body $payload
```

## 8. Если что-то не работает

### `401 Invalid API key`

Проверьте, что используете client key, а не admin key, и заголовок имеет вид:

```text Authorization: Bearer sk-live-...
```

### `429 quota_exceeded`

Достигнут RPM, RPD или token budget. Проверьте:

```powershell examples/vscode/show-key-usage.yaml
docker compose -f examples/vscode/show-key-usage.yaml run --rm usage
```

### `503 pool_unavailable`

Нет доступного authorized upstream credential. Проверьте variables в локальном `.env` с префиксами `OPENAI_TOKEN_`, `ANTHROPIC_TOKEN_`, `GOOGLE_TOKEN_`.

### VS Code не видит gateway

Проверьте, что контейнер запущен и endpoint отвечает:

```powershell examples/vscode/run-token-engine.yaml
Invoke-RestMethod http://127.0.0.1:8080/health
```

Если gateway запущен в WSL или удалённо, замените `127.0.0.1` на фактический адрес сервера и настройте HTTPS/reverse proxy для публичного доступа.
