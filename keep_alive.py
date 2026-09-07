import os
from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route("/")
def home():
    return "StockGro Telegram Bot is Online & Active! 🚀"

@app.route("/health")
def health():
    return "OK", 200

def keep_alive():
    port = int(os.environ.get("PORT", 10000))
    t = Thread(target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False), daemon=True)
    t.start()
