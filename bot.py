import os
import time
import csv
import requests
import yfinance as yf
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import threading

# Импортируем веб-сервер для поддержания активности на Render
from keep_alive import keep_alive

# Импорт базы знаний с кнопками
from knowledge import FINANCIAL_DATA

# Проверяем токен перед стартом
token = os.environ.get("BOT_TOKEN")
if not token:
    raise ValueError("Не найден токен бота! Проверь переменные окружения BOT_TOKEN на Render.")

bot = telebot.TeleBot(token)

# --- БЛОК С НАСТРОЙКАМИ GOOGLE SHEETS И КЭШИРОВАНИЕМ ---
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRitIhrxkXddq7kpf1Oy3qHXxSjD2u-8I0-deeQNOVLnhqTpFVjCtKE0t_ohYT4fvRKbvtX7kdOArWm/pub?output=csv"

# Глобальная переменная для хранения словаря в оперативной памяти
GLOSSARY_CACHE = {}

def update_glossary():
    global GLOSSARY_CACHE
    try:
        response = requests.get(SHEET_CSV_URL, timeout=10)
        
        if response.status_code != 200:
            print(f"Ошибка сервера Google: статус {response.status_code}")
            return False
            
        response.encoding = 'utf-8'
        lines = response.text.splitlines()
        reader = csv.reader(lines)

        new_glossary = {}
        next(reader, None) # Пропускаем заголовок таблицы

        for row in reader:
            # Проверяем, что в строке есть минимум 2 колонки и первая не пустая
            if len(row) >= 2 and row[0].strip():
                # Разбиваем по запятой, убираем пробелы и возможные случайные кавычки
                keys = tuple([k.strip().strip(' "\'').lower() for k in row[0].split(',') if k.strip()])
                definition = row[1].strip()
                if keys:
                    new_glossary[keys] = definition
                    
        if new_glossary:
            GLOSSARY_CACHE = new_glossary
            print(f"✅ Словарь обновлен! Загружено терминов: {len(GLOSSARY_CACHE)}")
            return True
        return False
    except Exception as e:
        print(f"Ошибка загрузки таблицы: {e}")
        return False

# Загружаем словарь в память при старте скрипта
update_glossary()
# -------------------------------------------------------

def get_main_menu():
    markup = InlineKeyboardMarkup(row_width=1)
    for key, data in FINANCIAL_DATA.items():
        markup.add(InlineKeyboardButton(text=data['title'], callback_data=f"info_{key}"))
    markup.add(InlineKeyboardButton(text="📊 Курсы валют, крипты и металлов", callback_data="show_rates"))
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.send_message(
        message.chat.id,
        "Привет! Я твой финансовый навигатор по Израилю. Выбери тему:",
        reply_markup=get_main_menu()
    )

# --- СКРЫТАЯ КОМАНДА ДЛЯ ОБНОВЛЕНИЯ СЛОВАРЯ ---
@bot.message_handler(commands=['update'])
def force_update_glossary(message):
    bot.send_message(message.chat.id, "🔄 Скачиваю свежие данные из Google Таблицы...")
    success = update_glossary()
    if success:
        bot.send_message(message.chat.id, f"✅ Словарь успешно обновлен! Теперь в базе {len(GLOSSARY_CACHE)} терминов.")
    else:
        bot.send_message(message.chat.id, "❌ Ошибка обновления. Таблица недоступна или пуста.")
# ----------------------------------------------

def get_market_rates():
    result_text = "<b>📊 Актуальные курсы и рынки:</b>\n\n"

    # 1. Фиатные валюты через открытый API
    try:
        response = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        data = response.json()
        
        if data.get("result") == "success":
            rates = data["rates"]
            usd_ILS = rates.get("ILS", 3.6)
            eur_USD = rates.get("EUR", 0.9)
            rub_USD = rates.get("RUB", 90.0)
            
            eur_ILS = usd_ILS / eur_USD if eur_USD else 0
            ils_RUB = rub_USD / usd_ILS if usd_ILS else 0
            usd_RUB = rub_USD
            eur_RUB = rub_USD * eur_USD
            
            result_text += f"🇺🇸 USD/ILS: {usd_ILS:.2f}\n"
            result_text += f"🇪🇺 EUR/ILS: {eur_ILS:.2f}\n"
            result_text += f"🇮🇱 ILS/RUB: {ils_RUB:.2f}\n"
            result_text += f"🇷🇺 USD/RUB: {usd_RUB:.2f}\n"
            result_text += f"🇪🇺 EUR/RUB: {eur_RUB:.2f}\n\n"
        else:
            result_text += "<i>💱 Валюты временно недоступны</i>\n\n"
    except Exception as e:
        result_text += f"<i>💱 Ошибка загрузки валют: {e}</i>\n\n"

    # 2. Крипта и металлы через yfinance (на Render работает без блокировок)
    crypto_tickers = {
        "🪙 Bitcoin": "BTC-USD",
        "🔷 Ethereum": "ETH-USD",
        "🟣 Solana": "SOL-USD",
        "🥇 Золото": "GC=F",
        "🥈 Серебро": "SI=F"
    }

    for name, ticker in crypto_tickers.items():
        try:
            # Устанавливаем небольшой таймаут, чтобы бот не зависал при проблемах у Yahoo
            data = yf.Ticker(ticker)
            hist = data.history(period="1d")
            if not hist.empty and 'Close' in hist.columns:
                price = hist['Close'].iloc[-1]
                result_text += f"{name}: ${price:.2f}\n"
            else:
                result_text += f"{name}: <i>нет данных</i>\n"
        except Exception:
            result_text += f"{name}: <i>временно недоступно</i>\n"

    return result_text

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    try:
        if call.data.startswith("info_"):
            topic_key = call.data.replace("info_", "", 1)
            if topic_key in FINANCIAL_DATA:
                data = FINANCIAL_DATA[topic_key]
                # Безопасное извлечение title и description
                message_text = f"*{data.get('title', 'Информация')}*\n\n{data.get('description', 'Описание отсутствует.')}"
                
                markup = InlineKeyboardMarkup()
                if 'url' in data and data['url']:
                    markup.add(InlineKeyboardButton(text="Открыть статью", url=data['url']))
                markup.add(InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_main"))
                
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=message_text,
                    parse_mode='Markdown',
                    reply_markup=markup,
                    disable_web_page_preview=True
                )
        
        elif call.data == "show_rates":
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="<i>⏳ Собираю свежие котировки с рынков...</i>",
                parse_mode='HTML'
            )
            rates_text = get_market_rates()
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_main"))
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=rates_text,
                parse_mode='HTML',
                reply_markup=markup
            )
            
        elif call.data == "back_to_main":
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="Выбери тему, чтобы узнать больше:",
                reply_markup=get_main_menu()
            )
    except Exception as e:
        print(f"Ошибка при обработке кнопки {call.data}: {e}")
    finally:
        # Убираем "часики" на кнопке
        bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_word = message.text.strip().lower()
    found = False
    
    # Ищем слово в глобальном кэше (мгновенный ответ)
    for keys, definition in GLOSSARY_CACHE.items():
        if user_word in keys:
            bot.send_message(message.chat.id, definition, parse_mode='HTML')
            found = True
            break
            
    if not found:
        bot.send_message(
            message.chat.id,
            "Я пока не знаю такого термина 😔\n"
            "Попробуй написать его иначе."
        )

if __name__ == "__main__":
    # Запускаем веб-сервер в фоновом потоке для прохождения проверок Render
    threading.Thread(target=keep_alive, daemon=True).start()
    print("Веб-сервер запущен в фоне.")

    print("Бот запущен и готов к работе...")
    
    # Бесконечный цикл опроса Telegram с защитой от падений
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=20)
        except Exception as e:
            print(f"Критическая ошибка polling: {e}")
            print("Переподключение через 5 секунд...")
            time.sleep(5)