"""
Telethon multi-account manager.
- Akkauntlar StringSession ko'rinishida SQLite'da saqlanadi, har biriga o'z qurilma ma'lumoti,
  api_id/api_hash, proxy, teglar va holat (FloodWait, SpamBot) yoziladi
- .session yonidagi .json (asl app_id, qurilma, 2FA, proxy) avtomatik o'qiladi
- Ulanish rejimi: on_demand (faqat ish paytida ulanadi, bo'sh turganda uziladi) yoki always
- Har bir amal manager.run() orqali: FloodWait eslab qolinadi, kunlik limit, amallar orasida pauza, jurnal
- sessions/ papkaga tashlangan .session / .zip fayllar avtomatik import qilinadi

Sozlamalar .env faylidan olinadi (TG_API_ID, TG_API_HASH, API_KEY, CONNECT_MODE ...).
"""
import asyncio
import json
import os
import re
import random
import shutil
import sqlite3
import time
import zipfile
from typing import Any, Awaitable, Callable
from urllib.parse import quote, unquote, urlsplit
from urllib.request import pathname2url

import aiosqlite
from cryptography.fernet import Fernet, InvalidToken
from telethon import TelegramClient
from telethon.crypto import AuthKey
from telethon.errors import (
    ApiIdInvalidError,
    AuthKeyDuplicatedError,
    AuthKeyNotFound,
    AuthKeyUnregisteredError,
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberUnoccupiedError,
    RPCError,
    SessionExpiredError,
    SessionPasswordNeededError,
    SessionRevokedError,
    UserDeactivatedBanError,
    UserDeactivatedError,
)
from telethon.sessions import SQLiteSession, StringSession

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")


def load_env(path: str = ENV_PATH):
    """Oddiy KEY=VALUE .env o'quvchi (python-dotenv'siz). Mavjud env o'zgaruvchilari ustun."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def save_env(values: dict[str, str], path: str = ENV_PATH):
    """.env dagi kalitlarni yangilaydi (qolgan qatorlarga tegmaydi) va os.environ ga ham yozadi."""
    lines = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    rest = {k: str(v) for k, v in values.items()}
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in rest:
            lines[i] = f"{key}={rest.pop(key)}"
    lines += [f"{k}={v}" for k, v in rest.items()]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.environ.update({k: str(v) for k, v in values.items()})


load_env()


# ---------- Bazadagi maxfiy maydonlarni shifrlash ----------
# Kalit .env dagi ENC_KEY da (yo'q bo'lsa birinchi ishga tushishda yaratiladi).
# Kalit yo'qolsa bazadagi sessiyalarni ochib bo'lmaydi — .env ni ham zaxiralang.
SECRET_FIELDS = ("session", "twofa")


def _load_fernet() -> Fernet:
    key = os.getenv("ENC_KEY")
    if not key:
        key = Fernet.generate_key().decode()
        save_env({"ENC_KEY": key})
    return Fernet(key.encode())


FERNET = _load_fernet()


def enc(value: str | None) -> str | None:
    if value is None or str(value).startswith("enc:"):
        return value
    return "enc:" + FERNET.encrypt(str(value).encode()).decode()


def dec(value: str | None) -> str | None:
    if isinstance(value, str) and value.startswith("enc:"):
        try:
            return FERNET.decrypt(value[4:].encode()).decode()
        except InvalidToken:
            raise RuntimeError("ENC_KEY noto'g'ri — bazadagi sessiyalarni ochib bo'lmadi")
    return value


def dec_row(row: dict) -> dict:
    for f in SECRET_FIELDS:
        if f in row:
            row[f] = dec(row[f])
    return row

API_ID = int(os.getenv("TG_API_ID") or 0)     # my.telegram.org
API_HASH = os.getenv("TG_API_HASH", "")
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "accounts.db"))
SESSIONS_DIR = os.getenv("SESSIONS_DIR", os.path.join(BASE_DIR, "sessions"))
CONNECT_CONCURRENCY = 5      # bir vaqtda nechta akkaunt ulanadi
CONNECT_TIMEOUT = 25         # soniya; o'lik sessiyada Telethon connect() cheksiz osilib qoladi
PENDING_TTL = 600            # tugallanmagan login necha soniyadan keyin bekor qilinadi
MAX_SESSION_SIZE = 2 * 1024 * 1024   # bitta .session fayl chegarasi (zip ichida ham)
MAX_JSON_SIZE = 256 * 1024
MAX_ZIP_FILES = 1000                 # bitta zip ichidan nechta .session olinadi

# .env orqali o'zgartiriladigan ish sozlamalari (veb-paneldan ham)
OPTION_DEFAULTS = {
    "CONNECT_MODE": "on_demand",   # on_demand | always
    "IDLE_MINUTES": "10",          # on_demand: shuncha daqiqa ishlatilmasa uziladi
    "DAILY_MSG_LIMIT": "30",       # bitta akkauntdan kuniga nechta xabar (0 = cheksiz)
    "ACTION_DELAY": "3",           # bitta akkauntdagi ketma-ket amallar orasidagi minimal pauza, s
    "DAILY_CHECK_HOUR": "9",       # kunlik tekshiruv soati (0-23), -1 = o'chiq
    "BACKUP_KEEP": "7",            # backups/auto da nechta kunlik zaxira saqlanadi (0 = avtomatik zaxira yo'q)
    "AUTO_BACKUP_TO_BOT": "0",     # 1 = kunlik zaxira admin-botga ham yuboriladi
    "WARMUP_INTERVAL_HOURS": "24",  # avtomatik warm-up intervali (soat), 0 = o'chiq
}
OPTION_RANGES = {"IDLE_MINUTES": (1, 1440), "DAILY_MSG_LIMIT": (0, 100000), "ACTION_DELAY": (0, 3600),
                 "DAILY_CHECK_HOUR": (-1, 23), "BACKUP_KEEP": (0, 365), "AUTO_BACKUP_TO_BOT": (0, 1),
                 "WARMUP_INTERVAL_HOURS": (0, 168)}

# Qurilma ma'lumoti .json bo'lmaganda (login yoki oddiy .session): har akkauntga bir marta yoziladi
DEFAULT_DEVICE = {
    "device_model": "PC 64bit",
    "system_version": "Linux",
    "app_version": "1.0",
    "lang_code": "en",
    "system_lang_code": "en-US",
}

# Haqiqiy qurilma profillari — import qilinganda .json bo'lmasa tasodifiy tanlanadi
DEVICE_PROFILES = [
    # Android
    {"device_model": "Samsung Galaxy S24 Ultra", "system_version": "Android 15", "app_version": "11.6.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S24", "system_version": "Android 15", "app_version": "11.6.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S23 Ultra", "system_version": "Android 14", "app_version": "11.5.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy A55", "system_version": "Android 14", "app_version": "11.4.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy Z Flip5", "system_version": "Android 14", "app_version": "11.5.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Google Pixel 9 Pro", "system_version": "Android 15", "app_version": "11.6.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Google Pixel 8", "system_version": "Android 15", "app_version": "11.6.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Google Pixel 7a", "system_version": "Android 14", "app_version": "11.4.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Xiaomi 14 Ultra", "system_version": "Android 14", "app_version": "11.5.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Xiaomi 13T Pro", "system_version": "Android 14", "app_version": "11.4.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Xiaomi Redmi Note 13 Pro", "system_version": "Android 14", "app_version": "11.4.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "OnePlus 12", "system_version": "Android 14", "app_version": "11.5.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "OnePlus Nord 3", "system_version": "Android 14", "app_version": "11.4.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Huawei P60 Pro", "system_version": "Android 13", "app_version": "11.3.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "OPPO Find X7 Ultra", "system_version": "Android 14", "app_version": "11.5.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Vivo X100 Pro", "system_version": "Android 14", "app_version": "11.5.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Realme GT5 Pro", "system_version": "Android 14", "app_version": "11.4.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "POCO F6 Pro", "system_version": "Android 14", "app_version": "11.4.2", "lang_code": "en", "system_lang_code": "en-US"},
    # iOS
    {"device_model": "iPhone 16 Pro Max", "system_version": "iOS 18.2", "app_version": "11.6.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 16 Pro", "system_version": "iOS 18.2", "app_version": "11.6.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 16", "system_version": "iOS 18.1", "app_version": "11.5.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 15 Pro Max", "system_version": "iOS 18.1", "app_version": "11.5.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 15 Pro", "system_version": "iOS 17.7", "app_version": "11.5.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 15", "system_version": "iOS 17.6", "app_version": "11.4.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 14 Pro Max", "system_version": "iOS 17.7", "app_version": "11.5.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 14 Pro", "system_version": "iOS 17.6", "app_version": "11.4.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 14", "system_version": "iOS 17.5", "app_version": "11.4.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 13 Pro", "system_version": "iOS 17.4", "app_version": "11.3.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 13", "system_version": "iOS 17.3", "app_version": "11.3.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPad Pro 12.9", "system_version": "iOS 18.1", "app_version": "11.5.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPad Air M2", "system_version": "iOS 17.6", "app_version": "11.4.2", "lang_code": "en", "system_lang_code": "en-US"},
    # Desktop
    {"device_model": "PC 64bit", "system_version": "Windows 11", "app_version": "5.8.3", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "PC 64bit", "system_version": "Windows 10", "app_version": "5.7.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "MacBook Pro", "system_version": "macOS 15.2", "app_version": "11.5.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "MacBook Air", "system_version": "macOS 14.7", "app_version": "11.4.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iMac", "system_version": "macOS 15.1", "app_version": "11.5.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "PC 64bit", "system_version": "Linux x86_64", "app_version": "5.8.3", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "PC 64bit", "system_version": "Ubuntu 24.04", "app_version": "5.8.3", "lang_code": "en", "system_lang_code": "en-US"},
]


def generate_device() -> dict:
    """Tasodifiy haqiqiy qurilma profili — Telegram tabiiy ko'rsin."""
    return dict(random.choice(DEVICE_PROFILES))

# Pyrogram faylida server manzili saqlanmaydi, shuning uchun DC IP'lari
DC_IPS = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}

# Akkaunt/sessiya butunlay ishlamay qolganini bildiruvchi xatolar
DEAD_ERRORS = (
    AuthKeyNotFound,               # Telegram auth_key ni umuman tanimaydi
    AuthKeyUnregisteredError,
    AuthKeyDuplicatedError,
    SessionRevokedError,
    SessionExpiredError,
    UserDeactivatedError,
    UserDeactivatedBanError,
)

# accounts jadvali ustunlari (yangi ustunlar eski bazaga avtomatik qo'shiladi)
ACCOUNT_COLUMNS = {
    "phone": "TEXT PRIMARY KEY",
    "session": "TEXT NOT NULL",
    "user_id": "INTEGER",
    "username": "TEXT",
    "first_name": "TEXT",
    "active": "INTEGER DEFAULT 1",
    "last_error": "TEXT",
    "added_at": "INTEGER",
    "api_id": "INTEGER",            # NULL -> global TG_API_ID
    "api_hash": "TEXT",
    "device_model": "TEXT",
    "system_version": "TEXT",
    "app_version": "TEXT",
    "lang_code": "TEXT",
    "system_lang_code": "TEXT",
    "proxy": "TEXT",                # socks5://user:pass@host:port | http://...
    "twofa": "TEXT",                # ma'lum bo'lsa 2FA parol (.json dan yoki paneldan qo'yilgan)
    "tags": "TEXT",                 # vergul bilan: "asosiy,zaxira"
    "note": "TEXT",
    "flood_until": "INTEGER",
    "spam_status": "TEXT",          # ok | limited | unknown
    "spam_text": "TEXT",
    "spam_checked_at": "INTEGER",
    "last_used_at": "INTEGER",
}
# PATCH orqali o'zgartirish mumkin bo'lgan ustunlar; CONN_FIELDS o'zgarsa client qayta ulanadi
EDITABLE_FIELDS = {"tags", "note", "proxy", "api_id", "api_hash", *DEFAULT_DEVICE}
CONN_FIELDS = {"proxy", "api_id", "api_hash", *DEFAULT_DEVICE}


class NotConfiguredError(RuntimeError):
    """TG_API_ID / TG_API_HASH o'rnatilmagan."""


class LoginError(ValueError):
    """Login oqimidagi foydalanuvchi xatosi."""


class AccountError(Exception):
    """Akkaunt bilan ishlashdagi xato; status — HTTP kodi."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def normalize_phone(phone: str) -> str:
    """'+998 90 123-45-67', '998901234567' -> '+998901234567'"""
    digits = "".join(c for c in str(phone) if c.isdigit())
    if len(digits) < 7:
        raise LoginError("Telefon raqam noto'g'ri")
    return "+" + digits


# ---------- Proxy ----------
_PROXY_TYPES = {"socks5", "socks4", "http"}


def parse_proxy(value: Any) -> str | None:
    """
    Turli ko'rinishdagi proxy'ni 'socks5://user:pass@host:port' ga keltiradi.
    Qabul qiladi: URL, 'host:port[:user:pass]', ro'yxat [type, host, port, rdns, user, pass], dict.
    Bo'sh qiymat -> None. Noto'g'ri -> ValueError.
    """
    if value in (None, "", [], {}):
        return None
    scheme, host, port, user, pwd = "socks5", None, None, None, None
    if isinstance(value, dict):
        scheme = str(value.get("proxy_type") or value.get("type") or value.get("scheme") or "socks5")
        host = value.get("addr") or value.get("host") or value.get("hostname")
        port = value.get("port")
        user = value.get("username") or value.get("user") or value.get("login")
        pwd = value.get("password") or value.get("pass")
    elif isinstance(value, (list, tuple)):
        items = list(value) + [None] * 6
        scheme, host, port = items[0], items[1], items[2]
        user, pwd = items[4], items[5]
        scheme = {1: "socks4", 2: "socks5", 3: "http"}.get(scheme, scheme)
    else:
        s = str(value).strip()
        if "://" in s:
            u = urlsplit(s)
            scheme, host = u.scheme, u.hostname
            try:
                port = u.port
            except ValueError:
                port = None
            user = unquote(u.username) if u.username else None
            pwd = unquote(u.password) if u.password else None
        else:
            parts = s.split(":")
            if len(parts) not in (2, 4):
                raise ValueError("Proxy formati: socks5://user:pass@host:port yoki host:port:user:pass")
            host, port = parts[0], parts[1]
            if len(parts) == 4:
                user, pwd = parts[2], parts[3]
    scheme = str(scheme).lower().replace("https", "http")
    if scheme not in _PROXY_TYPES:
        raise ValueError(f"Proxy turi qo'llanmaydi: {scheme} (socks5, socks4, http)")
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError("Proxy porti noto'g'ri")
    if not host or not 0 < port < 65536:
        raise ValueError("Proxy manzili noto'g'ri")
    auth = ""
    if user:
        auth = quote(str(user), safe="") + (":" + quote(str(pwd), safe="") if pwd else "") + "@"
    return f"{scheme}://{auth}{host}:{port}"


def proxy_to_telethon(url: str | None) -> dict | None:
    if not url:
        return None
    u = urlsplit(url)
    return {
        "proxy_type": u.scheme, "addr": u.hostname, "port": u.port, "rdns": True,
        "username": unquote(u.username) if u.username else None,
        "password": unquote(u.password) if u.password else None,
    }


def mask_proxy(url: str | None) -> str | None:
    """Parolni yashiradi: socks5://user:***@host:port"""
    if not url:
        return None
    u = urlsplit(url)
    auth = f"{unquote(u.username)}:***@" if u.username else ""
    return f"{u.scheme}://{auth}{u.hostname}:{u.port}"


# ---------- .session / .json fayllar ----------
def session_file_to_string(path: str) -> str:
    """Telethon yoki Pyrogram (pyrofork ham) .session (SQLite) faylidan Telethon StringSession yasaydi."""
    uri = "file:" + pathname2url(os.path.abspath(path)) + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        raise ValueError("Faylni ochib bo'lmadi")
    try:
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
        except sqlite3.DatabaseError:
            raise ValueError("SQLite fayl emas (buzilgan yoki boshqa format)")
        if not {"dc_id", "auth_key"} <= cols:
            raise ValueError("'sessions' jadvali yo'q, Telethon/Pyrogram fayli emas")

        if "test_mode" in cols:                              # Pyrogram
            test = con.execute("SELECT test_mode FROM sessions").fetchone()
            if test and test[0]:
                raise ValueError("Pyrogram test-server sessiyasi")
        if "is_bot" in cols:
            bot = con.execute("SELECT is_bot FROM sessions").fetchone()
            if bot and bot[0]:
                raise ValueError("Bot sessiyasi, user akkaunt emas")

        has_addr = {"server_address", "port"} <= cols       # Telethon (va pyrofork)
        row = con.execute(
            "SELECT dc_id, auth_key" + (", server_address, port" if has_addr else "") + " FROM sessions"
        ).fetchone()
    finally:
        con.close()

    if not row:
        raise ValueError("Sessiya bo'sh")
    dc_id, key = row[0], row[1]
    address, port = (row[2], row[3]) if has_addr else (None, None)
    if not address:
        address, port = DC_IPS.get(dc_id), 443
        if not address:
            raise ValueError(f"Noma'lum DC: {dc_id}")
    if not key:
        raise ValueError("Faylda auth_key yo'q (bo'sh sessiya)")

    s = StringSession()
    s.set_dc(dc_id, address, port or 443)
    s.auth_key = AuthKey(bytes(key))
    return s.save()


def string_to_session_file(session_str: str, path: str):
    """StringSession -> Telethon .session fayli (zaxira eksport uchun)."""
    src = StringSession(session_str)
    base = path[:-len(".session")] if path.endswith(".session") else path
    dst = SQLiteSession(base)
    dst.set_dc(src.dc_id, src.server_address, src.port)
    dst.auth_key = src.auth_key
    dst.save()
    dst.close()


_LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


def parse_session_json(data: Any) -> dict:
    """
    Sessiya bilan keladigan .json (sotuvchilar / tdata konverterlari formati) dan
    ulanish ma'lumotlarini ajratadi. Noma'lum kalitlar e'tiborsiz qoldiriladi.
    """
    if not isinstance(data, dict):
        return {}

    def pick(*keys):
        for k in keys:
            v = data.get(k)
            if v not in (None, "", 0):
                return v
        return None

    meta: dict = {}
    api_id, api_hash = pick("app_id", "api_id"), pick("app_hash", "api_hash")
    try:
        if api_id and api_hash and len(str(api_hash)) == 32:
            meta["api_id"], meta["api_hash"] = int(api_id), str(api_hash).lower()
    except (TypeError, ValueError):
        pass
    for col, keys in {
        "device_model": ("device_model", "device"),
        "system_version": ("system_version", "sdk", "system"),
        "app_version": ("app_version",),
    }.items():
        v = pick(*keys)
        if v:
            meta[col] = str(v)[:64]
    for col, keys in {"lang_code": ("lang_code", "lang_pack"), "system_lang_code": ("system_lang_code", "system_lang_pack")}.items():
        v = pick(*keys)
        if v and _LANG_RE.match(str(v)):          # lang_pack ba'zan "tdesktop" — u til kodi emas
            meta[col] = str(v)
    twofa = pick("twoFA", "twofa", "2fa", "password", "two_fa")
    if twofa:
        meta["twofa"] = str(twofa)
    try:
        proxy = parse_proxy(data.get("proxy"))
        if proxy:
            meta["proxy"] = proxy
    except ValueError:
        pass
    return meta


def load_session_json(session_path: str) -> dict:
    """foo.session yonidagi foo.json ni o'qiydi (bo'lmasa {})."""
    path = os.path.splitext(session_path)[0] + ".json"
    if not os.path.isfile(path) or os.path.getsize(path) > MAX_JSON_SIZE:
        return {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            return parse_session_json(json.load(f))
    except (OSError, ValueError):
        return {}


def _unique_path(dst_dir: str, name: str) -> str:
    """dst_dir/name, band bo'lsa name_1, name_2 ..."""
    dst = os.path.join(dst_dir, name)
    stem, ext = os.path.splitext(name)
    i = 1
    while os.path.exists(dst):
        dst = os.path.join(dst_dir, f"{stem}_{i}{ext}")
        i += 1
    return dst


def _move(src: str, dst_dir: str) -> str:
    """Faylni papkaga ko'chiradi; shu nomli fayl bo'lsa ustiga yozmaydi."""
    dst = _unique_path(dst_dir, os.path.basename(src))
    shutil.move(src, dst)
    return dst


def _move_with_json(session_path: str, dst_dir: str) -> str:
    """.session va yonidagi .json ni birga ko'chiradi (nomlari mos qoladi)."""
    dst = _move(session_path, dst_dir)
    js = os.path.splitext(session_path)[0] + ".json"
    if os.path.isfile(js):
        shutil.move(js, os.path.splitext(dst)[0] + ".json")
    return dst


def extract_sessions_from_zip(zip_path: str, dest_dir: str) -> list[str]:
    """
    Zip ichidagi barcha .session fayllarni (ichki papkalardan ham) dest_dir ga chiqaradi.
    Yonidagi bir xil nomli .json ham birga chiqariladi.
    Faqat fayl nomi olinadi (../ yo'llar ishlamaydi), katta fayllar o'tkazib yuboriladi.
    """
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise ValueError("Zip fayl buzilgan yoki zip emas")
    out = []
    with zf:
        members = {i.filename.replace("\\", "/"): i for i in zf.infolist() if not i.is_dir()}
        for fname, info in members.items():
            name = os.path.basename(fname)
            if not name.endswith(".session") or name.startswith("._"):
                continue                                  # ._ — macOS axlat fayllari
            if info.file_size > MAX_SESSION_SIZE:
                continue
            if info.flag_bits & 0x1:
                raise ValueError("Zip parol bilan himoyalangan")
            dst = _unique_path(dest_dir, name)
            with zf.open(info) as src, open(dst, "wb") as f:
                f.write(src.read(MAX_SESSION_SIZE))
            js = members.get(fname[: -len(".session")] + ".json")
            if js and js.file_size <= MAX_JSON_SIZE:
                with zf.open(js) as src, open(os.path.splitext(dst)[0] + ".json", "wb") as f:
                    f.write(src.read(MAX_JSON_SIZE))
            out.append(dst)
            if len(out) >= MAX_ZIP_FILES:
                break
    if not out:
        raise ValueError("Zip ichida .session fayl yo'q")
    return out


def classify_spambot(text: str) -> str:
    """@SpamBot javobidan holat: ok | limited | unknown"""
    low = text.lower()
    ok = ("good news", "no limits", "free as a bird", "свободен", "не ограничен", "нет ограничений")
    bad = ("limited", "restricted", "ограничен", "ограничения", "until", "banned", "заблокирован")
    if any(k in low for k in ok):
        return "ok"
    if any(k in low for k in bad):
        return "limited"
    return "unknown"


class AccountManager:
    def __init__(self, api_id: int = API_ID, api_hash: str = API_HASH, db_path: str = DB_PATH):
        self.api_id = api_id
        self.api_hash = api_hash
        self.db_path = db_path
        self.clients: dict[str, TelegramClient] = {}                       # phone -> ulangan client
        self.pending: dict[str, tuple[TelegramClient, str, float]] = {}   # phone -> (client, code_hash, vaqt)
        self.options = {k: os.getenv(k, v) for k, v in OPTION_DEFAULTS.items()}
        self.job: dict = {"name": None, "running": False}                  # fon ishi (SpamBot tekshiruvi)
        self._scan_lock = asyncio.Lock()
        self._connect_sem = asyncio.Semaphore(CONNECT_CONCURRENCY)
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_touch: dict[str, float] = {}     # phone -> oxirgi foydalanilgan vaqt
        self._last_action: dict[str, float] = {}    # phone -> oxirgi amal tugagan vaqt
        self.listeners: list[Callable[[str, dict], Awaitable]] = []   # hodisa tinglovchilar (admin-bot)
        self.inbox: dict[str, dict] = {}            # phone -> {"at": ts, "items": [...], "codes": [...]}

    async def emit(self, event: str, **data):
        """Hodisa: dead | flood | spam_limited | new_auth | job_done | report"""
        for fn in list(self.listeners):
            try:
                await fn(event, data)
            except Exception as e:
                print(f"[!] listener xatosi ({event}): {e!r}")

    # ---------- Sozlamalar ----------
    @property
    def configured(self) -> bool:
        return bool(self.api_id and self.api_hash)

    def _require_config(self):
        if not self.configured:
            raise NotConfiguredError("TG_API_ID / TG_API_HASH .env da o'rnatilmagan")

    @property
    def on_demand(self) -> bool:
        return self.options["CONNECT_MODE"] != "always"

    def opt_int(self, key: str) -> int:
        try:
            return int(self.options[key])
        except (TypeError, ValueError):
            return int(OPTION_DEFAULTS[key])

    async def configure(self, api_id: int, api_hash: str):
        """API_ID / API_HASH ni ishlab turgan paytda o'rnatadi, .env ga saqlaydi va akkauntlarni qayta ulaydi."""
        api_hash = api_hash.strip().lower()
        if api_id <= 0 or len(api_hash) != 32 or any(c not in "0123456789abcdef" for c in api_hash):
            raise ValueError("api_id musbat son, api_hash esa 32 belgili hex bo'lishi kerak")
        save_env({"TG_API_ID": str(api_id), "TG_API_HASH": api_hash})
        await self._disconnect_all()               # eski kalitlar bilan ulanganlarni uzamiz
        self.api_id, self.api_hash = api_id, api_hash
        if not self.on_demand:
            await self.connect_missing()

    async def set_options(self, values: dict):
        clean = {}
        for key, value in values.items():
            key = key.upper()
            if key not in OPTION_DEFAULTS or value is None:
                continue
            if key == "CONNECT_MODE":
                if value not in ("on_demand", "always"):
                    raise ValueError("CONNECT_MODE: on_demand yoki always")
                clean[key] = value
            else:
                try:
                    n = int(value)
                except (TypeError, ValueError):
                    raise ValueError(f"{key} son bo'lishi kerak")
                lo, hi = OPTION_RANGES[key]
                if not lo <= n <= hi:
                    raise ValueError(f"{key}: {lo}..{hi} oralig'ida bo'lishi kerak")
                clean[key] = str(n)
        save_env(clean)
        self.options.update(clean)
        if not self.on_demand and self.configured:
            asyncio.create_task(self.connect_missing())

    # ---------- DB ----------
    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            cols_sql = ",\n".join(f"{c} {t}" for c, t in ACCOUNT_COLUMNS.items())
            await db.execute(f"CREATE TABLE IF NOT EXISTS accounts ({cols_sql})")
            existing = {r[1] async for r in await db.execute("PRAGMA table_info(accounts)")}
            for col, typ in ACCOUNT_COLUMNS.items():
                if col not in existing:        # eski bazalar uchun
                    await db.execute(f"ALTER TABLE accounts ADD COLUMN {col} {typ.replace('PRIMARY KEY', '')}")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS actions (
                    id     INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone  TEXT,
                    action TEXT,
                    ok     INTEGER,
                    detail TEXT,
                    ts     INTEGER
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS actions_phone_ts ON actions(phone, ts)")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS proxies (
                    url TEXT PRIMARY KEY, ok INTEGER, latency_ms INTEGER, error TEXT,
                    checked_at INTEGER, added_at INTEGER
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    day TEXT PRIMARY KEY, total INTEGER, active INTEGER, dead INTEGER,
                    limited INTEGER, flood INTEGER
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS known_auths (
                    phone TEXT, hash TEXT, device TEXT, first_seen INTEGER, PRIMARY KEY (phone, hash)
                )
            """)
            # ochiq saqlangan sessiya / 2FA larni shifrlaymiz
            async with db.execute("SELECT phone, session, twofa FROM accounts") as cur:
                rows = await cur.fetchall()
            for phone, sess, twofa in rows:
                upd = {k: enc(v) for k, v in (("session", sess), ("twofa", twofa)) if v and not v.startswith("enc:")}
                if upd:
                    await db.execute(
                        f"UPDATE accounts SET {', '.join(f'{k}=?' for k in upd)} WHERE phone=?", (*upd.values(), phone)
                    )
            # eski yozuvlarga qurilma ma'lumotini bir marta yozib qo'yamiz
            for col, val in DEFAULT_DEVICE.items():
                await db.execute(f"UPDATE accounts SET {col}=? WHERE {col} IS NULL", (val,))
            await db.commit()
        if not self.configured:
            print("[!] TG_API_ID / TG_API_HASH o'rnatilmagan — akkauntlar ulanmaydi")
        elif not self.on_demand:
            await self.connect_missing()

    async def get_account(self, phone: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM accounts WHERE phone=?", (phone,)) as cur:
                row = await cur.fetchone()
        return dec_row(dict(row)) if row else None

    async def _update(self, phone: str, **fields):
        bad = set(fields) - set(ACCOUNT_COLUMNS)
        if bad:
            raise ValueError(f"Noma'lum ustun: {bad}")
        if not fields:
            return
        fields = {k: enc(v) if k in SECRET_FIELDS else v for k, v in fields.items()}
        sets = ", ".join(f"{k}=?" for k in fields)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"UPDATE accounts SET {sets} WHERE phone=?", (*fields.values(), phone))
            await db.commit()

    async def log(self, phone: str, action: str, ok: bool, detail: str | None = None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO actions (phone, action, ok, detail, ts) VALUES (?, ?, ?, ?, ?)",
                (phone, action, int(ok), (detail or "")[:500] or None, int(time.time())),
            )
            await db.commit()

    @staticmethod
    def _day_start() -> int:
        t = time.localtime()
        return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))

    async def count_today(self, phone: str, action: str | None = None) -> int:
        q = "SELECT COUNT(*) FROM actions WHERE phone=? AND ok=1 AND ts>=?"
        args: list = [phone, self._day_start()]
        if action:
            q += " AND action=?"
            args.append(action)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(q, args) as cur:
                return (await cur.fetchone())[0]

    async def get_actions(self, phone: str | None = None, limit: int = 100) -> list[dict]:
        q, args = "SELECT phone, action, ok, detail, ts FROM actions", []
        if phone:
            q += " WHERE phone=?"
            args.append(phone)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(q, args) as cur:
                rows = await cur.fetchall()
        return [{"phone": p, "action": a, "ok": bool(o), "detail": d, "ts": t} for p, a, o, d, t in rows]

    # ---------- Client yasash va ulash ----------
    def _client_for(self, acc: dict) -> TelegramClient:
        """Akkauntning o'z api_id, qurilma ma'lumoti va proxy'si bilan client."""
        api_id = acc.get("api_id") or self.api_id
        api_hash = acc.get("api_hash") or self.api_hash
        if not (api_id and api_hash):
            raise NotConfiguredError("TG_API_ID / TG_API_HASH .env da o'rnatilmagan")
        try:
            session = StringSession(acc.get("session") or "")
        except ValueError:
            raise ValueError("StringSession formati noto'g'ri")
        return TelegramClient(
            session, api_id, api_hash,
            device_model=acc.get("device_model") or DEFAULT_DEVICE["device_model"],
            system_version=acc.get("system_version") or DEFAULT_DEVICE["system_version"],
            app_version=acc.get("app_version") or DEFAULT_DEVICE["app_version"],
            lang_code=acc.get("lang_code") or DEFAULT_DEVICE["lang_code"],
            system_lang_code=acc.get("system_lang_code") or DEFAULT_DEVICE["system_lang_code"],
            proxy=proxy_to_telethon(acc.get("proxy")),
            connection_retries=3, retry_delay=2,
        )

    def _new_client(self, session: str = "") -> TelegramClient:
        return self._client_for({"session": session})

    async def _open(self, acc: dict) -> TelegramClient:
        """
        Akkauntni ulaydi va avtorizatsiyani tekshiradi.
        Sessiya o'lik -> akkaunt nofaol qilinadi, AccountError(410); tarmoq/proxy -> AccountError(503).
        """
        phone = acc["phone"]
        async with self._connect_sem:
            if phone in self.clients:
                return self.clients[phone]
            client = self._client_for(acc)
            try:
                await asyncio.wait_for(client.connect(), CONNECT_TIMEOUT)
                authorized = await client.is_user_authorized()
            except DEAD_ERRORS as e:
                await client.disconnect()
                await self.mark_dead(phone, type(e).__name__)
                raise AccountError(410, f"Sessiya o'lik: {type(e).__name__}")
            except ApiIdInvalidError:
                await client.disconnect()
                raise AccountError(400, "Bu akkauntdagi api_id / api_hash noto'g'ri")
            except Exception as e:
                await client.disconnect()
                via = " (proxy orqali)" if acc.get("proxy") else ""
                raise AccountError(503, f"Ulanib bo'lmadi{via}: {type(e).__name__} {e}".strip())
            if not authorized:
                await client.disconnect()
                await self.mark_dead(phone, "Sessiya bekor qilingan")
                raise AccountError(410, "Sessiya bekor qilingan")
            if phone in self.clients:     # shu orada boshqa joyda ulangan bo'lishi mumkin
                await client.disconnect()
                return self.clients[phone]
            self.clients[phone] = client
            self._last_touch[phone] = time.time()
            if acc.get("last_error"):
                await self._update(phone, last_error=None)
            return client

    async def acquire(self, phone: str) -> TelegramClient:
        """Ulangan clientni qaytaradi; on_demand rejimida kerak bo'lganda ulaydi."""
        self._last_touch[phone] = time.time()
        if phone in self.clients:
            return self.clients[phone]
        acc = await self.get_account(phone)
        if not acc:
            raise AccountError(404, "Akkaunt topilmadi")
        if not acc["active"]:
            raise AccountError(409, f"Akkaunt o'chiq: {acc['last_error'] or 'nofaol'}")
        return await self._open(acc)

    async def disconnect(self, phone: str):
        client = self.clients.pop(phone, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def _disconnect_all(self):
        for phone in list(self.clients):
            await self.disconnect(phone)

    async def mark_dead(self, phone: str, reason: str):
        """Sessiya bekor bo'lgan / akkaunt bloklangan: uzib, nofaol qilib qo'yadi."""
        await self.disconnect(phone)
        await self._update(phone, active=0, last_error=reason)
        await self.log(phone, "dead", False, reason)
        print(f"[!] {phone}: o'chirildi ({reason})")
        await self.emit("dead", phone=phone, reason=reason)

    async def connect_missing(self):
        """always rejimi: bazadagi faol, lekin ulanmagan akkauntlarni parallel ulaydi."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM accounts WHERE active=1") as cur:
                rows = [dec_row(dict(r)) for r in await cur.fetchall()]

        async def one(acc):
            try:
                await self._open(acc)
                print(f"[+] {acc['phone']} ulandi")
            except AccountError as e:
                if e.status == 503:
                    print(f"[~] {acc['phone']}: {e}, keyinroq qayta uriniladi")
            except Exception as e:
                print(f"[~] {acc['phone']}: {e!r}")

        await asyncio.gather(*(one(a) for a in rows if a["phone"] not in self.clients))

    async def maintenance(self, interval: int = 30):
        """Fon vazifasi: eskirgan loginlar, bo'sh turgan ulanishlar (on_demand), uzilganlarni ulash (always), warmup."""
        last_reconnect = 0.0
        last_warmup = time.time()
        while True:
            await asyncio.sleep(interval)
            try:
                now = time.time()
                for phone in [p for p, (_, _, t) in self.pending.items() if now - t > PENDING_TTL]:
                    await self.cancel_login(phone)
                if self.on_demand:
                    idle = self.opt_int("IDLE_MINUTES") * 60
                    for phone in list(self.clients):
                        lock = self._locks.get(phone)
                        if (lock is None or not lock.locked()) and now - self._last_touch.get(phone, 0) > idle:
                            await self.disconnect(phone)
                elif self.configured and now - last_reconnect > 60:
                    last_reconnect = now
                    await self.connect_missing()
                # Avtomatik warm-up
                warmup_h = self.opt_int("WARMUP_INTERVAL_HOURS")
                if warmup_h > 0 and self.configured and not self.job.get("running") and now - last_warmup > warmup_h * 3600:
                    last_warmup = now
                    try:
                        await self.start_warmup()
                    except Exception as e:
                        print(f"[!] avtomatik warmup xatosi: {e!r}")
            except Exception as e:
                print(f"[!] maintenance xatosi: {e!r}")

    # ---------- Har bir amal shu orqali ----------
    async def run(self, phone: str, action: str, fn: Callable[[TelegramClient], Awaitable], *, limited: bool = False):
        """
        fn(client) ni xavfsiz bajaradi:
        - FloodWait muddati tugamagan bo'lsa bajarmaydi, yangi FloodWait ni eslab qoladi
        - limited=True bo'lsa kunlik limitni tekshiradi (DAILY_MSG_LIMIT)
        - bitta akkauntda amallar navbat bilan, orasida ACTION_DELAY pauza
        - natija jurnalga yoziladi, o'lik sessiya nofaol qilinadi
        """
        self._require_config()
        phone = normalize_phone(phone)
        acc = await self.get_account(phone)
        if not acc:
            raise AccountError(404, "Akkaunt topilmadi")
        if not acc["active"]:
            raise AccountError(409, f"Akkaunt o'chiq: {acc['last_error'] or 'nofaol'}")
        now = time.time()
        if acc["flood_until"] and acc["flood_until"] > now:
            raise AccountError(429, f"FloodWait: yana {int(acc['flood_until'] - now)} s kutish kerak")
        limit = self.opt_int("DAILY_MSG_LIMIT")
        if limited and limit and await self.count_today(phone, action) >= limit:
            raise AccountError(429, f"Kunlik limit tugadi ({limit} ta)")

        lock = self._locks.setdefault(phone, asyncio.Lock())
        async with lock:
            client = await self.acquire(phone)
            wait = self.opt_int("ACTION_DELAY") - (time.time() - self._last_action.get(phone, 0))
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                result = await fn(client)
            except FloodWaitError as e:
                await self._update(phone, flood_until=int(time.time()) + e.seconds)
                await self.log(phone, action, False, f"FloodWait {e.seconds}s")
                await self.emit("flood", phone=phone, seconds=e.seconds, action=action)
                raise
            except DEAD_ERRORS as e:
                await self.mark_dead(phone, type(e).__name__)
                raise AccountError(410, f"Akkaunt ishlamay qoldi: {type(e).__name__}")
            except Exception as e:
                await self.log(phone, action, False, f"{type(e).__name__}: {e}")
                raise
            finally:
                self._last_action[phone] = self._last_touch[phone] = time.time()
            await self.log(phone, action, True)
            await self._update(phone, last_used_at=int(time.time()))
            return result

    # ---------- Login ----------
    async def send_code(self, phone: str) -> str:
        self._require_config()
        phone = normalize_phone(phone)
        await self.cancel_login(phone)            # eski tugallanmagan login bo'lsa yopiladi
        client = self._new_client()
        try:
            await asyncio.wait_for(client.connect(), CONNECT_TIMEOUT)
            sent = await client.send_code_request(phone)
        except BaseException:
            await client.disconnect()
            raise
        self.pending[phone] = (client, sent.phone_code_hash, time.time())
        return phone

    def _get_pending(self, phone: str) -> tuple[str, TelegramClient, str]:
        phone = normalize_phone(phone)
        item = self.pending.get(phone)
        if not item:
            raise LoginError("Avval send-code chaqiring (yoki login muddati o'tgan)")
        return phone, item[0], item[1]

    async def confirm_code(self, phone: str, code: str) -> dict:
        """status: 'ok' | 'password_needed' | 'invalid_code' | 'expired' | 'not_registered'"""
        phone, client, code_hash = self._get_pending(phone)
        try:
            await client.sign_in(phone=phone, code=code.strip(), phone_code_hash=code_hash)
        except SessionPasswordNeededError:
            return {"status": "password_needed"}
        except PhoneCodeInvalidError:
            return {"status": "invalid_code"}
        except PhoneCodeExpiredError:
            await self.cancel_login(phone)
            return {"status": "expired"}
        except PhoneNumberUnoccupiedError:
            await self.cancel_login(phone)
            return {"status": "not_registered"}
        self.pending.pop(phone, None)
        return {"status": "ok", **await self._finish_login(client)}

    async def confirm_password(self, phone: str, password: str) -> dict:
        phone, client, _ = self._get_pending(phone)
        await client.sign_in(password=password)   # PasswordHashInvalidError -> qayta urinish mumkin
        self.pending.pop(phone, None)
        return {"status": "ok", **await self._finish_login(client, {"twofa": password})}

    async def _finish_login(self, client: TelegramClient, meta: dict | None = None) -> dict:
        """
        Ulangan clientni bazaga yozadi. Kalit har doim Telegram qaytargan raqam: +998...
        meta — .json dan kelgan api_id / qurilma / proxy / 2FA (bo'lsa, eski qiymatlar ustidan yoziladi).
        """
        meta = dict(meta or {})
        try:
            me = await client.get_me()
            if me.bot:
                raise ValueError("Bot akkaunt qo'shilmaydi")
            if not me.phone:
                raise ValueError("Akkaunt telefon raqamini olib bo'lmadi")
        except BaseException:
            await client.disconnect()
            raise
        phone = normalize_phone(me.phone)
        base = {
            "phone": phone, "session": client.session.save(), "user_id": me.id,
            "username": me.username, "first_name": me.first_name, "active": 1, "last_error": None,
            "flood_until": None,
        }
        insert = {**base, **generate_device(), "added_at": int(time.time()), **meta}
        update = {**base, **meta}
        for f in SECRET_FIELDS:
            if insert.get(f) is not None:
                insert[f] = enc(insert[f])
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"INSERT INTO accounts ({', '.join(insert)}) VALUES ({', '.join('?' * len(insert))}) "
                f"ON CONFLICT(phone) DO UPDATE SET {', '.join(f'{k}=excluded.{k}' for k in update if k != 'phone')}",
                tuple(insert.values()),
            )
            await db.commit()
        old = self.clients.get(phone)
        self.clients[phone] = client
        self._last_touch[phone] = time.time()
        if old and old is not client:
            await old.disconnect()
        await self.log(phone, "import", True, ("json: " + ", ".join(sorted(meta))) if meta else None)
        print(f"[+] {phone} qo'shildi")
        return {"phone": phone, "user_id": me.id, "username": me.username, "json": sorted(meta)}

    async def cancel_login(self, phone: str):
        item = self.pending.pop(normalize_phone(phone), None)
        if item:
            await item[0].disconnect()

    # ---------- Tayyor sessiyalarni import qilish ----------
    async def import_string(self, session_str: str, meta: dict | None = None) -> dict:
        """Telethon StringSession matnini qo'shadi. meta — .json dagi ulanish ma'lumotlari."""
        self._require_config()
        meta = meta or {}
        client = self._client_for({"session": session_str.strip(), **meta})
        try:
            await asyncio.wait_for(client.connect(), CONNECT_TIMEOUT)
            if not await client.is_user_authorized():
                raise ValueError("Sessiya yaroqsiz yoki akkauntdan chiqib ketilgan")
        except AuthKeyNotFound:
            await client.disconnect()
            raise ValueError("Telegram bu sessiyani tanimaydi (o'chirilgan yoki soxta)")
        except asyncio.TimeoutError:
            await client.disconnect()
            raise ValueError(f"Telegram {CONNECT_TIMEOUT}s ichida javob bermadi — sessiya o'lik bo'lishi mumkin")
        except BaseException:
            await client.disconnect()
            raise
        return await self._finish_login(client, meta)

    async def import_session_file(self, path: str) -> dict:
        """Telethon yoki Pyrogram .session faylini (yonida .json bo'lsa u bilan) qo'shadi."""
        return await self.import_string(session_file_to_string(path), load_session_json(path))

    # ---------- Papkani avtomatik kuzatish ----------
    async def scan_folder(self, folder: str = SESSIONS_DIR) -> list[dict]:
        """
        Papkadagi yangi .session / .zip fayllarni tekshiradi:
          ishlasa          -> bazaga qo'shiladi, fayl (+ .json)  folder/imported/  ga ko'chadi
          yaroqsiz         -> fayl  folder/failed/  ga ko'chadi (+ sababi .txt da)
          tarmoq/FloodWait -> fayl joyida qoladi, keyingi aylanishda qayta uriniladi
        """
        self._require_config()
        async with self._scan_lock:             # watcher va /scan bir faylni ikki marta olmasin
            return await self._scan(folder)

    async def _scan(self, folder: str) -> list[dict]:
        ok_dir = os.path.join(folder, "imported")
        bad_dir = os.path.join(folder, "failed")
        os.makedirs(ok_dir, exist_ok=True)
        os.makedirs(bad_dir, exist_ok=True)

        results = []
        fresh = set()                                    # zipdan hozirgina chiqqanlar — kutish shart emas
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not (name.lower().endswith(".zip") and os.path.isfile(path)):
                continue
            if time.time() - os.path.getmtime(path) < 3:
                continue
            try:
                extracted = extract_sessions_from_zip(path, folder)
                fresh.update(os.path.basename(p) for p in extracted)
                _move(path, ok_dir)
                print(f"[*] {name}: {len(extracted)} ta .session chiqarildi")
            except ValueError as e:
                dst = _move(path, bad_dir)
                with open(dst + ".txt", "w", encoding="utf-8") as f:
                    f.write(str(e))
                results.append({"file": name, "ok": False, "error": str(e)})
                print(f"[x] {name}: {e}")

        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not (name.endswith(".session") and os.path.isfile(path)):
                continue
            if name not in fresh and time.time() - os.path.getmtime(path) < 3:   # hali yozilayotgan bo'lishi mumkin
                continue
            try:
                info = await self.import_session_file(path)
                _move_with_json(path, ok_dir)
                results.append({"file": name, "ok": True, **info})
            except FloodWaitError as e:
                results.append({"file": name, "ok": False, "error": f"FloodWait {e.seconds}s, qayta uriniladi"})
                print(f"[~] {name}: FloodWait {e.seconds}s")
                break                                    # qolganlarini ham keyinga qoldiramiz
            except ApiIdInvalidError:
                raise NotConfiguredError("TG_API_ID / TG_API_HASH noto'g'ri")
            except (ValueError, RPCError) as e:
                reason = str(e) or type(e).__name__
                dst = _move_with_json(path, bad_dir)
                with open(dst + ".txt", "w", encoding="utf-8") as f:
                    f.write(reason)
                results.append({"file": name, "ok": False, "error": reason})
                print(f"[x] {name}: {reason}")
            except Exception as e:
                results.append({"file": name, "ok": False, "error": f"qayta uriniladi: {e!r}"})
                print(f"[~] {name}: tarmoq xatosi, keyinroq qayta uriniladi ({e!r})")
            await asyncio.sleep(1)
        return results

    async def watch_folder(self, folder: str = SESSIONS_DIR, interval: int = 10):
        """Fon vazifasi: har `interval` soniyada papkani tekshiradi."""
        os.makedirs(folder, exist_ok=True)
        print(f"[*] {os.path.abspath(folder)} kuzatilmoqda")
        while True:
            if self.configured:
                try:
                    await self.scan_folder(folder)
                except Exception as e:
                    print(f"[!] watch_folder xatosi: {e!r}")
            await asyncio.sleep(interval)

    # ---------- Boshqarish ----------
    async def list_accounts(self) -> list[dict]:
        day = self._day_start()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM accounts ORDER BY added_at") as cur:
                rows = [dict(r) for r in await cur.fetchall()]
            async with db.execute(
                "SELECT phone, COUNT(*) FROM actions WHERE ok=1 AND ts>=? AND action='send_message' GROUP BY phone",
                (day,),
            ) as cur:
                sent_today = {p: n for p, n in await cur.fetchall()}
        return [self.public(r, sent_today.get(r["phone"], 0)) for r in rows]

    def public(self, acc: dict, sent_today: int | None = None) -> dict:
        """Akkaunt ma'lumoti: sessiya va parollarsiz."""
        now = time.time()
        out = {k: v for k, v in acc.items() if k not in ("session", "twofa", "api_hash", "proxy")}
        out.update({
            "active": bool(acc["active"]),
            "connected": acc["phone"] in self.clients,
            "proxy": mask_proxy(acc.get("proxy")),
            "own_api": bool(acc.get("api_id")),
            "has_twofa_saved": bool(acc.get("twofa")),
            "flood_left": max(0, int((acc.get("flood_until") or 0) - now)),
            "tags": [t for t in (acc.get("tags") or "").split(",") if t],
        })
        if sent_today is not None:
            out["sent_today"] = sent_today
        return out

    async def update_account(self, phone: str, fields: dict) -> dict:
        """Teglar, izoh, proxy, qurilma, api_id/hash ni o'zgartiradi."""
        phone = normalize_phone(phone)
        acc = await self.get_account(phone)
        if not acc:
            raise AccountError(404, "Akkaunt topilmadi")
        clean: dict = {}
        for k, v in fields.items():
            if k not in EDITABLE_FIELDS:
                continue
            if isinstance(v, str):
                v = v.strip()
            if k == "proxy":
                v = parse_proxy(v) if v else None
            elif k == "tags":
                items = v if isinstance(v, list) else str(v or "").split(",")
                v = ",".join(dict.fromkeys(t.strip().lower() for t in items if t and t.strip())) or None
            elif k == "api_id":
                try:
                    v = int(v) if v not in (None, "", 0) else None
                except (TypeError, ValueError):
                    raise ValueError("api_id son bo'lishi kerak")
            elif k == "api_hash":
                v = v.lower() if v else None
                if v and (len(v) != 32 or any(c not in "0123456789abcdef" for c in v)):
                    raise ValueError("api_hash 32 belgili hex bo'lishi kerak")
            elif k in ("lang_code", "system_lang_code") and v and not _LANG_RE.match(v):
                raise ValueError(f"{k} noto'g'ri (masalan: en, ru, en-US)")
            elif k in DEFAULT_DEVICE and not v:
                v = DEFAULT_DEVICE[k]
            elif k == "note":
                v = (v or "")[:1000] or None
            clean[k] = v
        api_id = clean.get("api_id", acc.get("api_id"))
        api_hash = clean.get("api_hash", acc.get("api_hash"))
        if bool(api_id) != bool(api_hash):
            raise ValueError("api_id va api_hash birga berilishi (yoki ikkalasi bo'sh) kerak")
        await self._update(phone, **clean)
        if CONN_FIELDS & {k for k in clean if clean[k] != acc.get(k)}:
            await self.disconnect(phone)            # keyingi amal yangi sozlama bilan ulanadi
        return self.public(await self.get_account(phone))

    async def enable(self, phone: str) -> bool:
        """Nofaol akkauntni qayta yoqib ulashga urinadi."""
        self._require_config()
        phone = normalize_phone(phone)
        acc = await self.get_account(phone)
        if not acc:
            raise AccountError(404, "Akkaunt topilmadi")
        await self._update(phone, active=1, last_error=None, flood_until=None)
        try:
            await self._open({**acc, "active": 1})
        except AccountError as e:
            if e.status == 503:
                await self._update(phone, last_error=str(e))
            return False
        return True

    async def remove(self, phone: str, logout: bool = False) -> bool:
        """logout=True — sessiya Telegram'da ham bekor qilinadi (qaytarib bo'lmaydi)."""
        phone = normalize_phone(phone)
        client = self.clients.pop(phone, None)
        if client:
            try:
                if logout:
                    await client.log_out()
                else:
                    await client.disconnect()
            except Exception as e:
                print(f"[!] {phone}: uzishda xato ({e!r})")
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("DELETE FROM accounts WHERE phone=?", (phone,))
            await db.commit()
            return cur.rowcount > 0

    async def phones(self, only_active: bool = True, tag: str | None = None) -> list[str]:
        q = "SELECT phone, tags FROM accounts" + (" WHERE active=1" if only_active else "") + " ORDER BY added_at"
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(q) as cur:
                rows = await cur.fetchall()
        return [p for p, t in rows if not tag or tag in (t or "").split(",")]

    async def run_on_all(
        self,
        action: str,
        func: Callable[[TelegramClient], Awaitable],
        delay: float = 5.0,
        tag: str | None = None,
    ) -> dict[str, object]:
        """func(client) ni har bir faol akkauntda navbat bilan (run() orqali) bajaradi."""
        results = {}
        for phone in await self.phones(tag=tag):
            try:
                results[phone] = await self.run(phone, action, func)
            except FloodWaitError as e:
                results[phone] = f"FloodWait: {e.seconds}s"
            except Exception as e:
                results[phone] = f"{type(e).__name__}: {e}"
            await asyncio.sleep(delay)
        return results

    # ---------- Bot Start (referral) ----------
    @staticmethod
    def _parse_bot_link(raw: str) -> tuple[str, str]:
        """
        Bot start linkini parse qiladi. Qabul qiladi:
          https://t.me/BotName?start=ref123, t.me/BotName?start=ref123,
          @BotName ref123, BotName ref123, BotName (start_param yo'q)
        Qaytaradi: (bot_username, start_param)
        """
        s = raw.strip()
        if not s:
            raise ValueError("Bot username kiritilmagan")
        start_param = ""
        # t.me havolalarni parse qilish
        for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
            if s.lower().startswith(prefix):
                s = s[len(prefix):]
                break
        # ?start=code ajratish
        if "?start=" in s:
            parts = s.split("?start=", 1)
            s = parts[0]
            start_param = parts[1].split("&")[0]  # boshqa parametrlarni olib tashlash
        elif "?startapp=" in s:
            parts = s.split("?startapp=", 1)
            s = parts[0]
            start_param = parts[1].split("&")[0]
        elif " " in s:
            # "@BotName ref123" formati
            parts = s.split(None, 1)
            s = parts[0]
            start_param = parts[1]
        s = s.lstrip("@").rstrip("/")
        if not s:
            raise ValueError("Bot username kiritilmagan")
        return s, start_param

    async def bot_start(self, phone: str, bot: str, start_param: str = "") -> dict:
        """Bitta akkauntdan botga /start [param] yuboradi."""
        username, parsed_param = self._parse_bot_link(bot)
        param = start_param or parsed_param
        message = f"/start {param}" if param else "/start"

        async def fn(client):
            entity = await client.get_entity(username)
            msg = await client.send_message(entity, message)
            # Javobni kutish (2s)
            await asyncio.sleep(2)
            return {"status": "started", "bot": username, "param": param, "message_id": msg.id}
        return await self.run(phone, "bot_start", fn, limited=True)

    async def start_bot_start(
        self, bot: str, start_param: str = "", count: int = 0,
        tag: str | None = None, phones: list[str] | None = None,
    ) -> dict:
        """Fon ishi: N ta akkauntdan botga /start yuborish."""
        username, parsed_param = self._parse_bot_link(bot)
        param = start_param or parsed_param
        if phones is None:
            all_phones = await self.phones(tag=tag)
        else:
            all_phones = list(dict.fromkeys(normalize_phone(p) for p in phones))
        if count and count < len(all_phones):
            all_phones = random.sample(all_phones, count)

        async def one(phone):
            await self.bot_start(phone, username, param)
            return "ok"
        title = f"Bot start: @{username}" + (f" ({param})" if param else "")
        return await self.start_job("bot_start", one, phones=all_phones, delay=8.0, title=title)

    # ---------- AI Bot Agent (GPT-4o) ----------
    _AI_SYSTEM_PROMPT = """Sen Telegram bot bilan muloqot qiluvchi AI agentsan.
Bot xabarini tahlil qilib, JSON formatda qaror qaytarasan.

VAZIFALARING:
1. Bot matnidagi t.me/kanal havolalarni top — ularga obuna bo'lish kerak
2. Inline tugmalardan "Tekshirish/Check/Verify" turini aniqlash
3. Bot savol bersa — mantiqiy javob tayyorlash (ism, raqam, matematik, captcha)
4. Bot nima qilishni so'rayotganini tushunish

Har doim FAQAT JSON qaytarasan, boshqa hech narsa yozma:
{
  "channels": ["kanal1", "kanal2"],
  "click_button": "Tugma matni yoki null",
  "reply_text": "Javob matni yoki null",
  "done": true/false,
  "reasoning": "Qisqacha tushuntirish"
}

QOIDALAR:
- channels: faqat t.me/ dan keyin kelgan username'lar (bot o'zini qo'shma)
- click_button: "Tekshirish", "Check", "Verify" kabi tugma matni (URL tugmalarni bosma)
- reply_text: bot savol berganda javob (captcha, matematik, ism kiritish)
- done: true agar bot muvaffaqiyatli javob bergan bo'lsa (bonus, tabrik, "tayyor")
- Agar bot hech narsa so'ramasa done=true qil"""

    async def _ai_decide(self, bot_username: str, bot_text: str, buttons_text: str, history: list[str]) -> dict:
        """GPT-4o dan bot xabariga qaror so'raydi."""
        import openai
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY .env da o'rnatilmagan")

        client = openai.AsyncOpenAI(api_key=api_key)
        user_msg = f"""Bot: @{bot_username}
Bot xabari: {bot_text[:1000]}
Tugmalar: {buttons_text or 'yo\'q'}
Oldingi qadamlar: {'; '.join(history[-5:]) if history else 'birinchi qadam'}"""

        try:
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": self._AI_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.1,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            import json
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            return {"channels": [], "click_button": None, "reply_text": None, "done": False,
                    "reasoning": f"AI xato: {e}"}

    async def ai_bot_agent(self, phone: str, bot: str, start_param: str = "", max_steps: int = 12) -> dict:
        """
        AI bilan ishlagan universal bot agent:
        1. /start yuboradi
        2. GPT-4o bot javobini tahlil qiladi
        3. AI qaroriga ko'ra: kanal obuna, tugma bosish, javob yozish
        4. Qayta tekshiradi — to 'liq avtomatik
        """
        from telethon.tl.functions.channels import JoinChannelRequest
        username, parsed_param = self._parse_bot_link(bot)
        param = start_param or parsed_param
        message = f"/start {param}" if param else "/start"

        result = {
            "status": "ok", "bot": username, "param": param,
            "channels_joined": [], "buttons_clicked": [], "replies_sent": [],
            "ref_link": None, "steps": [], "errors": [], "ai_decisions": [],
        }

        async def fn(client):
            me = await client.get_me()
            my_id = me.id

            # 1. /start yuborish
            result["steps"].append(f"→ {message}")
            await client.send_message(username, message)
            await asyncio.sleep(3)

            for step_i in range(max_steps):
                # Bot javobini o'qish
                msgs = await client.get_messages(username, limit=5)
                bot_msg = None
                for m in msgs:
                    if m.sender_id != my_id:
                        bot_msg = m
                        break
                if not bot_msg:
                    result["steps"].append("⏳ Bot javob bermadi")
                    break

                text = bot_msg.raw_text or ""
                result["steps"].append(f"← {text[:200]}")

                # Tugmalar matnini yig'ish
                buttons_info = []
                if bot_msg.buttons:
                    for row in bot_msg.buttons:
                        for btn in row:
                            btype = "url" if btn.url else "callback"
                            buttons_info.append(f"[{btype}] {btn.text}")

                # 2. AI dan qaror olish
                decision = await self._ai_decide(
                    username, text, " | ".join(buttons_info), result["steps"]
                )
                result["ai_decisions"].append(decision)
                result["steps"].append(f"🧠 AI: {decision.get('reasoning', '')[:100]}")

                actions_done = 0

                # 3. Kanallarga obuna
                for ch in decision.get("channels", []):
                    if ch in result["channels_joined"] or ch.lower() == username.lower():
                        continue
                    try:
                        await client(JoinChannelRequest(ch))
                        result["channels_joined"].append(ch)
                        result["steps"].append(f"📢 @{ch} ga obuna bo'ldi")
                        actions_done += 1
                        await asyncio.sleep(2)
                    except Exception as e:
                        result["errors"].append(f"@{ch}: {e}")

                # Ref link tekshirish
                ref = self._extract_ref_link(text, username)
                if ref:
                    result["ref_link"] = f"https://t.me/{username}?start={ref}"

                # 4. Tugma bosish
                btn_to_click = decision.get("click_button")
                if btn_to_click and bot_msg.buttons:
                    for i, row in enumerate(bot_msg.buttons):
                        for j, btn in enumerate(row):
                            if btn.text and btn_to_click.lower() in btn.text.lower() and not btn.url:
                                try:
                                    await bot_msg.click(i, j)
                                    result["buttons_clicked"].append(btn.text)
                                    result["steps"].append(f"🔘 «{btn.text}» bosildi")
                                    actions_done += 1
                                    await asyncio.sleep(3)
                                except Exception as e:
                                    result["errors"].append(f"Tugma: {e}")
                                break
                        if actions_done:
                            break

                # 5. Javob yozish
                reply = decision.get("reply_text")
                if reply:
                    await client.send_message(username, reply)
                    result["replies_sent"].append(reply)
                    result["steps"].append(f"→ {reply}")
                    actions_done += 1
                    await asyncio.sleep(3)

                # 6. Tugadimi?
                if decision.get("done") and actions_done == 0:
                    result["steps"].append("✅ AI: tugatildi")
                    break

                if actions_done == 0 and not decision.get("channels") and not btn_to_click and not reply:
                    result["steps"].append("✅ Boshqa vazifa yo'q")
                    break

                await asyncio.sleep(2)

            return result

        return await self.run(phone, "ai_bot_agent", fn, limited=True)

    async def start_ai_bot_agent(
        self, bot: str, start_param: str = "", count: int = 0,
        tag: str | None = None, phones: list[str] | None = None,
    ) -> dict:
        """Fon ishi: N ta akkauntda AI bot agent."""
        username, parsed_param = self._parse_bot_link(bot)
        param = start_param or parsed_param
        if phones is None:
            all_phones = await self.phones(tag=tag)
        else:
            all_phones = list(dict.fromkeys(normalize_phone(p) for p in phones))
        if count and count < len(all_phones):
            all_phones = random.sample(all_phones, count)

        async def one(phone):
            r = await self.ai_bot_agent(phone, username, param)
            ch = len(r.get("channels_joined", []))
            btn = len(r.get("buttons_clicked", []))
            rep = len(r.get("replies_sent", []))
            return f"ok: {ch} kanal, {btn} tugma, {rep} javob"
        title = f"AI Agent: @{username}" + (f" ({param})" if param else "")
        return await self.start_job("ai_bot_agent", one, phones=all_phones, delay=12.0, title=title)

    # ---------- Bot Autopilot ----------
    # "Tekshirish" / "Verify" / "Check" tugmalarini aniqlash uchun kalit so'zlar
    _VERIFY_WORDS = {
        "tekshirish", "tekshir", "check", "verify", "проверить", "проверка",
        "✅", "obuna bo'ldim", "подписался", "joined", "done", "davom",
        "continue", "продолжить", "boshlash", "start", "получить", "olish",
        "подтвердить", "confirm", "готово", "tayyor",
    }
    _CHANNEL_RE = re.compile(r"(?:https?://)?t\.me/(?:\+)?([a-zA-Z0-9_]+)", re.IGNORECASE)

    @classmethod
    def _is_verify_button(cls, text: str) -> bool:
        """Tugma matni 'Tekshirish' yoki shungao'xshash ekanini aniqlaydi."""
        low = text.lower().strip()
        return any(w in low for w in cls._VERIFY_WORDS)

    @classmethod
    def _extract_channels(cls, text: str, bot_username: str = "") -> list[str]:
        """Matndan t.me/channel havolalarni topadi (botning o'zini chiqarib tashlaydi)."""
        found = cls._CHANNEL_RE.findall(text)
        bot_low = bot_username.lower()
        # joinchat, addstickers va botni filtrlash
        skip = {"joinchat", "addstickers", "addtheme", "proxy", "socks", bot_low}
        return list(dict.fromkeys(ch for ch in found if ch.lower() not in skip and not ch.startswith("+")))

    @classmethod
    def _extract_ref_link(cls, text: str, bot_username: str) -> str | None:
        """Bot javobidan foydalanuvchining ref havolasini topadi."""
        pattern = re.compile(
            rf"(?:https?://)?t\.me/{re.escape(bot_username)}\?start=([a-zA-Z0-9_-]+)",
            re.IGNORECASE,
        )
        m = pattern.search(text)
        return m.group(1) if m else None

    async def bot_autopilot(self, phone: str, bot: str, start_param: str = "", max_steps: int = 8) -> dict:
        """
        Bot bilan to'liq avtomatik ishlash:
        1. /start [param] yuboradi
        2. Javobdagi t.me/channel havolalarni topib obuna bo'ladi
        3. Inline button'lardan "Tekshirish" ni topib bosadi
        4. Keyingi javobni o'qiydi, yana kanal bo'lsa obuna bo'ladi
        5. Ref linkni topsa qaytaradi

        Qaytaradi: {status, bot, channels_joined, buttons_clicked, ref_link, steps}
        """
        from telethon.tl.functions.channels import JoinChannelRequest
        username, parsed_param = self._parse_bot_link(bot)
        param = start_param or parsed_param
        message = f"/start {param}" if param else "/start"

        result = {
            "status": "ok", "bot": username, "param": param,
            "channels_joined": [], "buttons_clicked": [],
            "ref_link": None, "steps": [], "errors": [],
        }

        async def fn(client):
            # 1. /start yuborish
            result["steps"].append(f"→ {message}")
            msg = await client.send_message(username, message)
            await asyncio.sleep(3)

            for step in range(max_steps):
                # Bot javobini o'qish
                msgs = await client.get_messages(username, limit=3)
                bot_msg = None
                for m in msgs:
                    if m.sender_id != (await client.get_me()).id:
                        bot_msg = m
                        break
                if not bot_msg:
                    result["steps"].append("⏳ Bot javob bermadi")
                    break

                text = bot_msg.raw_text or ""
                result["steps"].append(f"← {text[:150]}")

                # 2. Kanalllarni topish va obuna bo'lish
                channels = self._extract_channels(text, username)
                # Inline button URL'lardan ham kanallarni topish
                if bot_msg.buttons:
                    for row in bot_msg.buttons:
                        for btn in row:
                            if btn.url:
                                btn_channels = self._extract_channels(btn.url, username)
                                channels.extend(btn_channels)
                channels = list(dict.fromkeys(channels))  # dublikatlarni olib tashlash

                if channels:
                    for ch in channels:
                        if ch in result["channels_joined"]:
                            continue
                        try:
                            await client(JoinChannelRequest(ch))
                            result["channels_joined"].append(ch)
                            result["steps"].append(f"📢 @{ch} ga obuna bo'ldi")
                            await asyncio.sleep(2)
                        except Exception as e:
                            err = f"@{ch}: {type(e).__name__}"
                            result["errors"].append(err)
                            result["steps"].append(f"⚠️ {err}")

                # 3. Ref linkni topish
                ref = self._extract_ref_link(text, username)
                if ref:
                    result["ref_link"] = f"https://t.me/{username}?start={ref}"
                    result["steps"].append(f"🔗 Ref: {result['ref_link']}")

                # 4. "Tekshirish" tugmasini topib bosish
                clicked = False
                if bot_msg.buttons:
                    for i, row in enumerate(bot_msg.buttons):
                        for j, btn in enumerate(row):
                            btn_text = btn.text or ""
                            if self._is_verify_button(btn_text) and not btn.url:
                                try:
                                    await bot_msg.click(i, j)
                                    result["buttons_clicked"].append(btn_text)
                                    result["steps"].append(f"🔘 «{btn_text}» bosildi")
                                    clicked = True
                                    await asyncio.sleep(3)
                                except Exception as e:
                                    result["errors"].append(f"Tugma: {e}")
                                break
                        if clicked:
                            break

                # Agar na kanal bor, na tugma — tugatamiz
                if not channels and not clicked:
                    # Oxirgi tekshiruv: boshqa inline buttonlar bormi?
                    has_non_url_buttons = False
                    if bot_msg.buttons:
                        for row in bot_msg.buttons:
                            for btn in row:
                                if not btn.url and btn.text:
                                    has_non_url_buttons = True
                    if not has_non_url_buttons:
                        result["steps"].append("✅ Tugatildi — boshqa vazifa yo'q")
                        break

                await asyncio.sleep(2)

            return result

        return await self.run(phone, "bot_autopilot", fn, limited=True)

    async def start_bot_autopilot(
        self, bot: str, start_param: str = "", count: int = 0,
        tag: str | None = None, phones: list[str] | None = None,
    ) -> dict:
        """Fon ishi: N ta akkauntda bot autopilot."""
        username, parsed_param = self._parse_bot_link(bot)
        param = start_param or parsed_param
        if phones is None:
            all_phones = await self.phones(tag=tag)
        else:
            all_phones = list(dict.fromkeys(normalize_phone(p) for p in phones))
        if count and count < len(all_phones):
            all_phones = random.sample(all_phones, count)

        async def one(phone):
            r = await self.bot_autopilot(phone, username, param)
            ch = len(r.get("channels_joined", []))
            btn = len(r.get("buttons_clicked", []))
            return f"ok: {ch} kanal, {btn} tugma"
        title = f"Autopilot: @{username}" + (f" ({param})" if param else "")
        return await self.start_job("bot_autopilot", one, phones=all_phones, delay=10.0, title=title)

    async def start_ref_chain(
        self, bot: str, start_param: str = "", count: int = 0,
        tag: str | None = None, phones: list[str] | None = None,
    ) -> dict:
        """
        Referral zanjiri: har akkaunt autopilot bilan kiradi,
        ref linkni oladi va keyingi akkauntga uzatadi.
        1-akk: /start [boshlang'ich_param] → ref_link oladi
        2-akk: /start [1-akk_ref] → ref_link oladi
        3-akk: /start [2-akk_ref] → ...
        """
        username, parsed_param = self._parse_bot_link(bot)
        first_param = start_param or parsed_param
        if phones is None:
            all_phones = await self.phones(tag=tag)
        else:
            all_phones = list(dict.fromkeys(normalize_phone(p) for p in phones))
        if count and count < len(all_phones):
            all_phones = all_phones[:count]  # zanjir uchun tartib muhim, random emas
        if not all_phones:
            raise AccountError(400, "Tanlangan faol akkaunt yo'q")

        chain_state = {"current_param": first_param, "results": []}

        async def one(phone):
            param = chain_state["current_param"]
            r = await self.bot_autopilot(phone, username, param)
            ch = len(r.get("channels_joined", []))
            btn = len(r.get("buttons_clicked", []))
            ref = r.get("ref_link")
            summary = f"ok: {ch} kanal, {btn} tugma"
            if ref:
                # Keyingi akkaunt uchun ref param ni yangilash
                new_param = ref.split("?start=")[-1] if "?start=" in ref else ""
                if new_param:
                    chain_state["current_param"] = new_param
                    summary += f", ref→{new_param[:20]}"
            chain_state["results"].append({"phone": phone, "ref": ref, "channels": ch, "buttons": btn})
            return summary

        title = f"Ref zanjir: @{username}" + (f" ({first_param})" if first_param else "")
        return await self.start_job("ref_chain", one, phones=all_phones, delay=10.0, title=title)

    # ---------- Kanalga obuna / Reaksiya / Warm-up ----------
    @staticmethod
    def _parse_channel(raw: str) -> tuple[str, int | None]:
        """
        Kanal kiritmasini parse qiladi. Qabul qiladi:
          @username, username, https://t.me/username, https://t.me/username/123,
          t.me/username, t.me/+invite_hash
        Qaytaradi: (username_yoki_invite, msg_id_yoki_None)
        """
        s = raw.strip()
        if not s:
            raise ValueError("Kanal username kiritilmagan")
        msg_id = None
        # t.me havolalarni parse qilish
        for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
            if s.lower().startswith(prefix):
                s = s[len(prefix):]
                break
        # @ olib tashlash
        s = s.lstrip("@")
        # username/123 formatini ajratish (msg_id)
        if "/" in s:
            parts = s.rsplit("/", 1)
            if parts[1].isdigit():
                s, msg_id = parts[0], int(parts[1])
            else:
                s = parts[0]  # username/qo'shimcha -> faqat username
        if not s:
            raise ValueError("Kanal username kiritilmagan")
        return s, msg_id

    async def join_channel(self, phone: str, channel: str) -> dict:
        """Bitta akkauntni kanalga obuna qiladi."""
        from telethon.tl.functions.channels import JoinChannelRequest
        username, _ = self._parse_channel(channel)

        async def fn(client):
            await client(JoinChannelRequest(username))
            return {"status": "joined", "channel": username}
        return await self.run(phone, "join_channel", fn, limited=True)

    async def start_join_channel(
        self, channel: str, count: int = 0, tag: str | None = None, phones: list[str] | None = None,
    ) -> dict:
        """Fon ishi: N ta akkauntni kanalga obuna qilish."""
        username, _ = self._parse_channel(channel)
        if phones is None:
            all_phones = await self.phones(tag=tag)
        else:
            all_phones = list(dict.fromkeys(normalize_phone(p) for p in phones))
        if count and count < len(all_phones):
            all_phones = random.sample(all_phones, count)

        async def one(phone):
            await self.join_channel(phone, username)
            return "ok"
        return await self.start_job("join_channel", one, phones=all_phones, delay=8.0,
                                    title=f"Kanalga obuna: @{username}")

    async def send_reaction(self, phone: str, channel: str, emoji: str = "👍") -> dict:
        """Kanal/guruhning so'ngi postiga yoki berilgan postga reaksiya qo'yadi."""
        from telethon.tl.functions.messages import GetHistoryRequest, SendReactionRequest
        from telethon.tl.types import ReactionEmoji
        username, fixed_msg_id = self._parse_channel(channel)

        async def fn(client):
            entity = await client.get_entity(username)
            if fixed_msg_id:
                target_msg_id = fixed_msg_id
            else:
                history = await client(GetHistoryRequest(
                    peer=entity, limit=1, offset_date=None, offset_id=0,
                    max_id=0, min_id=0, add_offset=0, hash=0,
                ))
                if not history.messages:
                    raise ValueError("Kanalda post topilmadi")
                target_msg_id = history.messages[0].id
            await client(SendReactionRequest(
                peer=entity, msg_id=target_msg_id,
                reaction=[ReactionEmoji(emoticon=emoji)],
            ))
            return {"status": "reacted", "channel": username, "emoji": emoji, "msg_id": target_msg_id}
        return await self.run(phone, "send_reaction", fn, limited=True)

    async def start_send_reaction(
        self, channel: str, emoji: str = "👍", count: int = 0,
        tag: str | None = None, phones: list[str] | None = None,
    ) -> dict:
        """Fon ishi: N ta akkauntdan so'ngi postga reaksiya."""
        username, _ = self._parse_channel(channel)
        if phones is None:
            all_phones = await self.phones(tag=tag)
        else:
            all_phones = list(dict.fromkeys(normalize_phone(p) for p in phones))
        if count and count < len(all_phones):
            all_phones = random.sample(all_phones, count)

        async def one(phone):
            await self.send_reaction(phone, username, emoji)
            return "ok"
        return await self.start_job("send_reaction", one, phones=all_phones, delay=8.0,
                                    title=f"Reaksiya: @{username} {emoji}")

    async def warmup(self, phone: str) -> dict:
        """Sessiyani tirik tutish: get_me + dialoglar o'qish."""
        async def fn(client):
            me = await client.get_me()
            dialogs = await client.get_dialogs(limit=3)
            return {"status": "ok", "user_id": me.id, "dialogs": len(dialogs)}
        return await self.run(phone, "warmup", fn)

    async def start_warmup(self, tag: str | None = None, phones: list[str] | None = None) -> dict:
        """Fon ishi: barcha akkauntlarda warm-up."""
        async def one(phone):
            await self.warmup(phone)
            return "ok"
        return await self.start_job("warmup", one, phones=phones, tag=tag, delay=10.0,
                                    title="Warm-up")

    async def randomize_device(self, phone: str) -> dict:
        """Akkauntga tasodifiy haqiqiy qurilma profili qo'yadi."""
        phone = normalize_phone(phone)
        acc = await self.get_account(phone)
        if not acc:
            raise AccountError(404, "Akkaunt topilmadi")
        dev = generate_device()
        await self._update(phone, **dev)
        await self.disconnect(phone)  # keyingi amal yangi qurilma bilan ulanadi
        await self.log(phone, "randomize_device", True, dev["device_model"])
        return dev

    # ---------- SpamBot ----------
    async def spam_check(self, phone: str) -> dict:
        async def fn(client):
            async with client.conversation("SpamBot", timeout=20) as conv:
                await conv.send_message("/start")
                return (await conv.get_response()).raw_text or ""

        phone = normalize_phone(phone)
        prev = (await self.get_account(phone) or {}).get("spam_status")
        text = await self.run(phone, "spam_check", fn)
        status = classify_spambot(text)
        await self._update(phone, spam_status=status, spam_text=text[:1000], spam_checked_at=int(time.time()))
        if status == "limited" and prev != "limited":
            await self.emit("spam_limited", phone=phone, text=text)
        return {"phone": phone, "status": status, "text": text}

    async def start_job(
        self, name: str, per_phone: Callable[[str], Awaitable[Any]], *,
        phones: list[str] | None = None, tag: str | None = None, delay: float = 5.0, title: str | None = None,
    ) -> dict:
        """
        Fon ishi: per_phone(phone) ni har akkauntda navbat bilan bajaradi (bir vaqtda bitta ish).
        Holatini self.job dan (GET /jobs) kuzatish mumkin; tugaganda "job_done" hodisasi.
        """
        self._require_config()
        if self.job.get("running"):
            raise AccountError(409, f"Boshqa fon ishi ketmoqda: {self.job.get('title')}")
        if phones is None:
            phones = await self.phones(tag=tag)
        else:
            phones = list(dict.fromkeys(normalize_phone(p) for p in phones))
        if not phones:
            raise AccountError(400, "Tanlangan faol akkaunt yo'q")
        self.job = {"name": name, "title": title or name, "running": True, "done": 0, "total": len(phones),
                    "current": None, "results": {}, "started_at": int(time.time())}
        job = self.job

        async def worker():
            try:
                for i, phone in enumerate(phones):
                    job["current"] = phone
                    try:
                        r = await per_phone(phone)
                        job["results"][phone] = r if isinstance(r, str) else "ok"
                    except FloodWaitError as e:
                        job["results"][phone] = f"FloodWait {e.seconds}s"
                    except Exception as e:
                        job["results"][phone] = f"xato: {e}"
                    job["done"] = i + 1
                    if i + 1 < len(phones):
                        await asyncio.sleep(delay)
            except Exception as e:
                job["error"] = repr(e)
            finally:
                job.update(running=False, current=None, finished_at=int(time.time()))
                await self.emit("job_done", job=dict(job))

        self._job_task = asyncio.create_task(worker())
        return job

    async def start_spam_check_all(self, tag: str | None = None, phones: list[str] | None = None) -> dict:
        async def one(phone):
            return (await self.spam_check(phone))["status"]
        return await self.start_job("spam_check", one, phones=phones, tag=tag, delay=8.0, title="SpamBot tekshiruvi")

    # ---------- Kirgan qurilmalar nazorati ----------
    async def check_new_auths(self, phone: str, auths: list[dict]) -> list[dict]:
        """
        Oldin ko'rilmagan qurilmalarni qaytaradi va eslab qoladi.
        Akkaunt uchun birinchi tekshiruv — faqat eslab qolinadi (hammasi "yangi" deb chiqmasin).
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT hash FROM known_auths WHERE phone=?", (phone,)) as cur:
                known = {r[0] for r in await cur.fetchall()}
            first_time = not known
            new = [a for a in auths if a["hash"] not in known]
            now = int(time.time())
            for a in new:
                await db.execute("INSERT OR IGNORE INTO known_auths VALUES (?, ?, ?, ?)",
                                 (phone, a["hash"], f"{a['device']} {a['platform']}", now))
            await db.commit()
        fresh = [] if first_time else [a for a in new if not a.get("current")]
        for a in fresh:
            await self.emit("new_auth", phone=phone, auth=a)
        return fresh

    # ---------- Statistika ----------
    async def snapshot_stats(self):
        """Bugungi holatni stats jadvaliga yozadi (kun davomida yangilanib boradi)."""
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT COUNT(*), SUM(active=1), SUM(active=0), SUM(spam_status='limited'),
                       SUM(COALESCE(flood_until, 0) > ?) FROM accounts
            """, (now,)) as cur:
                total, active, dead, limited, flood = await cur.fetchone()
            await db.execute(
                "INSERT OR REPLACE INTO stats VALUES (?, ?, ?, ?, ?, ?)",
                (time.strftime("%Y-%m-%d"), total or 0, active or 0, dead or 0, limited or 0, flood or 0),
            )
            await db.commit()

    async def get_stats(self, days: int = 14) -> list[dict]:
        since = self._day_start() - (days - 1) * 86400
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM stats WHERE day >= ? ORDER BY day",
                                  (time.strftime("%Y-%m-%d", time.localtime(since)),)) as cur:
                snaps = {r[0]: r[1:] for r in await cur.fetchall()}
            async with db.execute("""
                SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') d, SUM(ok=1), SUM(ok=0)
                FROM actions WHERE ts >= ? GROUP BY d
            """, (since,)) as cur:
                acts = {r[0]: (r[1] or 0, r[2] or 0) for r in await cur.fetchall()}
        out = []
        for i in range(days):
            d = time.strftime("%Y-%m-%d", time.localtime(since + i * 86400 + 3600))
            snap = snaps.get(d)
            ok, fail = acts.get(d, (0, 0))
            out.append({
                "day": d, "actions_ok": ok, "actions_fail": fail,
                **(dict(zip(("total", "active", "dead", "limited", "flood"), snap)) if snap else {}),
            })
        return out

    # ---------- Zaxira ----------
    async def export_zip(self, path: str) -> int:
        """Barcha akkauntlar: <raqam>.session (Telethon) + <raqam>.json + strings.txt."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM accounts ORDER BY added_at") as cur:
                rows = [dec_row(dict(r)) for r in await cur.fetchall()]
        tmp = path + ".d"
        os.makedirs(tmp, exist_ok=True)
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                lines = []
                for acc in rows:
                    stem = acc["phone"].lstrip("+")
                    sess_path = os.path.join(tmp, stem + ".session")
                    string_to_session_file(acc["session"], sess_path)
                    zf.write(sess_path, stem + ".session")
                    meta = {
                        "phone": stem, "user_id": acc["user_id"], "username": acc["username"],
                        "first_name": acc["first_name"],
                        "app_id": acc["api_id"] or self.api_id, "app_hash": acc["api_hash"] or self.api_hash,
                        "device": acc["device_model"], "sdk": acc["system_version"],
                        "app_version": acc["app_version"], "lang_code": acc["lang_code"],
                        "system_lang_code": acc["system_lang_code"], "twoFA": acc["twofa"],
                        "proxy": acc["proxy"], "tags": acc["tags"], "note": acc["note"],
                        "active": bool(acc["active"]), "last_error": acc["last_error"],
                    }
                    zf.writestr(stem + ".json", json.dumps(meta, ensure_ascii=False, indent=2))
                    lines.append(f"{acc['phone']}\t{acc['session']}")
                zf.writestr("strings.txt", "\n".join(lines) + "\n")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return len(rows)

    async def shutdown(self):
        await self._disconnect_all()
        for client, _, _ in list(self.pending.values()):
            await client.disconnect()
        self.pending.clear()


# ---------- Test (konsol orqali) ----------
async def main():
    m = AccountManager()
    m._require_config()
    await m.init()

    if input("Yangi akkaunt qo'shasizmi? (y/n): ").lower() == "y":
        phone = await m.send_code(input("Telefon (+998...): "))
        res = await m.confirm_code(phone, input("Kod: "))
        if res["status"] == "password_needed":
            res = await m.confirm_password(phone, input("2FA parol: "))
        print(res)

    async def whoami(client: TelegramClient):
        me = await client.get_me()
        return f"{me.first_name} (@{me.username})"

    print(await m.run_on_all("whoami", whoami))
    await m.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
