# 📱 TG Account Manager

**Telegram akkauntlarni boshqarish paneli** — sessiyalarni import/export qilish, xabar yuborish, SpamBot tekshirish, botlarga avtomatik start berish va AI bilan har qanday botni bajarish.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green?logo=fastapi)
![Telethon](https://img.shields.io/badge/Telethon-1.34+-orange)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## ✨ Imkoniyatlar

| Funksiya | Tavsif |
|----------|--------|
| 📂 **Sessiya import** | `.session`, `.json`, `.zip` fayl yuklash, StringSession, telefon login — progress bar bilan |
| 💬 **Xabar yuborish** | Har qanday chat/guruh/kanalga xabar, kunlik limit, FloodWait himoyasi |
| 🔍 **SpamBot tekshirish** | `@SpamBot` orqali barcha akkauntlarni ommaviy tekshirish |
| 📢 **Kanalga obuna** | N ta akkauntni kanalga obuna qilish (tag bo'yicha filtr) |
| 👍 **Reaksiya qo'yish** | So'ngi postga yoki aniq postga emoji reaksiya |
| 🤖 **Bot Start** | Botlarga `/start ref_code` yuborish — referral uchun |
| 🤖 **Bot Autopilot** | Majburiy kanallarga avtomatik obuna + "Tekshirish" tugmasini bosish |
| 🔗 **Ref zanjir** | Piramida — har akkaunt ref oladi va keyingisiga uzatadi |
| 🧠 **AI Bot Agent** | GPT-4o bilan har qanday botni avtomatik bajarish (savol-javob, captcha, kanal) |
| 🛡️ **Warm-up** | Sessiyalarni tirik tutish — avtomatik 24 soatda bir |
| 📱 **Qurilma randomizer** | 38 ta haqiqiy qurilma profili (Samsung, iPhone, Pixel, Xiaomi...) |
| 🔐 **2FA boshqarish** | Ikki bosqichli parol o'rnatish/o'chirish |
| 👤 **Profil boshqarish** | Ism, bio, username, avatar o'zgartirish |
| 🔒 **Sessiya shifrlash** | Fernet AES-256 bilan sessiyalar bazada shifrlangan |
| 🌐 **Proksi** | SOCKS5/HTTP proksi har akkaunt uchun alohida |
| 💾 **Zaxira** | Barcha akkauntlarni `.zip` da yuklab olish |

---

## 🚀 Tez o'rnatish

### 1. Klonlash
```bash
git clone https://github.com/mkshohjaxon/azizhaniki.git
cd azizhaniki
```

### 2. Virtual muhit
```bash
python3 -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

pip install telethon aiosqlite fastapi uvicorn cryptography openai
```

### 3. Sozlash
```bash
cp .env.example .env
nano .env
```

`.env` faylida:
```env
TG_API_ID=12345678
TG_API_HASH=abcdef1234567890abcdef1234567890
API_KEY=sizning_maxfiy_kalitingiz
OPENAI_API_KEY=sk-...          # ixtiyoriy — AI Agent uchun
BASE_PATH=/7878                # ixtiyoriy — reverse proxy uchun
```

> 📌 `TG_API_ID` va `TG_API_HASH` ni [my.telegram.org](https://my.telegram.org) dan oling

### 4. Ishga tushirish

**Linux / Mac:**
```bash
./start.sh
```

**Windows:**
```
start.bat
```

Yoki qo'lda:
```bash
uvicorn api:root --host 127.0.0.1 --port 8010
```

Brauzer avtomatik ochiladi: **http://127.0.0.1:8010/**

---

## 📖 Foydalanish

### 📂 Sessiya import qilish

1. **Qo'shish** tabini oching
2. `.session` fayllarni drag-and-drop qiling (yoki bosib tanlang)
3. `.zip` ichidagi sessiyalar avtomatik olinadi
4. `.json` fayl bo'lsa (asl qurilma, 2FA, proxy) — avtomatik o'qiladi
5. Yoki **StringSession** ga Telethon/Pyrogram stringni yopishtiring
6. Yoki **Telefon login** — kod yuboriladi, kirasiz

### 🤖 Bot Avtomatizatsiya

**🤖 Bot Start** tugmasini bosing → 4 ta rejim bor:

#### 🧠 AI Agent (tavsiya)
GPT-4o har qanday botni tushunadi va o'zi bajaradi:
- Bot kanalga obuna so'rasa → avtomatik obuna
- "Tekshirish" tugmasi bo'lsa → avtomatik bosadi
- Savol bersa → AI javob beradi
- Ref link bo'lsa → topib oladi

```
Bot link: https://t.me/SomeBot?start=ref123
Rejim: 🧠 AI Agent
Nechta akkaunt: 0 (hammasi)
→ 🚀 Boshlash
```

> ⚠️ AI Agent uchun OpenAI API key kerak — **Sozlamalar** → **🧠 OpenAI API** → key kiriting

#### 🤖 Autopilot
AI'siz ishlaydi — kanal havolalarini regex bilan topadi, "Tekshirish" tugmasini kalit so'zlar bilan aniqlaydi.

#### 🔗 Ref zanjir
Piramida rejimi:
```
1-akkaunt → /start → ref link oladi
2-akkaunt → /start [1-ning ref'i] → ref link oladi
3-akkaunt → /start [2-ning ref'i] → ...
```

#### 🚀 Oddiy Start
Faqat `/start [param]` yuboradi — boshqa hech narsa qilmaydi.

### 📢 Kanalga obuna

1. Dashboard → **📢 Kanalga obuna**
2. `@username` yoki `https://t.me/kanal` kiriting
3. Nechta akkaunt (0 = hammasi)
4. 🚀 Boshlash

### 👍 Reaksiya qo'yish

1. Dashboard → **👍 Reaksiya**
2. Kanal va emoji tanlang
3. `t.me/kanal/123` — aniq postga reaksiya

### 🛡️ Warm-up

Akkauntlarni "tirik" tutish — `get_me()` + dialoglar o'qish:
- **Qo'lda:** Dashboard → **🛡️ Warm-up**
- **Avtomatik:** Har 24 soatda o'zi bajaradi (Sozlamalar → `WARMUP_INTERVAL_HOURS`)

---

## 🏗️ Arxitektura

```
tg-account-manager/
├── api.py                 # FastAPI — barcha endpointlar
├── account_manager.py     # Biznes logika — AccountManager sinfi
├── static/
│   └── index.html         # SPA frontend (CSS + JS + HTML)
├── start.sh / start.bat   # Launcher skriptlar
├── stop.sh / stop.bat     # To'xtatish skriptlari
├── .env                   # Sozlamalar (git'ga tushmaydi)
├── accounts.db            # SQLite baza (git'ga tushmaydi)
└── sessions/              # Import papka (git'ga tushmaydi)
    ├── imported/           # Muvaffaqiyatli import
    └── failed/             # Xato sessiyalar + .txt sabab
```

### Texnologiyalar

| Komponent | Texnologiya |
|-----------|-------------|
| Backend | Python 3.10+, FastAPI, Uvicorn |
| Telegram | Telethon (MTProto) |
| Baza | SQLite + aiosqlite |
| Shifrlash | Fernet (AES-256-CBC) |
| Frontend | Vanilla JS SPA (bitta HTML fayl) |
| AI | OpenAI GPT-4o-mini |

### API endpointlar

| Metod | Yo'l | Vazifasi |
|-------|------|----------|
| `GET` | `/config` | Sozlamalar |
| `POST` | `/config/telegram` | API ID/Hash saqlash |
| `POST` | `/config/openai` | OpenAI key saqlash |
| `GET` | `/accounts` | Akkauntlar ro'yxati |
| `POST` | `/accounts/import-files` | Fayl import |
| `POST` | `/accounts/scan` | sessions/ papkani skanerlash |
| `POST` | `/jobs/join-channel` | Kanalga obuna |
| `POST` | `/jobs/send-reaction` | Reaksiya qo'yish |
| `POST` | `/jobs/warmup` | Warm-up |
| `POST` | `/jobs/bot-start` | Bot start (oddiy) |
| `POST` | `/jobs/bot-autopilot` | Bot autopilot |
| `POST` | `/jobs/ref-chain` | Ref zanjir |
| `POST` | `/jobs/ai-agent` | AI Bot Agent |
| `POST` | `/jobs/spam-check` | SpamBot tekshirish |
| `GET` | `/jobs` | Joriy ish holati |
| `GET` | `/export` | Zaxira (.zip) |

---

## 🔒 Xavfsizlik

- **API Key** — barcha so'rovlar `X-API-Key` header talab qiladi
- **Sessiya shifrlash** — bazadagi sessiyalar Fernet (AES-256) bilan shifrlangan
- **.env** — maxfiy kalitlar faqat server faylida, git'ga tushmaydi
- **Lokal server** — faqat `127.0.0.1` da tinglaydi (tashqaridan kirish yo'q)

---

## ⚙️ Sozlamalar

| O'zgaruvchi | Tavsif | Standart |
|-------------|--------|----------|
| `TG_API_ID` | Telegram API ID | — |
| `TG_API_HASH` | Telegram API Hash | — |
| `API_KEY` | Panel uchun maxfiy kalit | — |
| `OPENAI_API_KEY` | AI Agent uchun | — |
| `CONNECT_MODE` | `on_demand` yoki `always` | `on_demand` |
| `IDLE_MINUTES` | Bo'sh turganda uzish (daqiqa) | `5` |
| `DAILY_MSG_LIMIT` | Kunlik xabar limiti | `40` |
| `ACTION_DELAY` | Amallar orasidagi pauza (soniya) | `2` |
| `WARMUP_INTERVAL_HOURS` | Avtomatik warm-up intervali | `24` |
| `BASE_PATH` | Reverse proxy uchun yo'l | — |

---

## 📋 .env.example

```env
TG_API_ID=
TG_API_HASH=
API_KEY=your_secret_api_key_here
OPENAI_API_KEY=
CONNECT_MODE=on_demand
IDLE_MINUTES=5
DAILY_MSG_LIMIT=40
ACTION_DELAY=2
WARMUP_INTERVAL_HOURS=24
BASE_PATH=
```

---

## 📄 Litsenziya

MIT License

---

> **Diqqat:** Bu dastur faqat o'z akkauntlaringizni boshqarish uchun mo'ljallangan. Telegram foydalanish shartlarini buzish uchun foydalanmang.
