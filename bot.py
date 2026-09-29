import os
import time
import csv
import json
import requests
import threading
import schedule
import psycopg2
from psycopg2.extras import RealDictCursor
import yfinance as yf
import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from flask import request, jsonify

# 1. Импортируем наш сервер и функцию запуска из keep_alive
from keep_alive import app, run_server
from knowledge import FINANCIAL_DATA
from telebot.types import ReplyKeyboardRemove

# --- НАСТРОЙКИ БАЗЫ ДАННЫХ ---
DB_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    try:
        return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    except Exception as e:
        print("Ошибка подключения к БД:", e)
        return None

def init_db_tables():
    """Создает таблицы и индексы в базе данных при запуске"""
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id BIGINT PRIMARY KEY,
                    monthly_budget NUMERIC(10, 2) DEFAULT 0.00
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS expenses (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT REFERENCES users(telegram_id),
                    category VARCHAR(50),
                    amount NUMERIC(10, 2),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # НОВАЯ ТАБЛИЦА ДЛЯ ЛИМИТОВ:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS category_limits (
                    telegram_id BIGINT REFERENCES users(telegram_id),
                    category VARCHAR(50),
                    limit_amount NUMERIC(10, 2),
                    PRIMARY KEY (telegram_id, category)
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_expenses_user ON expenses(telegram_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(created_at);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_limits_user ON category_limits(telegram_id);")
            conn.commit()
            print("Таблицы и индексы БД успешно инициализированы.")
        except Exception as e:
            print("Ошибка при создании таблиц:", e)
        finally:
            cur.close()
            conn.close()

def init_user_in_db(telegram_id):
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO users (telegram_id, monthly_budget) 
                VALUES (%s, %s) 
                ON CONFLICT (telegram_id) DO NOTHING;
            """, (telegram_id, 0.00)) # Изменено на 0, чтобы просить установить бюджет
            conn.commit()
            return True
        except Exception as e:
            print("Ошибка при записи юзера:", e)
        finally:
            cur.close()
            conn.close()
    return False

def add_expense_to_db(telegram_id, category, amount):
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO expenses (telegram_id, category, amount) 
                VALUES (%s, %s, %s);
            """, (telegram_id, category, amount))
            conn.commit()
            return True
        except Exception as e:
            print("Ошибка записи расхода:", e)
        finally:
            cur.close()
            conn.close()
    return False

def update_user_budget(telegram_id, new_budget):
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE users 
                SET monthly_budget = %s 
                WHERE telegram_id = %s;
            """, (new_budget, telegram_id))
            conn.commit()
            return True
        except Exception as e:
            print("Ошибка обновления бюджета:", e)
        finally:
            cur.close()
            conn.close()
    return False

def set_user_category_limit(telegram_id, category, limit_amount):
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO category_limits (telegram_id, category, limit_amount) 
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id, category) 
                DO UPDATE SET limit_amount = EXCLUDED.limit_amount;
            """, (telegram_id, category, limit_amount))
            conn.commit()
            return True
        except Exception as e:
            print("Ошибка сохранения лимита:", e)
        finally:
            cur.close()
            conn.close()
    return False

# --- 2. API МАРШРУТЫ ДЛЯ WEB APP (Используем app из keep_alive) ---
@app.route('/api/stats', methods=['GET'])
@app.route('/api/stats', methods=['GET'])
def get_stats():
    user_id = request.args.get('telegram_id')
    if not user_id:
        return jsonify({"error": "Missing telegram_id"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database error"}), 500

    cur = None
    try:
        cur = conn.cursor()
        
        # Получаем бюджет пользователя
        cur.execute("SELECT monthly_budget FROM users WHERE telegram_id = %s", (user_id,))
        user_data = cur.fetchone()
        budget = float(user_data['monthly_budget']) if user_data and user_data['monthly_budget'] is not None else 0

        # Считаем траты за текущий месяц (КРОМЕ Копилки)
        cur.execute("""
            SELECT category, SUM(amount) as total
            FROM expenses 
            WHERE telegram_id = %s 
              AND category != 'Копилка'
              AND EXTRACT(MONTH FROM created_at) = EXTRACT(MONTH FROM CURRENT_DATE)
              AND EXTRACT(YEAR FROM created_at) = EXTRACT(YEAR FROM CURRENT_DATE)
            GROUP BY category
        """, (user_id,))
        
        expenses = cur.fetchall()
        categories = {row['category']: float(row['total']) for row in expenses if row['category']}
        total_spent = sum(categories.values())

        # Считаем сумму копилки за всё время
        cur.execute("""
            SELECT SUM(amount) as total
            FROM expenses 
            WHERE telegram_id = %s AND category = 'Копилка'
        """, (user_id,))
        savings_data = cur.fetchone()
        total_savings = float(savings_data['total']) if savings_data and savings_data['total'] is not None else 0

        # --- РАСЧЕТ ЛИМИТОВ (Умные проценты 50/30/20 + PRO настройки) ---
        default_weights = {
            'Продукты': 0.20,
            'Счета': 0.20,
            'Транспорт': 0.10,
            'Еда вне дома': 0.10,
            'Кафе': 0.05,
            'Шоппинг': 0.05,
            'Бары': 0.05,
            'Курение': 0.05
        }

        # Получаем персональные лимиты пользователя из базы
        cur.execute("SELECT category, limit_amount FROM category_limits WHERE telegram_id = %s", (user_id,))
        custom_limits_rows = cur.fetchall()
        custom_limits = {row['category']: float(row['limit_amount']) for row in custom_limits_rows}

        # Собираем финальные лимиты: кастомный или автоматически рассчитанный по процентам
        limits = {}
        all_categories = ['Продукты', 'Транспорт', 'Кафе', 'Счета', 'Шоппинг', 'Бары', 'Курение', 'Еда вне дома']
        
        for cat in all_categories:
            if cat in custom_limits:
                limits[cat] = custom_limits[cat]
            else:
                weight = default_weights.get(cat, 0.05)
                limits[cat] = round(budget * weight, 2)

        return jsonify({
            "budget": budget,
            "total_spent": total_spent,
            "categories": categories,
            "total_savings": total_savings,
            "limits": limits
        })
        
    except Exception as e:
        print("Ошибка API:", e)
        return jsonify({"error": "Internal error"}), 500
    finally:
        if cur: cur.close()
        if conn: conn.close()
# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
token = os.environ.get("BOT_TOKEN")
if not token:
    raise ValueError("Не найден токен бота! Проверь переменные окружения BOT_TOKEN на Render.")

bot = telebot.TeleBot(token)

# --- БЛОК С НАСТРОЙКАМИ GOOGLE SHEETS И КЭШИРОВАНИЕМ ---
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRitIhrxkXddq7kpf1Oy3qHXxSjD2u-8I0-deeQNOVLnhqTpFVjCtKE0t_ohYT4fvRKbvtX7kdOArWm/pub?output=csv"
GLOSSARY_CACHE = {}

@app.route('/api/action', methods=['POST'])
def handle_action():
    data = request.json
    user_id = data.get('telegram_id')
    action = data.get('action')
    category = data.get('category_id')
    amount = data.get('amount')

    if not user_id or not amount:
        return jsonify({"error": "Bad request"}), 400

    success = False
    try:
        if action == 'expense':
            success = add_expense_to_db(user_id, category, amount)
            if success: 
                bot.send_message(user_id, f"✅ Учтено: <b>{amount} ₪</b> в категорию «{category}»", parse_mode='HTML')
        
        elif action == 'to_savings':
            success = add_expense_to_db(user_id, 'Копилка', amount)
            if success: 
                bot.send_message(user_id, f"🐷 <b>{amount} ₪</b> отправлено в копилку!", parse_mode='HTML')
        
        elif action == 'set_budget':
            success = update_user_budget(user_id, amount)
            if success: 
                bot.send_message(user_id, f"🎯 Твой новый бюджет на месяц установлен: <b>{amount} ₪</b>", parse_mode='HTML')

        if success:
            return jsonify({"status": "success"})
        else:
            return jsonify({"error": "DB error"}), 500
    except Exception as e:
        print("Ошибка обработки действия:", e)
        return jsonify({"error": "Internal error"}), 500

# ВАЖНО: Функцию @bot.message_handler(content_types=['web_app_data']) 
# теперь можно полностью удалить из bot.py, она больше не нужна.

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
    btn_articles = InlineKeyboardButton("📚 Статьи и база знаний", callback_data="menu_articles")
    btn_tables = InlineKeyboardButton("📊 Полезные таблицы", callback_data="menu_tables")
    markup.add(btn_articles)
    markup.add(btn_tables)
    
    btn_calc = InlineKeyboardButton(
        "🧮 Калькулятор", 
        web_app=WebAppInfo(url="https://antonpankov1.github.io/finance-il-bot/calculator.html")
    )
    btn_tlush = InlineKeyboardButton("📄 Чтение тлуша", callback_data="read_tlush")
    btn_rates = InlineKeyboardButton("💱 Курс валют", callback_data="show_rates")
    
    markup.row(btn_calc, btn_tlush)
    markup.add(btn_rates)
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    init_user_in_db(message.from_user.id)
    
    bot.send_message(
        message.chat.id,
        "Привет! Я твой финансовый навигатор по Израилю. Твой кошелек теперь всегда под рукой — нажми на кнопку finance manager слева от поля ввода текста 👇",
        reply_markup=ReplyKeyboardRemove() # <--- ЭТА КОМАНДА НАВСЕГДА УБЕРЕТ СТАРУЮ КНОПКУ
    )
    
    bot.send_message(
        message.chat.id,
        "Также выбери нужный раздел базы знаний:",
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
        if call.data == "menu_articles":
            markup = InlineKeyboardMarkup(row_width=1)
            article_keys = ["banking", "pensions", "hishtalmut", "non_bank_cards"] 
            for key in article_keys:
                if key in FINANCIAL_DATA:
                    markup.add(InlineKeyboardButton(FINANCIAL_DATA[key]['title'], callback_data=f"info_{key}"))
            markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main"))
            bot.edit_message_text("📚 *База знаний*\nВыбери статью:", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown', reply_markup=markup)

        elif call.data == "menu_tables":
            markup = InlineKeyboardMarkup(row_width=1)
            table_keys = ["pension_companies", "non_bank_cards_ad_min"] 
            for key in table_keys:
                if key in FINANCIAL_DATA:
                    markup.add(InlineKeyboardButton(FINANCIAL_DATA[key]['title'], callback_data=f"info_{key}"))
            markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main"))
            bot.edit_message_text("📊 *Полезные таблицы*\nВыбери таблицу:", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown', reply_markup=markup)

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
        
        elif call.data == "show_rates":
            bot.edit_message_text("<i>⏳ Собираю котировки...</i>", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='HTML')
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main"))
            bot.edit_message_text(get_market_rates(), chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='HTML', reply_markup=markup)
            
        elif call.data == "read_tlush":
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main"))
            bot.edit_message_text("📄 *Чтение тлуша*\n\nФункция в разработке! Скоро здесь можно будет загрузить фото зарплатного листа для анализа.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown', reply_markup=markup)
            
        elif call.data == "back_to_main":
            bot.edit_message_text("Выбери нужный раздел:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_main_menu())
            
    except Exception as e:
        print(f"Ошибка кнопки {call.data}: {e}")
    finally:
        bot.answer_callback_query(call.id)

@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    try:
        data = json.loads(message.web_app_data.data)
        action = data.get('action')
        category = data.get('category_id')
        amount = data.get('amount')
        
        if action == 'expense':
            success = add_expense_to_db(message.from_user.id, category, amount)
            if success:
                bot.send_message(message.chat.id, f"✅ Учтено: <b>{amount} ₪</b> в категорию «{category}»", parse_mode='HTML')
            else:
                bot.send_message(message.chat.id, "❌ Ошибка базы данных.")
                
        elif action == 'to_savings':
            success = add_expense_to_db(message.from_user.id, 'Копилка', amount)
            if success:
                bot.send_message(message.chat.id, f"🐷 <b>{amount} ₪</b> отправлено в копилку!", parse_mode='HTML')
            else:
                bot.send_message(message.chat.id, "❌ Ошибка базы данных.")

        elif action == 'set_budget':
            success = update_user_budget(message.from_user.id, amount)
            if success:
                bot.send_message(message.chat.id, f"🎯 Твой новый бюджет на месяц установлен: <b>{amount} ₪</b>", parse_mode='HTML')
            else:
                bot.send_message(message.chat.id, "❌ Ошибка базы данных при обновлении бюджета.")

    except Exception as e:
        print("Ошибка обработки web_app_data:", e)
        bot.send_message(message.chat.id, "❌ Произошла ошибка при обработке данных.")

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_word = message.text.strip().lower()
    for keys, definition in GLOSSARY_CACHE.items():
        if user_word in keys:
            bot.send_message(message.chat.id, definition, parse_mode='HTML')
            return
    bot.send_message(message.chat.id, "Я пока не знаю такого термина 😔\nПопробуй написать иначе.")

# --- ПЛАНИРОВЩИК НАПОМИНАНИЙ ---
def send_daily_reminders():
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("SELECT telegram_id FROM users")
        users = cur.fetchall()
        for user in users:
            try:
                bot.send_message(
                    user['telegram_id'], 
                    "🌙 День подходит к концу! Не забудь внести сегодняшние расходы в CoinKeeper 👇"
                )
                time.sleep(0.05) # Защита от спам-фильтра Telegram
            except Exception:
                pass
        cur.close()
        conn.close()

def reminder_thread():
    schedule.every().day.at("17:00").do(send_daily_reminders)
    while True:
        schedule.run_pending()
        time.sleep(60)

# --- ЗАПУСК ---
if __name__ == "__main__":
    init_db_tables()
    
    # 3. Запускаем сервер, импортированный из keep_alive
    threading.Thread(target=run_server, daemon=True).start()
    print("Веб-сервер Flask запущен в фоне.")
    
    threading.Thread(target=reminder_thread, daemon=True).start()
    print("Планировщик напоминаний запущен.")

    print("Бот запущен...")
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=20)
        except Exception as e:
            print(f"Ошибка polling: {e}")
            time.sleep(5)