# language: Python, file: auto_bootstrap_full.py
# *Полный бутстрап: запуск, генерация ключа, генерация Continue конфига, инжект в README*

import os
import sys
import time
import json
import httpx
import secrets
import subprocess
import re
from pathlib import Path

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", secrets.token_urlsafe(32))
GATEWAY_URL = "http://127.0.0.1:8080"
README_PATH = Path("README.md")

def start_gateway():
    print("[Bootstrap] Инициализация шлюза...")
    process = subprocess.Popen(
        [sys.executable, "-m", "token_abuse_engine.main", "serve", "config.yaml"],
        env={**os.environ, "ADMIN_API_KEY": ADMIN_KEY},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    for _ in range(30):
        try:
            if httpx.get(f"{GATEWAY_URL}/health", timeout=2.0).status_code == 200:
                print("[Bootstrap] Шлюз онлайн.")
                return process
        except httpx.RequestError:
            time.sleep(1)
    raise RuntimeError("Шлюз не ответил на /health за 30 секунд.")

def generate_public_key():
    headers = {"Authorization": f"Bearer {ADMIN_KEY}", "Content-Type": "application/json"}
    payload = {
        "name": "continue-vscode-client",
        "rpm_limit": 120,
        "rpd_limit": 50000,
        "token_limit": 0,
        "ttl_days": 365
    }
    resp = httpx.post(f"{GATEWAY_URL}/admin/api-keys", headers=headers, json=payload, timeout=5.0)
    resp.raise_for_status()
    return resp.json().get("raw_key")

def generate_continue_config(api_key: str, base_url: str = "http://127.0.0.1:8080/v1"):
    """Генерирует JSON-конфиг для расширения Continue в VS Code."""
    config = {
        "models": [
            {
                "title": "Unified Gateway (GPT-4o)",
                "provider": "openai",
                "model": "gpt-4o",
                "apiKey": api_key,
                "apiBase": base_url
            },
            {
                "title": "Unified Gateway (Claude 3.5 Sonnet)",
                "provider": "openai", # Continue использует openai-compatible для шлюзов
                "model": "claude-3-5-sonnet",
                "apiKey": api_key,
                "apiBase": base_url
            }
        ],
        "tabAutocompleteModel": {
            "title": "Fast Autocomplete",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "apiKey": api_key,
            "apiBase": base_url
        }
    }
    return json.dumps(config, indent=4)

def inject_to_readme(api_key: str, continue_config: str):
    if not README_PATH.exists():
        print("[Bootstrap] README.md не найден. Создаю базовый.")
        README_PATH.write_text("# Unified LLM Gateway\n\n", encoding="utf-8")

    content = README_PATH.read_text(encoding="utf-8")
    
    # Блок для вставки
    injection_block = f"""
<!-- AUTO_GENERATED_CONFIG_START -->
## 🚀 Быстрый старт и Подключение

### Публичный API Ключ
Используйте этот ключ для авторизации в шлюзе:
```text
{api_key}
```

### Подключение к Continue (VS Code)
1. Откройте настройки Continue (`config.json`).
2. Вставьте следующую конфигурацию:

```json
{continue_config}
```

### Ручной запрос (cURL)
```bash
curl -X POST "{GATEWAY_URL}/v1/chat/completions" \\
  -H "Authorization: Bearer {api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{{"model": "gpt-4o", "messages": [{{"role": "user", "content": "Hello"}}]}}'
```
<!-- AUTO_GENERATED_CONFIG_END -->
"""

    # Заменяем старый блок или добавляем в конец
    pattern = re.compile(r"<!-- AUTO_GENERATED_CONFIG_START -->.*?<!-- AUTO_GENERATED_CONFIG_END -->", re.DOTALL)
    if pattern.search(content):
        new_content = pattern.sub(injection_block.strip(), content)
    else:
        new_content = content.rstrip() + "\n\n" + injection_block.strip() + "\n"
        
    README_PATH.write_text(new_content, encoding="utf-8")
    print("[Bootstrap] README.md успешно обновлен.")

def main():
    gateway_process = start_gateway()
    
    try:
        public_key = generate_public_key()
        print(f"[Bootstrap] Ключ сгенерирован: {public_key[:15]}...")
        
        continue_config = generate_continue_config(public_key)
        inject_to_readme(public_key, continue_config)
        
        print("\n[Bootstrap] Система полностью развернута. Шлюз активен.")
        print("[Bootstrap] Нажмите Ctrl+C для остановки.")
        gateway_process.wait()
    except KeyboardInterrupt:
        print("\n[Bootstrap] Остановка шлюза.")
        gateway_process.terminate()
    except Exception as e:
        print(f"[Bootstrap] Критическая ошибка: {e}")
        gateway_process.terminate()
        sys.exit(1)

if __name__ == "__main__":
    main()