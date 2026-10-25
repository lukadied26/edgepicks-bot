import threading
from flask import Flask
from bot import run_bot

app = Flask(__name__)

@app.route('/')
def health():
    return "EdgePicks Bot Online!", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    run_bot()
  
