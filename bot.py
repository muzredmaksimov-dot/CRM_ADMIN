#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import json
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

# ==================== ВЕБ-СЕРВЕР ====================
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
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("SHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

# Пробуем открыть лист
try:
    sheet = client.open_by_key(SHEET_ID).worksheet("Лист1")
except:
    sheet = client.open_by_key(SHEET_ID).sheet1

bot = telebot.TeleBot(BOT_TOKEN)
scheduler = BackgroundScheduler()

user_state = {}
user_data = {}

# ==================== РАБОТА С ТАБЛИЦЕЙ ====================
def get_clients():
    rows = sheet.get_all_values()
    if len(rows) <= 1:
        return []
    clients = []
    for r in rows[1:]:
        if len(r) >= 1 and r[0]:
            clients.append({
                "id": r[0],
                "name": r[1] if len(r) > 1 else "",
                "activity": r[2] if len(r) > 2 else "",
                "phone": r[3] if len(r) > 3 else "",
                "status": r[4] if len(r) > 4 else "",
                "paid_until": r[5] if len(r) > 5 else "",
                "notes": r[6] if len(r) > 6 else ""
            })
    return clients

def get_client_by_id(client_id):
    clients = get_clients()
    for c in clients:
        if c["id"] == str(client_id):
            return c
    return None

def get_client_by_name(name):
    clients = get_clients()
    for c in clients:
        if c["name"].lower() == name.lower():
            return c
    return None

def update_client_field(client_id, field_col, value):
    try:
        cell = sheet.find(str(client_id), in_column=1)
        if cell:
            sheet.update_cell(cell.row, field_col, value)
            return True
    except:
        pass
    return False

def update_client_status(client_id, status, paid_until=None):
    try:
        cell = sheet.find(str(client_id), in_column=1)
        if cell:
            sheet.update_cell(cell.row, 5, status)
            if paid_until:
                sheet.update_cell(cell.row, 6, paid_until)
            return True
    except:
        pass
    return False

def add_client(name, activity, phone):
    rows = sheet.get_all_values()
    new_id = str(len(rows))
    today = datetime.now()
    test_until = (today + timedelta(days=30)).strftime("%d.%m.%Y")
    sheet.append_row([new_id, name, activity, phone, "Тест", test_until, ""])
    return new_id

def calculate_days_left(paid_until):
    if not paid_until:
        return None
    try:
        dt = datetime.strptime(paid_until, "%d.%m.%Y").date()
        today = datetime.now().date()
        return (dt - today).days
    except:
        return None

# ==================== ПРОВЕРКА И УВЕДОМЛЕНИЯ ====================
def check_payments():
    clients = get_clients()
    today = datetime.now().date()
    
    expired = []
    soon_3 = []
    soon_7 = []
    
    for c in clients:
        if c["status"] not in ["Активен", "Тест"]:
            continue
        try:
            paid_date = datetime.strptime(c["paid_until"], "%d.%m.%Y").date()
            days_left = (paid_date - today).days
            
            if days_left < 0:
                expired.append(c)
                update_client_status(c["id"], "Просрочен", c["paid_until"])
            elif days_left <= 3:
                soon_3.append(c)
            elif days_left <= 7:
                soon_7.append(c)
        except:
            pass
    
    if not expired and not soon_3 and not soon_7:
        return
    
    msg = "📅 **Напоминания по оплате:**\n\n"
    
    if expired:
        msg += "🔴 **ПРОСРОЧЕНЫ:**\n"
        for c in expired:
            msg += f"• {c['name']} ({c['activity']}) — {c['phone']}\n"
        msg += "\n"
    
    if soon_3:
        msg += "🟡 **Заканчивается через 1-3 дня:**\n"
        for c in soon_3:
            msg += f"• {c['name']} ({c['activity']}) — до {c['paid_until']}\n"
        msg += "\n"
    
    if soon_7:
        msg += "🟢 **Заканчивается через 4-7 дней:**\n"
        for c in soon_7:
            msg += f"• {c['name']} ({c['activity']}) — до {c['paid_until']}\n"
        msg += "\n"
    
    bot.send_message(ADMIN_ID, msg, parse_mode='Markdown')

# ==================== КНОПКИ ====================
def main_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("➕ Добавить клиента", "📋 Все клиенты")
    kb.row("🔍 Поиск")
    return kb

def client_card_keyboard(client_id):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🔄 Тестовый период 30 дней", callback_data=f"status_test_{client_id}"),
        types.InlineKeyboardButton("✅ Оплачен на 30 дней", callback_data=f"status_paid_{client_id}"),
        types.InlineKeyboardButton("❌ Просрочен / не активен", callback_data=f"status_expired_{client_id}"),
        types.InlineKeyboardButton("💳 Отправить реквизиты ЕРИП", callback_data=f"erip_{client_id}"),
        types.InlineKeyboardButton("📝 Добавить / изменить заметку", callback_data=f"note_{client_id}"),
        types.InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_client_{client_id}")
    )
    return kb

def back_to_client_keyboard(client_id):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 Назад к клиенту", callback_data=f"view_{client_id}"))
    kb.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu"))
    return kb

# ==================== КОМАНДЫ ====================
@bot.message_handler(commands=['start'])
def cmd_start(message):
    if message.chat.id != ADMIN_ID:
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return
    
    bot.send_message(
        message.chat.id,
        "🏠 **Админ-панель CRM**\n\nВыберите действие:",
        reply_markup=main_menu_keyboard(),
        parse_mode='Markdown'
    )

# ==================== ОБРАБОТКА СООБЩЕНИЙ ====================
@bot.message_handler(func=lambda m: True)
def handle_message(message):
    if message.chat.id != ADMIN_ID:
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return
    
    chat_id = message.chat.id
    text = message.text
    
    if chat_id not in user_state:
        user_state[chat_id] = None
        user_data[chat_id] = {}
    
    state = user_state.get(chat_id)
    
    if text == "➕ Добавить клиента":
        user_state[chat_id] = "WAIT_NAME"
        bot.send_message(chat_id, "👤 Введите имя клиента:")
        return
    
    elif text == "📋 Все клиенты":
        show_all_clients(chat_id)
        return
    
    elif text == "🔍 Поиск":
        user_state[chat_id] = "WAIT_SEARCH"
        bot.send_message(chat_id, "🔍 Введите имя клиента:")
        return
    
    elif text == "🏠 Главное меню":
        user_state[chat_id] = None
        bot.send_message(chat_id, "Главное меню:", reply_markup=main_menu_keyboard())
        return
    
    # Обработка состояний
    if state == "WAIT_NAME":
        user_data[chat_id]["new_name"] = text
        user_state[chat_id] = "WAIT_ACTIVITY"
        bot.send_message(chat_id, "📋 Введите вид деятельности (например: Копчение мяса):")
    
    elif state == "WAIT_ACTIVITY":
        user_data[chat_id]["new_activity"] = text
        user_state[chat_id] = "WAIT_PHONE"
        bot.send_message(chat_id, "📞 Введите телефон клиента:")
    
    elif state == "WAIT_PHONE":
        name = user_data[chat_id].get("new_name", "")
        activity = user_data[chat_id].get("new_activity", "")
        phone = text
        
        client_id = add_client(name, activity, phone)
        
        bot.send_message(
            chat_id,
            f"✅ Клиент **{name}** добавлен!\nID: {client_id}\nТестовый период: 30 дней",
            reply_markup=main_menu_keyboard(),
            parse_mode='Markdown'
        )
        user_state[chat_id] = None
        user_data[chat_id] = {}
    
    elif state == "WAIT_SEARCH":
        client = get_client_by_name(text)
        if client:
            show_client_card(chat_id, client, new_message=True)
        else:
            bot.send_message(chat_id, f"❌ Клиент **{text}** не найден.", parse_mode='Markdown')
        user_state[chat_id] = None
    
    elif state == "EDIT_NAME":
        client_id = user_data[chat_id].get("edit_client_id")
        if update_client_field(client_id, 2, text):
            bot.send_message(chat_id, f"✅ Имя изменено на {text}")
        user_state[chat_id] = None
        show_client_by_id(chat_id, client_id)
    
    elif state == "EDIT_ACTIVITY":
        client_id = user_data[chat_id].get("edit_client_id")
        if update_client_field(client_id, 3, text):
            bot.send_message(chat_id, f"✅ Деятельность изменена")
        user_state[chat_id] = None
        show_client_by_id(chat_id, client_id)
    
    elif state == "EDIT_PHONE":
        client_id = user_data[chat_id].get("edit_client_id")
        if update_client_field(client_id, 4, text):
            bot.send_message(chat_id, f"✅ Телефон изменён на {text}")
        user_state[chat_id] = None
        show_client_by_id(chat_id, client_id)
    
    elif state == "EDIT_NOTE":
        client_id = user_data[chat_id].get("edit_client_id")
        if update_client_field(client_id, 7, text):
            bot.send_message(chat_id, f"✅ Заметка сохранена")
        user_state[chat_id] = None
        show_client_by_id(chat_id, client_id)

def show_all_clients(chat_id):
    clients = get_clients()
    if not clients:
        bot.send_message(chat_id, "📭 Нет клиентов.", reply_markup=main_menu_keyboard())
        return
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    for c in clients:
        status_emoji = "🟢" if c["status"] == "Активен" else "🟡" if c["status"] == "Тест" else "🔴"
        days_left = calculate_days_left(c["paid_until"])
        days_text = f" ({days_left} дн)" if days_left is not None else ""
        btn_text = f"{status_emoji} {c['name']} ({c['activity']}){days_text}"
        kb.add(types.InlineKeyboardButton(btn_text, callback_data=f"view_{c['id']}"))
    
    kb.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu"))
    bot.send_message(chat_id, "📋 **Все клиенты:**", reply_markup=kb, parse_mode='Markdown')

def show_client_card(chat_id, client, new_message=False):
    status_emoji = "🟢" if client["status"] == "Активен" else "🟡" if client["status"] == "Тест" else "🔴"
    days_left = calculate_days_left(client["paid_until"])
    
    msg = f"👤 **{client['name']}**\n"
    msg += f"📋 {client['activity']}\n"
    msg += f"📞 {client['phone']}\n"
    msg += f"📅 Статус: {status_emoji} {client['status']}"
    if client["paid_until"]:
        msg += f" (до {client['paid_until']})"
        if days_left is not None:
            if days_left < 0:
                msg += f" — просрочено на {abs(days_left)} дн."
            else:
                msg += f" — осталось {days_left} дн."
    if client["notes"]:
        msg += f"\n\n📝 **Заметки:**\n{client['notes']}"
    
    if new_message:
        msg_sent = bot.send_message(chat_id, msg, reply_markup=client_card_keyboard(client['id']), parse_mode='Markdown')
        user_data[chat_id]["last_msg_id"] = msg_sent.message_id
    else:
        bot.edit_message_text(msg, chat_id, message_id=user_data[chat_id].get("last_msg_id"), 
                              reply_markup=client_card_keyboard(client['id']), parse_mode='Markdown')

def show_client_by_id(chat_id, client_id):
    client = get_client_by_id(client_id)
    if client:
        show_client_card(chat_id, client, new_message=True)

# ==================== INLINE-КНОПКИ ====================
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "⛔ Доступ запрещён.", show_alert=True)
        return
    
    chat_id = call.message.chat.id
    data = call.data
    
    user_data[chat_id]["last_msg_id"] = call.message.message_id
    
    # Главное меню
    if data == "main_menu":
        bot.edit_message_text("🏠 Главное меню:", chat_id, call.message.message_id)
        bot.send_message(chat_id, "Выберите действие:", reply_markup=main_menu_keyboard())
        bot.answer_callback_query(call.id)
        return
    
    # Просмотр клиента
    if data.startswith("view_"):
        client_id = data.split("_")[1]
        client = get_client_by_id(client_id)
        if client:
            show_client_card(chat_id, client)
        else:
            bot.answer_callback_query(call.id, "Клиент не найден")
    
    # Тестовый период 30 дней
    elif data.startswith("status_test_"):
        client_id = data.split("_")[2]
        today = datetime.now()
        test_until = (today + timedelta(days=30)).strftime("%d.%m.%Y")
        if update_client_status(client_id, "Тест", test_until):
            bot.answer_callback_query(call.id, "✅ Тестовый период активирован на 30 дней")
            client = get_client_by_id(client_id)
            if client:
                show_client_card(chat_id, client)
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка")
    
    # Оплачен на 30 дней
    elif data.startswith("status_paid_"):
        client_id = data.split("_")[2]
        today = datetime.now()
        paid_until = (today + timedelta(days=30)).strftime("%d.%m.%Y")
        if update_client_status(client_id, "Активен", paid_until):
            bot.answer_callback_query(call.id, "✅ Статус изменён на «Активен» (30 дней)")
            client = get_client_by_id(client_id)
            if client:
                show_client_card(chat_id, client)
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка")
    
    # Просрочен / не активен
    elif data.startswith("status_expired_"):
        client_id = data.split("_")[2]
        if update_client_status(client_id, "Просрочен"):
            bot.answer_callback_query(call.id, "✅ Статус изменён на «Просрочен»")
            client = get_client_by_id(client_id)
            if client:
                show_client_card(chat_id, client)
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка")
    
    # Реквизиты ЕРИП
    elif data.startswith("erip_"):
        client_id = data.split("_")[1]
        client = get_client_by_id(client_id)
        if client:
            msg = f"💳 **Реквизиты для оплаты через ЕРИП**\n\n"
            msg += f"👤 Клиент: {client['name']}\n\n"
            msg += "🏦 **Банк:** Беларусбанк / ЕРИП\n"
            msg += "📌 **Код услуги:** 1234567\n"
            msg += "👤 **Получатель:** ИП Иванов И.И.\n"
            msg += "💵 **Сумма:** 50 BYN (30 дней)\n\n"
            msg += "**Инструкция:**\n"
            msg += "1. ЕРИП → Интернет-магазины/сервисы\n"
            msg += "2. Введите код услуги: 1234567\n"
            msg += "3. Введите сумму: 50.00\n"
            msg += "4. Оплатите\n\n"
            msg += "✅ После оплаты нажмите «Оплачен на 30 дней»"
            
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔙 Назад к клиенту", callback_data=f"view_{client_id}"))
            
            bot.edit_message_text(msg, chat_id, call.message.message_id, reply_markup=kb, parse_mode='Markdown')
        bot.answer_callback_query(call.id)
    
    # Добавить заметку
    elif data.startswith("note_"):
        client_id = data.split("_")[1]
        user_state[chat_id] = "EDIT_NOTE"
        user_data[chat_id]["edit_client_id"] = client_id
        bot.edit_message_text(
            "📝 Введите заметку (или '-' чтобы очистить):",
            chat_id, call.message.message_id,
            reply_markup=back_to_client_keyboard(client_id)
        )
        bot.answer_callback_query(call.id)
    
    # Редактирование клиента
    elif data.startswith("edit_client_"):
        client_id = data.split("_")[2]
        client = get_client_by_id(client_id)
        if client:
            kb = types.InlineKeyboardMarkup(row_width=1)
            kb.add(
                types.InlineKeyboardButton("👤 Изменить имя", callback_data=f"edit_name_{client_id}"),
                types.InlineKeyboardButton("📋 Изменить деятельность", callback_data=f"edit_activity_{client_id}"),
                types.InlineKeyboardButton("📞 Изменить телефон", callback_data=f"edit_phone_{client_id}"),
                types.InlineKeyboardButton("🔙 Назад", callback_data=f"view_{client_id}")
            )
            bot.edit_message_text(
                f"✏️ Редактирование: {client['name']}",
                chat_id, call.message.message_id, reply_markup=kb
            )
        bot.answer_callback_query(call.id)
    
    elif data.startswith("edit_name_"):
        client_id = data.split("_")[2]
        user_state[chat_id] = "EDIT_NAME"
        user_data[chat_id]["edit_client_id"] = client_id
        bot.edit_message_text(
            "👤 Введите новое имя:", chat_id, call.message.message_id,
            reply_markup=back_to_client_keyboard(client_id)
        )
        bot.answer_callback_query(call.id)
    
    elif data.startswith("edit_activity_"):
        client_id = data.split("_")[2]
        user_state[chat_id] = "EDIT_ACTIVITY"
        user_data[chat_id]["edit_client_id"] = client_id
        bot.edit_message_text(
            "📋 Введите новую деятельность:", chat_id, call.message.message_id,
            reply_markup=back_to_client_keyboard(client_id)
        )
        bot.answer_callback_query(call.id)
    
    elif data.startswith("edit_phone_"):
        client_id = data.split("_")[2]
        user_state[chat_id] = "EDIT_PHONE"
        user_data[chat_id]["edit_client_id"] = client_id
        bot.edit_message_text(
            "📞 Введите новый телефон:", chat_id, call.message.message_id,
            reply_markup=back_to_client_keyboard(client_id)
        )
        bot.answer_callback_query(call.id)

# ==================== ЗАПУСК ====================
def main():
    scheduler.add_job(check_payments, 'cron', hour=10, minute=0)
    scheduler.start()
    
    threading.Thread(target=run_web_server, daemon=True).start()
    logger.info("🤖 Админ-бот запущен...")
    
    bot.polling(none_stop=True)

if __name__ == "__main__":
    main()
