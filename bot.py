#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import logging
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

from dotenv import load_dotenv
import telebot
from telebot import types
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from apscheduler.schedulers.background import BackgroundScheduler

# ==================== ВЕБ-СЕРВЕР ДЛЯ RENDER ====================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# ==================== НАСТРОЙКИ ====================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Токен АДМИН-БОТА
SHEET_ID = os.getenv("SHEET_ID")    # ID вашей таблицы clients
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # Ваш Telegram ID

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).sheet1

bot = telebot.TeleBot(BOT_TOKEN)
scheduler = BackgroundScheduler()

# ==================== РАБОТА С ТАБЛИЦЕЙ КЛИЕНТОВ ====================
def get_clients():
    """Возвращает список всех клиентов"""
    rows = sheet.get_all_values()
    if len(rows) <= 1:
        return []
    clients = []
    for r in rows[1:]:
        if len(r) >= 7:
            clients.append({
                "id": r[0],
                "name": r[1],
                "telegram": r[2],
                "bot_token": r[3],
                "sheet_id": r[4],
                "paid_until": r[5],
                "status": r[6]
            })
    return clients

def update_client_status(name, status):
    """Обновляет статус клиента"""
    try:
        cell = sheet.find(name, in_column=2)
        if cell:
            sheet.update_cell(cell.row, 7, status)
            return True
    except:
        pass
    return False

def extend_subscription(name, days):
    """Продлевает подписку на указанное количество дней"""
    try:
        cell = sheet.find(name, in_column=2)
        if cell:
            current = sheet.cell(cell.row, 6).value
            if current:
                dt = datetime.strptime(current, "%d.%m.%Y")
            else:
                dt = datetime.now()
            new_dt = dt + timedelta(days=days)
            new_date = new_dt.strftime("%d.%m.%Y")
            sheet.update_cell(cell.row, 6, new_date)
            sheet.update_cell(cell.row, 7, "Активен")
            return new_date
    except:
        pass
    return None

def add_client(name, telegram, bot_token, sheet_id, paid_until):
    """Добавляет нового клиента"""
    rows = sheet.get_all_values()
    new_id = len(rows)
    sheet.append_row([str(new_id), name, telegram, bot_token, sheet_id, paid_until, "Активен"])
    return new_id

# ==================== ПРОВЕРКА ОПЛАТ ====================
def check_payments():
    """Проверяет сроки оплаты и отправляет уведомления админу"""
    clients = get_clients()
    today = datetime.now().date()
    
    expired = []
    soon_3 = []
    soon_7 = []
    
    for c in clients:
        if c["status"] != "Активен":
            continue
        try:
            paid_date = datetime.strptime(c["paid_until"], "%d.%m.%Y").date()
            days_left = (paid_date - today).days
            
            if days_left < 0:
                expired.append(c)
                # Автоматически меняем статус на Просрочен
                update_client_status(c["name"], "Просрочен")
            elif days_left <= 3:
                soon_3.append(c)
            elif days_left <= 7:
                soon_7.append(c)
        except:
            pass
    
    if expired or soon_3 or soon_7:
        msg = "📅 **Напоминания по оплате:**\n\n"
        
        if expired:
            msg += "🔴 **ПРОСРОЧЕНЫ:**\n"
            for c in expired:
                msg += f"• {c['name']} ({c['telegram']}) — просрочено\n"
            msg += "\n"
        
        if soon_3:
            msg += "🟡 **Заканчивается через 1-3 дня:**\n"
            for c in soon_3:
                msg += f"• {c['name']} ({c['telegram']}) — до {c['paid_until']}\n"
            msg += "\n"
        
        if soon_7:
            msg += "🟢 **Заканчивается через 4-7 дней:**\n"
            for c in soon_7:
                msg += f"• {c['name']} ({c['telegram']}) — до {c['paid_until']}\n"
        
        msg += "\n---\n"
        msg += "💳 **Реквизиты для оплаты:**\n"
        msg += "Сбер: 2202 20XX XXXX 1234\n"
        msg += "Тинькофф: 5536 91XX XXXX 5678\n"
        msg += "ЮMoney: 4100 11XX XXXX 9012"
        
        bot.send_message(ADMIN_ID, msg, parse_mode='Markdown')

# ==================== КОМАНДЫ АДМИН-БОТА ====================
@bot.message_handler(commands=['start', 'help'])
def cmd_start(message):
    if message.chat.id != ADMIN_ID:
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return
    
    msg = "🤖 **Админ-бот CRM Larich Food**\n\n"
    msg += "**Команды:**\n"
    msg += "/clients — список всех клиентов\n"
    msg += "/expired — просроченные клиенты\n"
    msg += "/add Имя @user токен sheet_id ДД.ММ.ГГГГ — добавить клиента\n"
    msg += "/extend Имя 30 — продлить на N дней\n"
    msg += "/block Имя — заблокировать\n"
    msg += "/unblock Имя — разблокировать\n"
    msg += "/remind Имя — напомнить о платеже\n"
    msg += "/check — проверить оплаты сейчас"
    
    bot.send_message(message.chat.id, msg, parse_mode='Markdown')

@bot.message_handler(commands=['clients'])
def cmd_clients(message):
    if message.chat.id != ADMIN_ID:
        return
    
    clients = get_clients()
    if not clients:
        bot.reply_to(message, "📭 Нет клиентов.")
        return
    
    msg = "📋 **Список клиентов:**\n\n"
    for c in clients:
        emoji = "✅" if c["status"] == "Активен" else "❌"
        msg += f"{emoji} {c['name']} ({c['telegram']}) — до {c['paid_until']}\n"
    
    bot.send_message(message.chat.id, msg, parse_mode='Markdown')

@bot.message_handler(commands=['expired'])
def cmd_expired(message):
    if message.chat.id != ADMIN_ID:
        return
    
    clients = get_clients()
    expired = [c for c in clients if c["status"] == "Просрочен"]
    
    if not expired:
        bot.reply_to(message, "✅ Нет просроченных клиентов.")
        return
    
    msg = "🔴 **Просроченные клиенты:**\n\n"
    for c in expired:
        msg += f"• {c['name']} ({c['telegram']}) — {c['paid_until']}\n"
    
    msg += "\n---\n"
    msg += "💳 **Реквизиты для отправки клиенту:**\n"
    msg += "Сбер: 2202 20XX XXXX 1234 (Иван И.)\n"
    msg += "Тинькофф: 5536 91XX XXXX 5678\n"
    msg += "ЮMoney: 4100 11XX XXXX 9012"
    
    bot.send_message(message.chat.id, msg, parse_mode='Markdown')

@bot.message_handler(commands=['add'])
def cmd_add(message):
    if message.chat.id != ADMIN_ID:
        return
    
    parts = message.text.split(maxsplit=5)
    if len(parts) < 6:
        bot.reply_to(message, "❌ Формат: /add Имя @telegram bot_token sheet_id ДД.ММ.ГГГГ")
        return
    
    name = parts[1]
    telegram = parts[2]
    bot_token = parts[3]
    sheet_id = parts[4]
    paid_until = parts[5]
    
    new_id = add_client(name, telegram, bot_token, sheet_id, paid_until)
    bot.reply_to(message, f"✅ Клиент {name} добавлен! ID: {new_id}")

@bot.message_handler(commands=['extend'])
def cmd_extend(message):
    if message.chat.id != ADMIN_ID:
        return
    
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "❌ Формат: /extend Имя 30")
        return
    
    name = parts[1]
    days = int(parts[2])
    
    new_date = extend_subscription(name, days)
    if new_date:
        bot.reply_to(message, f"✅ Подписка {name} продлена до {new_date}")
    else:
        bot.reply_to(message, f"❌ Клиент {name} не найден")

@bot.message_handler(commands=['block'])
def cmd_block(message):
    if message.chat.id != ADMIN_ID:
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Формат: /block Имя")
        return
    
    name = parts[1]
    if update_client_status(name, "Заблокирован"):
        bot.reply_to(message, f"✅ Клиент {name} заблокирован")
    else:
        bot.reply_to(message, f"❌ Клиент {name} не найден")

@bot.message_handler(commands=['unblock'])
def cmd_unblock(message):
    if message.chat.id != ADMIN_ID:
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Формат: /unblock Имя")
        return
    
    name = parts[1]
    if update_client_status(name, "Активен"):
        bot.reply_to(message, f"✅ Клиент {name} разблокирован")
    else:
        bot.reply_to(message, f"❌ Клиент {name} не найден")

@bot.message_handler(commands=['remind'])
def cmd_remind(message):
    if message.chat.id != ADMIN_ID:
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Формат: /remind Имя")
        return
    
    name = parts[1]
    clients = get_clients()
    client = next((c for c in clients if c["name"].lower() == name.lower()), None)
    
    if not client:
        bot.reply_to(message, f"❌ Клиент {name} не найден")
        return
    
    msg = f"📅 Напоминание для {client['name']}:\n\n"
    msg += f"Подписка до: {client['paid_until']}\n"
    msg += f"Статус: {client['status']}\n\n"
    msg += "💳 Реквизиты для оплаты:\n"
    msg += "Сбер: 2202 20XX XXXX 1234\n"
    msg += "Тинькофф: 5536 91XX XXXX 5678"
    
    bot.send_message(message.chat.id, msg)

@bot.message_handler(commands=['check'])
def cmd_check(message):
    if message.chat.id != ADMIN_ID:
        return
    
    check_payments()
    bot.reply_to(message, "✅ Проверка оплат выполнена")

# ==================== ЗАПУСК ====================
def main():
    # Планировщик: проверка каждый день в 10:00
    scheduler.add_job(check_payments, 'cron', hour=10, minute=0)
    scheduler.start()
    
    threading.Thread(target=run_web_server, daemon=True).start()
    logger.info("🤖 Админ-бот запущен...")
    
    bot.polling(none_stop=True)

if __name__ == "__main__":
    import json
    main()
