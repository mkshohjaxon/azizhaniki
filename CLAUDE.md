# Telegram Multi-Account Manager

Bir nechta Telegram user-akkauntni Telethon orqali boshqaradigan servis. FastAPI API + SQLite baza + veb panel.
Muallif: Shoh. Javoblar o'zbek tilida.

## Fayllar
- `account_manager.py` — yadro (`AccountManager`)
  - `accounts` jadvali: sessiya (StringSession) + har akkauntga o'z `api_id/api_hash`, qurilma (`device_model`, `system_version`, `app_version`, `lang_code`, `system_lang_code`), `proxy`, `twofa`, `tags`, `note`, `flood_until`, `spam_*`, `last_used_at`. Yangi ustunlar `init()` da eski bazaga avtomatik qo'shiladi
  - `actions` jadvali — har bir amal jurnali (kunlik limit shundan hisoblanadi)
  - **Har bir Telegram amali `manager.run(phone, action, fn, limited=)` orqali**: FloodWait muddatini eslab qoladi, kunlik limit (`DAILY_MSG_LIMIT`, faqat `limited=True`), bitta akkauntda navbat + `ACTION_DELAY` pauza, o'lik sessiyani nofaol qiladi
  - Ulanish rejimi `CONNECT_MODE`: `on_demand` (standart — `acquire()` kerak bo'lganda ulaydi, `maintenance()` `IDLE_MINUTES` dan keyin uzadi) yoki `always`
  - `session_file_to_string()` — Telethon / Pyrogram / pyrofork `.session`; yonidagi `.json` (`parse_session_json`: app_id, device, sdk, twoFA, proxy...) import paytida o'qiladi
  - `extract_sessions_from_zip()` — zip ichidagi `.session` + bir xil nomli `.json`
  - `scan_folder()` / `watch_folder()` — `sessions/` har 10 s: zip ochiladi, ishlagan → `imported/`, yaroqsiz → `failed/` (+ `.txt`), tarmoq/FloodWait → joyida qoladi
  - `CONNECT_TIMEOUT=25` — o'lik sessiyada Telethon `connect()` cheksiz osiladi, shuning uchun hamma joyda `wait_for`
  - `spam_check()` / `start_spam_check_all()` (@SpamBot, fon ishi `manager.job`), `export_zip()` (.session + .json + strings.txt)
- `api.py` — FastAPI, auth `X-API-Key`. `/` veb panel, `/docs` Swagger, `/health` kalitsiz
  - Sozlama: `GET /config`, `POST /config/telegram`, `POST /config/options`
  - Login: `POST /accounts/send-code`, `/confirm-code`, `/confirm-password`, `/cancel-login`
  - Import: `POST /accounts/import-string`, `/accounts/import-files` (.session/.json/.zip), `/accounts/scan`
  - Akkaunt: `GET/PATCH/DELETE /accounts/{phone}` (DELETE sukut bo'yicha logout QILMAYDI), `/enable`, `/disconnect`, `/actions`, `/me`, `/dialogs`, `/send-message`, `/profile`, `/username`, `/avatar`, `/authorizations` (+ DELETE `/{hash}`), `/2fa`, `/spam-check`
  - `POST /jobs/spam-check`, `GET /jobs`, `GET /actions`, `GET /export`
- `static/index.html` — veb panel (vanilla JS, API key localStorage da): bosh sahifa, akkauntlar (qidiruv/filtr/teglar), akkaunt oynasi (umumiy, ulanish, profil, xavfsizlik, chatlar, jurnal), qo'shish, xabar, jurnal, sozlamalar
- `import_sessions.py` — API'siz bir martalik import

## Ishga tushirish
- Papka `~/tg-account-manager`, venv `venv/` (fastapi uvicorn telethon aiosqlite python-multipart python-socks[asyncio])
- User servis: `systemctl --user {status,restart} tg-account-manager`, loglar: `journalctl --user -u tg-account-manager -f`
- Manzil: lokal `http://127.0.0.1:8010/7878/`, tashqi `https://app.topen.uz/7878/` (topen-tunnel, `config_topen.yml` dagi `path: ^/7878` qoidasi; `.env` da `BASE_PATH=/7878`, uvicorn `api:root`). `topen.uz/7878` ham qoidada bor, lekin topen.uz DNS boshqa (o'lik) tunnelga qaragan — 1033
- `.env` (chmod 600): `API_KEY`, `TG_API_ID`, `TG_API_HASH`, `CONNECT_MODE`, `IDLE_MINUTES`, `DAILY_MSG_LIMIT`, `ACTION_DELAY` — paneldan o'zgartirilsa shu faylga yoziladi
- `backups/` — katta o'zgarishlardan oldingi baza va kod nusxalari

## Holat (2026-09-29)
- Bazada 1 ta haqiqiy akkaunt (+18016581056) — @SpamBot bo'yicha **cheklangan** (limited)
- Sinalgan: konvertor, zip+json juftligi, proxy parser, migratsiya, eksport aylanishi, limit/FloodWait bloklash, API smoke, SpamBot haqiqiy akkauntda
- Sinalmagan: panel tugmalari brauzerda qo'lda bosib ko'rilmagan; proxy orqali haqiqiy ulanish; 2FA o'rnatish; avatar

## Muhim
- `accounts.db`, `.session`, eksport zip akkauntlarga to'liq kirish beradi — git'ga qo'shmang
- Ommaviy so'ralmagan xabar (spam) yuborishni chetlab o'tadigan funksiyalar qo'shilmaydi
