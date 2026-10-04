# Заявка на hosted client API key

Скопируйте шаблон и отправьте владельцу репозитория через доступный защищённый контакт. Не отправляйте password, OAuth refresh token или другие credentials для входа.

```text docs/KEY_REQUEST_RU.md
Имя: <имя или псевдоним>
Контакт для выдачи ключа: <email/Telegram/другой защищённый канал>
Назначение: <например, Continue в VS Code>
Модели: <gpt-4o-mini, gpt-4o, claude-3-5-haiku, claude-3-5-sonnet, gemini-1.5-flash, gemini-1.5-pro>
RPM: <например, 60>
RPD: <например, 10000>
Token budget: <например, 1000000>
Срок действия: <например, 30 дней>
```

## Правила

- Один пользователь получает отдельный ключ формата `sk-live-…`.
- Raw key показывается только при выпуске и не публикуется в GitHub.
- Клиент хранит его в Continue secret storage или `%USERPROFILE%\.continue\.env`.
- Владелец может изменить квоты или отозвать ключ через `DELETE /admin/api-keys/{id}`.
- При утечке сразу сообщите владельцу и попросите revoke.

## Baseline-модели

| Model ID | Provider |
|---|---|
| `gpt-4o-mini` | OpenAI |
| `gpt-4o` | OpenAI |
| `claude-3-5-haiku` | Anthropic |
| `claude-3-5-sonnet` | Anthropic |
| `gemini-1.5-flash` | Google |
| `gemini-1.5-pro` | Google |

Фактический список зависит от upstream credentials и model discovery. Актуальные модели доступны через `GET /v1/models`.
