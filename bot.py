import os
import time
import csv
import requests
import yfinance as yf
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# Импортируем наш веб-сервер для поддержания активности на Render
from keep_alive import keep_alive
import threading

# Импорт базы знаний с кнопками
from knowledge import FINANCIAL_DATA

# Проверяем токен перед стартом
token = os.environ.get("BOT_TOKEN")
if not token:
    raise ValueError("Не найден токен бота! Проверь переменные окружения BOT_TOKEN на Render.")

bot = telebot.TeleBot(token)

# --- БЛОК С НАСТРОЙКАМИ GOOGLE SHEETS ---
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRitIhrxkXddq7kpf1Oy3qHXxSjD2u-8I0-deeQNOVLnhqTpFVjCtKE0t_ohYT4fvRKbvtX7kdOArWm/pub?output=csv"

def get_glossary_from_google():
    try:
        response = requests.get(SHEET_CSV_URL, timeout=10)
        response.encoding = 'utf-8'
        lines = response.text.splitlines()
        reader = csv.reader(lines)

        glossary = {}
        next(reader, None)

        for row in reader:
            if len(row) >= 2:
                keys = tuple([k.strip().lower() for k in row[0].split(',')])
                glossary[keys] = row[1]
        return glossary
    except Exception as e:
        print("Ошибка загрузки таблицы:", e)
        return {}
# ----------------------------------------------

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

def get_market_rates():
    result_text = "<b>📊 Актуальные курсы и рынки:</b>\n\n"

    # 1. Получаем валюты через бесплатный API (базовая валюта — USD)
    try:
        response = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        data = response.json()
        
        if data.get("result") == "success":
            rates = data["rates"]
            usd_ILS = rates.get("ILS", 3.6)
            eur_USD = rates.get("EUR", 0.9) # Сколько евро за 1 USD
            rub_USD = rates.get("RUB", 90.0) # Сколько рублей за 1 USD
            
            # Считаем кросс-курсы
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

    # 2. Получаем крипту и металлы через yfinance
    crypto_tickers = {
        "🪙 Bitcoin": "BTC-USD",
        "🔷 Ethereum": "ETH-USD",
        "🟣 Solana": "SOL-USD",
        "🥇 Золото": "GC=F",
        "🥈 Серебро": "SI=F"
    }

    for name, ticker in crypto_tickers.items():
        try:
            data = yf.Ticker(ticker)
            hist = data.history(period="5d")
            if not hist.empty and 'Close' in hist.columns:
                price = hist['Close'].iloc[-1]
                result_text += f"{name}: ${price:.2f}\n"
            else:
                result_text += f"{name}: <i>нет данных</i>\n"
        except Exception as e:
            result_text += f"{name}: <i>временно недоступно</i>\n"

    return result_text

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if call.data.startswith("info_"):
        topic_key = call.data.replace("info_", "", 1)
        if topic_key in FINANCIAL_DATA:
            data = FINANCIAL_DATA[topic_key]
            message_text = f"*{data.get('title', 'Информация')}*\n\n{data.get('description', '')}"
            markup = InlineKeyboardMarkup()
            if 'url' in data:
                markup.add(InlineKeyboardButton(text="Открыть статью", url=data['url']))
            markup.add(InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_main"))
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=message_text,
                parse_mode='Markdown',
                reply_markup=markup
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

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_word = message.text.strip().lower()
    current_glossary = get_glossary_from_google()
    found = False
    for keys, definition in current_glossary.items():
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
    # Запускаем веб-сервер в фоновом потоке
    threading.Thread(target=keep_alive).start()
    print("Веб-сервер запущен в фоне.")

    print("Бот запущен и готов к работе...")
    
    # Бесконечный цикл опроса Telegram с защитой от разрывов связи
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=20)
        except Exception as e:
            print(f"Ошибка соединения Telegram: {e}")
            print("Переподключение через 10 секунд...")
            time.sleep(10)