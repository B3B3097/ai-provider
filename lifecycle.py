# language: Python, file: lifecycle.py, target: Linux/Windows, Python 3.10+
import httpx
import asyncio
import json

class LifecycleManager:
    def __init__(self, gateway_url: str, admin_api_key: str, storage_path: str = "./tokens/tokens.json"):
        self.gateway_url = gateway_url.rstrip("/")
        self.admin_api_key = admin_api_key
        self.storage_path = storage_path
        self.client = httpx.AsyncClient(timeout=5.0)

    async def get_all_tokens(self) -> list[dict]:
        # Чтение из локального JSON (если используется file storage)
        # Для Redis потребуется aioredis
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("tokens", [])
        except FileNotFoundError:
            return []

    async def delete_token(self, provider: str, fingerprint: str) -> bool:
        headers = {"Authorization": f"Bearer {self.admin_api_key}"}
        try:
            resp = await self.client.delete(
                f"{self.gateway_url}/admin/tokens/{provider}/{fingerprint}",
                headers=headers
            )
            if resp.status_code in (200, 204):
                print(f"[Lifecycle] Удален мертвый токен: {provider}/{fingerprint}")
                return True
            return False
        except Exception as e:
            print(f"[Lifecycle] Ошибка удаления: {e}")
            return False

    async def health_check_loop(self, interval: int = 300):
        """Периодическая проверка активных токенов тестовым запросом."""
        while True:
            tokens = await self.get_all_tokens()
            for token_data in tokens:
                provider = token_data.get("provider")
                fingerprint = token_data.get("fingerprint")
                
                # Тестовый запрос через шлюз с использованием админского ключа или специального test-ключа
                # В реальной реализации лучше использовать выделенный test-client-key с лимитом 1 RPM
                headers = {"Authorization": "Bearer TEST_CLIENT_KEY"}
                try:
                    resp = await self.client.post(
                        f"{self.gateway_url}/v1/chat/completions",
                        headers=headers,
                        json={
                            "model": "gpt-3.5-turbo", # Или актуальная модель провайдера
                            "messages": [{"role": "user", "content": "ping"}],
                            "max_tokens": 5
                        }
                    )
                    if resp.status_code in (401, 403):
                        print(f"[Lifecycle] Обнаружен невалидный токен {fingerprint}. Планируется удаление.")
                        await self.delete_token(provider, fingerprint)
                except Exception:
                    pass # Игнорируем сетевые сбои, фокус на HTTP статусах
            
            await asyncio.sleep(interval)

async def main():
    manager = LifecycleManager(
        gateway_url="http://127.0.0.1:8080",
        admin_api_key="generated-random-admin-key"
    )
    await manager.health_check_loop()

if __name__ == "__main__":
    asyncio.run(main())