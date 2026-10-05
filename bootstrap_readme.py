# language: Python, file: bootstrap_readme.py, target: Windows/Linux, Python 3.10+
# *Генерация публичного ключа и автоматическое обновление README.md*

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
    print("[Bootstrap] Запуск шлюза...")
    process = subprocess.Popen(
        [sys.executable, "-m", "token_abuse_engine.main", "serve", "config.yaml"],
        env={**os.environ, "ADMIN_API_KEY": ADMIN_KEY},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    for _ in range(20):
        try:
            if httpx.get(f"{GATEWAY_URL}/health", timeout=2.0).status_code == 200:
                print("[Bootstrap] Шлюз онлайн.")
                return process
        except httpx.RequestError:
            time.sleep(1)
    raise RuntimeError("Шлюз не запустился.")

def generate_public_key():
    headers = {"Authorization": f"Bearer {ADMIN_KEY}", "Content-Type": "application/json"}
    payload = {"name": "continue-vscode-client", "rpm_limit": 120, "rpd_limit": 50000, "token_limit": 0, "ttl_days": 365}
    resp = httpx.post(f"{GATEWAY_URL}/admin/api-keys", headers=headers, json=payload, timeout=5.0)
    resp.raise_for_status()
    return resp.json().get("raw_key")

def inject_to_readme(api_key: str):
    if not README_PATH.exists():
        README_PATH.write_text("# Unified LLM Gateway\n\n", encoding="utf-8")

    content = README_PATH.read_text(encoding="utf-8")
    
    continue_config = json.dumps({
        "models": [
            {"title": "Unified Gateway (GPT-4o)", "provider": "openai", "model": "gpt-4o", "apiKey": api_key, "apiBase": f"{GATEWAY_URL}/v1"},
            {"title": "Unified Gateway (Claude 3.5)", "provider": "openai", "model": "claude-3-5-sonnet", "apiKey": api_key, "apiBase": f"{GATEWAY_URL}/v1"}
        ],
        "tabAutocompleteModel": {"title": "Fast Autocomplete", "provider": "openai", "model": "gpt-4o-mini", "apiKey": api_key, "apiBase": f"{GATEWAY_URL}/v1"}
    }, indent=4)

    injection_block = f"""
<!-- AUTO_GENERATED_CONFIG_START -->
## 🚀 Подключение Continue (VS Code)

Публичный ключ шлюза:
```text
{api_key}
```

Конфигурация для `config.json` в Continue:
```json
{continue_config}
```
<!-- AUTO_GENERATED_CONFIG_END -->
"""

    pattern = re.compile(r"<!-- AUTO_GENERATED_CONFIG_START -->.*?<!-- AUTO_GENERATED_CONFIG_END -->", re.DOTALL)
    new_content = pattern.sub(injection_block.strip(), content) if pattern.search(content) else content.rstrip() + "\n\n" + injection_block.strip() + "\n"
    
    README_PATH.write_text(new_content, encoding="utf-8")
    print("[Bootstrap] README.md обновлен.")

def main():
    process = start_gateway()
    try:
        key = generate_public_key()
        print(f"[Bootstrap] Ключ сгенерирован: {key}")
        inject_to_readme(key)
        print("[Bootstrap] Система развернута. Шлюз активен.")
        process.wait()
    except KeyboardInterrupt:
        process.terminate()

if __name__ == "__main__":
    main()