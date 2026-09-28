import os
from flask import Flask
from flask_cors import CORS

# Создаем приложение
app = Flask(__name__)
# Снимаем блокировку CORS, чтобы Telegram Web App мог читать данные
CORS(app) 

@app.route('/')
def index():
    return "Bot is running and API is active!"

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)