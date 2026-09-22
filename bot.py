import os
import threading
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import csv
import time
import yfinance as yf
# Импорт базы знаний с кнопками
from knowledge import FINANCIAL_DATA

bot = telebot.TeleBot(os.environ.get("BOT_TOKEN"))


# --- НОВЫЙ БЛОК С НАСТРОЙКАМИ GOOGLE SHEETS ---
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRitIhrxkXddq7kpf1Oy3qHXxSjD2u-8I0-deeQNOVLnhqTpFVjCtKE0t_ohYT4fvRKbvtX7kdOArWm/pub?output=csv"

def get_glossary_from_google():
    try:
        response = requests.get(SHEET_CSV_URL)
        response.encoding = 'utf-8'
        lines = response.text.splitlines()
        reader = csv.reader(lines)

        glossary = {}
        next(reader, None) # Пропускаем заголовки

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

    # Добавляем кнопки из базы знаний
    for key, data in FINANCIAL_DATA.items():
        markup.add(InlineKeyboardButton(text=data['title'], callback_data=f"info_{key}"))

    # ДОБАВЛЯЕМ КНОПКУ КУРСОВ ВАЛЮТ
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
    # Словарь тикеров для Yahoo Finance
    tickers = {
        "🇺🇸 USD/ILS": "USDILS=X",
        "🇪🇺 EUR/ILS": "EURILS=X",
        "🇮🇱 ILS/RUB": "ILSRUB=X",
        "🇷🇺 USD/RUB": "USDRUB=X",
        "🇪🇺 EUR/RUB": "EURRUB=X",
        "🪙 Bitcoin": "BTC-USD",
        "🔷 Ethereum": "ETH-USD",
        "🟣 Solana": "SOL-USD",
        "🥇 Золото": "GC=F",
        "🥈 Серебро": "SI=F"
    }

    result_text = "<b>📊 Актуальные курсы и рынки:</b>\n\n"

    for name, ticker in tickers.items():
        try:
            data = yf.Ticker(ticker)
            # Берем последнюю актуальную цену закрытия
            price = data.history(period="1d")['Close'].iloc[0]

            # Форматируем вывод в зависимости от актива
            if "RUB" in name or "ILS" in name:
                result_text += f"{name}: {price:.2f}\n"
            else:
                result_text += f"{name}: ${price:.2f}\n"
        except Exception as e:
            result_text += f"{name}: <i>ошибка: {e}</i>\n"

    return result_text

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    # 1. Если нажали на кнопку финансовой темы
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

    # 2. Если нажали на кнопку курсов валют
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

    # 3. Если нажали на кнопку "Назад"
    elif call.data == "back_to_main":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Выбери тему, чтобы узнать больше:",
            reply_markup=get_main_menu()
        )

# --- НОВЫЙ ОБРАБОТЧИК СЛОВАРЯ (ОБЯЗАТЕЛЬНО В САМОМ НИЗУ!) ---
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
# -----------------------------------------------------------
# --- НАСТРОЙКИ ДЛЯ RENDER (СЕРВЕР-ЗАГЛУШКА) ---
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running!"

def run_web():
    # Render сам выдаст нужный порт через переменную окружения PORT
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
# ----------------------------------------------

if __name__ == "__main__":
    # 1. Запускаем фейковый веб-сервер в отдельном потоке, 
    # чтобы Render видел открытый порт и ставил "зеленую галочку"
    threading.Thread(target=run_web).start()
    
    # 2. Очищаем старые привязки (вебхуки), чтобы не было конфликтов
    try:
        bot.remove_webhook()
        print("Вебхуки очищены.")
    except Exception as e:
        print(f"Ошибка при очистке вебхука: {e}")

    # 3. Запускаем самого бота с максимальной защитой от падений
    print("Бот запущен и готов к работе...")
    
    while True:
        try:
            # Увеличенные таймауты спасают от ошибки urllib3.connectionpool
            bot.polling(none_stop=True, interval=0, timeout=20, request_timeout=65)
        except Exception as e:
            print(f"Ошибка соединения Telegram: {e}")
            print("Ждем 15 секунд и пробуем переподключиться...")
            time.sleep(15)