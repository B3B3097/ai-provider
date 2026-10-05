# language: Python, file: auto_farm_engine.py, target: Windows/Linux, Python 3.10+
# *Движок автоматического фарминга и инжекта токенов в шлюз*

import asyncio
import httpx
import re
from pathlib import Path
from drissionpage import ChromiumPage, ChromiumOptions

class TempMailClient:
    def __init__(self):
        self.session = httpx.AsyncClient(base_url="https://api.mail.tm", timeout=10.0)
        self.address = None
        self.token = None

    async def create_account(self) -> str:
        resp = await self.session.get("/domains")
        domain = resp.json()["hydra:member"][0]["domain"]
        username = f"farm_{asyncio.get_event_loop().time()}"
        self.address = f"{username}@{domain}"
        password = "FarmPass123!"
        
        await self.session.post("/accounts", json={"address": self.address, "password": password})
        auth = await self.session.post("/token", json={"address": self.address, "password": password})
        self.token = auth.json()["token"]
        return self.address

    async def check_for_token(self, keyword: str = "verify") -> str | None:
        if not self.token:
            return None
        headers = {"Authorization": f"Bearer {self.token}"}
        messages = await self.session.get("/messages", headers=headers)
        for msg in messages.json()["hydra:member"]:
            if keyword.lower() in msg["subject"].lower() or keyword.lower() in msg["intro"].lower():
                full_msg = await self.session.get(f"/messages/{msg['id']}", headers=headers)
                text = full_msg.json()["text"]
                # Извлечение 6-значного кода или ссылки
                code_match = re.search(r'\b\d{6}\b', text)
                if code_match:
                    return code_match.group(0)
        return None

class FarmEngine:
    def __init__(self, sms_api_key: str, target_file: Path):
        self.sms_api_key = sms_api_key
        self.target_file = target_file
        self.target_file.parent.mkdir(parents=True, exist_ok=True)
        self.mail = TempMailClient()
        self.browser: ChromiumPage | None = None

    async def setup_browser(self):
        co = ChromiumOptions().set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-setuid-sandbox")
        self.browser = ChromiumPage(co)

    async def get_sms_number(self) -> tuple[str, str]:
        # Интеграция с SMS-Activate (стандартный API)
        url = "https://api.sms-activate.org/stubs/handler_api.php"
        params = {"api_key": self.sms_api_key, "action": "getNumber", "service": "ot", "country": "0"}
        resp = await httpx.AsyncClient().get(url, params=params)
        parts = resp.text.split(":")
        if parts[0] == "ACCESS_NUMBER":
            return parts[2], parts[1] # number, order_id
        raise RuntimeError(f"SMS Activate failed: {resp.text}")

    async def append_token_to_pool(self, token: str, provider: str = "openai"):
        """Записывает токен в файл, который мониторит token_sources.py шлюза."""
        with open(self.target_file, "a", encoding="utf-8") as f:
            f.write(f"{token}\n")
        print(f"[FarmEngine] Токен успешно добавлен в пул: {self.target_file}")

    async def run_farm_cycle(self):
        await self.setup_browser()
        email = await self.mail.create_account()
        phone, order_id = await self.get_sms_number()
        print(f"[FarmEngine] Почта: {email}, Телефон: {phone}")

        try:
            # 1. Переход на регистрацию (заменить на реальный URL целевого провайдера)
            self.browser.get("https://platform.example.com/register")
            
            # 2. Заполнение полей (селекторы требуют адаптации под конкретный UI)
            self.browser.ele("@name=email", timeout=5).input(email)
            self.browser.ele("@name=password", timeout=5).input("StrongFarmPass123!")
            self.browser.ele("@name=phone", timeout=5).input(phone)
            self.browser.ele("@type=submit", timeout=5).click()

            # 3. Ожидание кода подтверждения (поллинг почты)
            code = None
            for _ in range(12): # 60 секунд ожидания
                await asyncio.sleep(5)
                code = await self.mail.check_for_token("verify")
                if code:
                    break
            
            if code:
                print(f"[FarmEngine] Код получен: {code}")
                # Ввод кода в браузере (адаптировать селектор)
                # self.browser.ele("@name=code").input(code)
                # self.browser.ele("@type=submit").click()
                pass

            # 4. Перехват токена из LocalStorage после успешного входа
            await asyncio.sleep(3)
            token = self.browser.run_js("return localStorage.getItem('auth_token') || localStorage.getItem('apiKey');")
            
            if token and token.startswith("sk-"):
                await self.append_token_to_pool(token)
                # Подтверждение заказа в SMS-Activate
                await httpx.AsyncClient().get("https://api.sms-activate.org/stubs/handler_api.php", 
                                              params={"api_key": self.sms_api_key, "action": "setStatus", "status": "6", "id": order_id})
                return True
            else:
                print("[FarmEngine] Токен не найден в LocalStorage. Требуется корректировка селекторов или перехват network-трафика.")
                await httpx.AsyncClient().get("https://api.sms-activate.org/stubs/handler_api.php", 
                                              params={"api_key": self.sms_api_key, "action": "setStatus", "status": "8", "id": order_id})
                return False

        except Exception as e:
            print(f"[FarmEngine] Ошибка цикла: {e}")
            return False
        finally:
            if self.browser:
                self.browser.quit()

async def main():
    # Заменить на реальный ключ SMS-Activate и путь из config.yaml
    engine = FarmEngine(
        sms_api_key="YOUR_SMS_ACTIVATE_API_KEY", 
        target_file=Path("./data/incoming/openai.txt")
    )
    
    # Запуск непрерывного цикла фарминга
    while True:
        success = await engine.run_farm_cycle()
        if success:
            print("[FarmEngine] Цикл завершен успешно. Ожидание перед следующим...")
        else:
            print("[FarmEngine] Цикл не удался. Повтор через 30 секунд...")
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())