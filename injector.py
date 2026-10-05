# language: Python, file: injector.py, target: Linux/Windows, Python 3.10+
import httpx
import asyncio

class TokenInjector:
    def __init__(self, gateway_url: str, admin_api_key: str):
        self.gateway_url = gateway_url.rstrip("/")
        self.admin_api_key = admin_api_key
        self.client = httpx.AsyncClient(timeout=10.0)

    async def inject(self, provider: str, token_value: str) -> bool:
        headers = {
            "Authorization": f"Bearer {self.admin_api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "provider": provider,
            "value": token_value
        }
        
        try:
            resp = await self.client.post(
                f"{self.gateway_url}/admin/tokens", 
                headers=headers, 
                json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            print(f"[Injector] Успешно добавлен токен. Fingerprint: {data.get('fingerprint')}")
            return True
        except httpx.HTTPStatusError as e:
            print(f"[Injector] Ошибка HTTP {e.response.status_code}: {e.response.text}")
            return False
        except Exception as e:
            print(f"[Injector] Критическая ошибка: {e}")
            return False

async def test_inject():
    injector = TokenInjector(
        gateway_url="http://127.0.0.1:8080", 
        admin_api_key="generated-random-admin-key" # ЗАМЕНИТЬ НА РЕАЛЬНЫЙ КЛЮЧ ИЗ .ENV
    )
    await injector.inject("openai", "sk-farmed-token-1234567890")

if __name__ == "__main__":
    asyncio.run(test_inject())