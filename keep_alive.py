import os
from flask import Flask

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running!"

def keep_alive():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)