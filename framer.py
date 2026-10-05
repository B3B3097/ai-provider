# language: Python, file: farmer.py, target: Linux/Windows, Python 3.10+
import asyncio
import httpx
import re
from drissionpage import ChromiumPage, ChromiumOptions
from typing import Optional

class TempMailClient:
    def __init__(self):
        self.session = httpx.AsyncClient(base_url="https://api.mail.tm")
        self.account = None
        self.token = None

    async def create_account(self) -> tuple[str, str]:
        domain_resp = await self.session.get("/domains")
        domain = domain_resp.json()["hydra:member"][0]["domain"]
        username = f"user_{asyncio.get_event_loop().time()}"
        address = f"{username}@{domain}"
        password = "TempPass123!"
        
        await self.session.post("/accounts", json={"address": address, "password": password})
        auth_resp = await self.session.post("/token", json={"address": address, "password": password})
        self.token = auth_resp.json()["token"]
        self.account = {"address": address, "password": password}
        return address, password

    async def get_latest_message(self) -> Optional[dict]:
        headers = {"Authorization": f"Bearer {self.token}"}
        resp = await self.session.get("/messages", headers=headers)
        messages = resp.json()["hydra:member"]
        if not messages:
            return None
        msg_id = messages[0]["id"]
        msg_resp = await self.session.get(f"/messages/{msg_id}", headers=headers)
        return msg_resp.json()

class SMSActivateClient:
    def __init__(self, api_key: str, service: str = "ot", country: str = "0"):
        self.api_key = api_key
        self.service = service
        self.country = country
        self.session = httpx.AsyncClient(base_url="https://api.sms-activate.org/stubs/handler_api.php")

    async def get_number(self) -> tuple[str, str]:
        resp = await self.session.get("/", params={
            "api_key": self.api_key, "action": "getNumber", 
            "service": self.service, "country": self.country
        })
        # Формат ответа: ACCESS_NUMBER:ID_ЗАКАЗА:НОМЕР
        parts = resp.text.split(":")
        if parts[0] == "ACCESS_NUMBER":
            return parts[2], parts[1]
        raise RuntimeError(f"SMS Activate error: {resp.text}")

    async def set_status(self, order_id: str, status: str):
        await self.session.get("/", params={
            "api_key": self.api_key, "action": "setStatus", 
            "status": status, "id": order_id
        })

class ProviderFarmer:
    def __init__(self, sms_api_key: str):
        self.sms_client = SMSActivateClient(sms_api_key)
        self.mail_client = TempMailClient()
        self.browser: Optional[ChromiumPage] = None

    async def setup_browser(self):
        co = ChromiumOptions().set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--no-sandbox")
        self.browser = ChromiumPage(co)

    async def farm_token(self) -> Optional[str]:
        await self.setup_browser()
        email, _ = await self.mail_client.create_account()
        phone, order_id = await self.sms_client.get_number()
        
        try:
            # 1. Переход на страницу регистрации целевого провайдера
            self.browser.get("https://platform.example.com/register") # ЗАМЕНИТЬ НА РЕАЛЬНЫЙ URL
            
            # 2. Заполнение формы (селекторы требуют адаптации под конкретный UI)
            self.browser.ele("@name=email").input(email)
            self.browser.ele("@name=password").input("StrongPassword123!")
            self.browser.ele("@name=phone").input(phone)
            self.browser.ele("@type=submit").click()

            # 3. Ожидание SMS (простой поллинг)
            for _ in range(10):
                await asyncio.sleep(5)
                # Логика проверки кода из SMS или почты. 
                # Для SMS: запросить код у SMS-activate, если он пришел, или ждать ввода вручную в dev-режиме
                # Для почты: проверить self.mail_client.get_latest_message() на наличие кода подтверждения
            
            # 4. Перехват токена из LocalStorage или Network после успешного входа
            # Пример извлечения из LocalStorage:
            token = self.browser.run_js("return localStorage.getItem('auth_token');")
            
            if token and token.startswith("sk-"):
                await self.sms_client.set_status(order_id, "6") # Статус "завершено"
                return token
            
            # Если токен в куках или ответе API, используем перехват сетевых запросов DrissionPage
            # packets = self.browser.listen.start('https://api.example.com/v1/auth')
            # ... логика парсинга
            
        except Exception as e:
            await self.sms_client.set_status(order_id, "8") # Статус "отмена"
            print(f"Farm failed: {e}")
            return None
        finally:
            if self.browser:
                self.browser.quit()
        return None

# Пример запуска
async def main():
    farmer = ProviderFarmer(sms_api_key="YOUR_SMS_ACTIVATE_KEY")
    token = await farmer.farm_token()
    if token:
        print(f"Успешно добыт токен: {token[:10]}...")

if __name__ == "__main__":
    asyncio.run(main())