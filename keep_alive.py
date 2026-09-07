import os
import logging
from flask import Flask
from threading import Thread

app = Flask(__name__)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

@app.route("/")
def home():
    return "StockGro Telegram Bot is Online & Active! 🚀", 200

@app.route("/health")
def health():
    return "OK", 200

def keep_alive():
    port = int(os.environ.get("PORT", 10000))
    t = Thread(target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True), daemon=True)
    t.start()
