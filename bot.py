import os
import time
import csv
import requests
import yfinance as yf
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
import threading
import psycopg2
from psycopg2.extras import RealDictCursor

# Импортируем веб-сервер для поддержания активности на Render
from keep_alive import keep_alive
# Импорт базы знаний
from knowledge import FINANCIAL_DATA

# --- НАСТРОЙКИ БАЗЫ ДАННЫХ ---
DB_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    try:
        conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print("Ошибка подключения к БД:", e)
        return None

def init_user_in_db(telegram_id):
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO users (telegram_id, monthly_budget) 
                VALUES (%s, %s) 
                ON CONFLICT (telegram_id) DO NOTHING;
            """, (telegram_id, 10000.00))
            conn.commit()
            cur.close()
            conn.close()
            return True
        except Exception as e:
            print("Ошибка при записи юзера:", e)
    return False

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
token = os.environ.get("BOT_TOKEN")
if not token:
    raise ValueError("Не найден токен бота! Проверь переменные окружения BOT_TOKEN на Render.")

bot = telebot.TeleBot(token)

# --- БЛОК С НАСТРОЙКАМИ GOOGLE SHEETS И КЭШИРОВАНИЕМ ---
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRitIhrxkXddq7kpf1Oy3qHXxSjD2u-8I0-deeQNOVLnhqTpFVjCtKE0t_ohYT4fvRKbvtX7kdOArWm/pub?output=csv"
GLOSSARY_CACHE = {}

def update_glossary():
    global GLOSSARY_CACHE
    try:
        response = requests.get(SHEET_CSV_URL, timeout=10)
        if response.status_code != 200:
            return False
            
        response.encoding = 'utf-8'
        lines = response.text.splitlines()
        reader = csv.reader(lines)
        new_glossary = {}
        next(reader, None)

        for row in reader:
            if len(row) >= 2 and row[0].strip():
                keys = tuple([k.strip().strip(' "\'').lower() for k in row[0].split(',') if k.strip()])
                definition = row[1].strip()
                if keys:
                    new_glossary[keys] = definition
                    
        if new_glossary:
            GLOSSARY_CACHE = new_glossary
            print(f"✅ Словарь обновлен! Терминов: {len(GLOSSARY_CACHE)}")
            return True
        return False
    except Exception as e:
        print(f"Ошибка загрузки таблицы: {e}")
        return False

update_glossary()

# --- ЛОГИКА МЕНЮ ---
def get_main_menu():
    markup = InlineKeyboardMarkup()
    
    # 1. Кнопки на всю ширину (Категории)
    btn_articles = InlineKeyboardButton("📚 Статьи и база знаний", callback_data="menu_articles")
    btn_tables = InlineKeyboardButton("📊 Полезные таблицы", callback_data="menu_tables")
    markup.add(btn_articles)
    markup.add(btn_tables)
    
    # 2. Кнопки 2x2 (Инструменты)
    btn_calc = InlineKeyboardButton(
        "🧮 Калькулятор", 
        web_app=WebAppInfo(url="https://antonpankov1.github.io/finance-il-bot/calculator.html")
    )
    btn_tlush = InlineKeyboardButton("📄 Чтение тлуша", callback_data="read_tlush")
    
    btn_rates = InlineKeyboardButton("💱 Курс валют", callback_data="show_rates")
    btn_coinkeeper = InlineKeyboardButton(
        "💰 CoinKeeper", 
        web_app=WebAppInfo(url="https://твоя-ссылка-на-coinkeeper.com") # TODO: Заменить URL
    )
    
    markup.row(btn_calc, btn_tlush)
    markup.row(btn_rates, btn_coinkeeper)
    
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    init_user_in_db(message.from_user.id) # Регистрируем юзера при старте
    bot.send_message(
        message.chat.id,
        "Привет! Я твой финансовый навигатор по Израилю. Выбери нужный раздел:",
        reply_markup=get_main_menu()
    )

@bot.message_handler(commands=['update'])
def force_update_glossary(message):
    bot.send_message(message.chat.id, "🔄 Скачиваю свежие данные...")
    if update_glossary():
        bot.send_message(message.chat.id, f"✅ Обновлено! Терминов в базе: {len(GLOSSARY_CACHE)}.")
    else:
        bot.send_message(message.chat.id, "❌ Ошибка обновления.")

# --- КОТИРОВКИ ---
def get_market_rates():
    result_text = "<b>📊 Актуальные курсы и рынки:</b>\n\n"
    try:
        response = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        data = response.json()
        if data.get("result") == "success":
            rates = data["rates"]
            usd_ILS = rates.get("ILS", 3.6)
            eur_USD = rates.get("EUR", 0.9)
            rub_USD = rates.get("RUB", 90.0)
            
            result_text += f"🇺🇸 USD/ILS: {usd_ILS:.2f}\n"
            result_text += f"🇪🇺 EUR/ILS: {(usd_ILS / eur_USD if eur_USD else 0):.2f}\n"
            result_text += f"🇮🇱 ILS/RUB: {(rub_USD / usd_ILS if usd_ILS else 0):.2f}\n"
            result_text += f"🇷🇺 USD/RUB: {rub_USD:.2f}\n"
            result_text += f"🇪🇺 EUR/RUB: {(rub_USD * eur_USD):.2f}\n\n"
    except Exception:
        result_text += "<i>💱 Валюты временно недоступны</i>\n\n"

    crypto_tickers = {"🪙 Bitcoin": "BTC-USD", "🔷 Ethereum": "ETH-USD", "🟣 Solana": "SOL-USD", "🥇 Золото": "GC=F", "🥈 Серебро": "SI=F"}
    for name, ticker in crypto_tickers.items():
        try:
            hist = yf.Ticker(ticker).history(period="1d")
            if not hist.empty and 'Close' in hist.columns:
                result_text += f"{name}: ${hist['Close'].iloc[-1]:.2f}\n"
        except Exception:
            result_text += f"{name}: <i>нет данных</i>\n"
    return result_text

# --- ОБРАБОТЧИК КНОПОК ---
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    try:
        # Подменю: Статьи
        if call.data == "menu_articles":
            markup = InlineKeyboardMarkup(row_width=1)
            # Укажи здесь ключи твоих статей из FINANCIAL_DATA
            article_keys = ["banking", "pensions", "hishtalmut", "non_bank_cards"] 
            for key in article_keys:
                if key in FINANCIAL_DATA:
                    markup.add(InlineKeyboardButton(FINANCIAL_DATA[key]['title'], callback_data=f"info_{key}"))
            markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main"))
            bot.edit_message_text("📚 *База знаний*\nВыбери статью:", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown', reply_markup=markup)

        # Подменю: Таблицы
        elif call.data == "menu_tables":
            markup = InlineKeyboardMarkup(row_width=1)
            # Укажи здесь ключи твоих таблиц из FINANCIAL_DATA
            table_keys = ["pension_companies", "non_bank_cards_ad_min"] 
            for key in table_keys:
                if key in FINANCIAL_DATA:
                    markup.add(InlineKeyboardButton(FINANCIAL_DATA[key]['title'], callback_data=f"info_{key}"))
            markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main"))
            bot.edit_message_text("📊 *Полезные таблицы*\nВыбери таблицу:", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown', reply_markup=markup)

        # Вывод конкретной статьи/таблицы
        elif call.data.startswith("info_"):
            topic_key = call.data.replace("info_", "", 1)
            if topic_key in FINANCIAL_DATA:
                data = FINANCIAL_DATA[topic_key]
                message_text = f"*{data.get('title', '')}*\n\n{data.get('description', '')}"
                markup = InlineKeyboardMarkup()
                if data.get('url'):
                    markup.add(InlineKeyboardButton("Открыть материал", url=data['url']))
                markup.add(InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_main"))
                bot.edit_message_text(message_text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown', reply_markup=markup, disable_web_page_preview=True)
        
        # Курсы валют
        elif call.data == "show_rates":
            bot.edit_message_text("<i>⏳ Собираю котировки...</i>", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='HTML')
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main"))
            bot.edit_message_text(get_market_rates(), chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='HTML', reply_markup=markup)
            
        # Заглушка для Тлуша
        elif call.data == "read_tlush":
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main"))
            bot.edit_message_text("📄 *Чтение тлуша*\n\nФункция в разработке! Скоро здесь можно будет загрузить фото зарплатного листа для анализа.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown', reply_markup=markup)
            
        # Возврат в главное меню
        elif call.data == "back_to_main":
            bot.edit_message_text("Выбери нужный раздел:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_main_menu())
            
    except Exception as e:
        print(f"Ошибка кнопки {call.data}: {e}")
    finally:
        bot.answer_callback_query(call.id)

# --- СЛОВАРЬ (ТЕКСТ) ---
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_word = message.text.strip().lower()
    for keys, definition in GLOSSARY_CACHE.items():
        if user_word in keys:
            bot.send_message(message.chat.id, definition, parse_mode='HTML')
            return
    bot.send_message(message.chat.id, "Я пока не знаю такого термина 😔\nПопробуй написать иначе.")

if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()
    print("Веб-сервер запущен в фоне.")
    print("Бот запущен...")
    
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=20)
        except Exception as e:
            print(f"Ошибка polling: {e}")
            time.sleep(5)