"""
FastAPI qatlami — account_manager.py ustida.

venv/bin/uvicorn api:app --host 127.0.0.1 --port 8010

Veb panel: http://SERVER:8010/
Swagger:   http://SERVER:8010/docs  ("Authorize" tugmasiga API kalitni kiriting)
Har bir so'rovda header: X-API-Key: <API_KEY>
"""
import asyncio
import io
import os
import secrets
import tempfile
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, Security, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from starlette.background import BackgroundTask
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    GetPasswordRequest,
    ResetAuthorizationRequest,
    UpdateProfileRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.photos import UploadProfilePhotoRequest

from account_manager import (  # .env shu yerda o'qiladi
    BASE_DIR,
    OPTION_DEFAULTS,
    SESSIONS_DIR,
    AccountError,
    AccountManager,
    NotConfiguredError,
    extract_sessions_from_zip,
    generate_device,
    normalize_phone,
)

API_KEY = os.getenv("API_KEY", "")
if len(API_KEY) < 16:
    raise SystemExit("API_KEY .env da o'rnatilmagan yoki juda qisqa (kamida 16 belgi)")

MAX_UPLOAD = 2 * 1024 * 1024        # bitta .session / .json fayl uchun
MAX_ZIP_UPLOAD = 100 * 1024 * 1024  # bitta .zip uchun
MAX_AVATAR = 5 * 1024 * 1024

manager = AccountManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await manager.init()
    tasks = [
        asyncio.create_task(manager.watch_folder(SESSIONS_DIR)),   # sessions/ ni kuzatadi
        asyncio.create_task(manager.maintenance()),                # bo'sh ulanishlarni uzish, qayta ulash
    ]
    yield
    for t in tasks:
        t.cancel()
    await manager.shutdown()


app = FastAPI(title="Telegram Account Manager", lifespan=lifespan)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def auth(key: str | None = Security(api_key_header)):
    if not key or not secrets.compare_digest(key, API_KEY):
        raise HTTPException(401, "API key noto'g'ri")


router = APIRouter(dependencies=[Depends(auth)])


# ---------- Xatolarni HTTP javobga aylantirish ----------
@app.exception_handler(AccountError)
async def _account(request: Request, e: AccountError):
    return JSONResponse({"detail": str(e)}, status_code=e.status)


@app.exception_handler(NotConfiguredError)
async def _not_configured(request: Request, e: NotConfiguredError):
    return JSONResponse({"detail": str(e)}, status_code=503)


@app.exception_handler(FloodWaitError)
async def _flood(request: Request, e: FloodWaitError):
    return JSONResponse(
        {"detail": f"FloodWait: {e.seconds}s kuting"}, status_code=429,
        headers={"Retry-After": str(e.seconds)},
    )


@app.exception_handler(RPCError)
async def _rpc(request: Request, e: RPCError):
    return JSONResponse({"detail": f"Telegram xatosi: {type(e).__name__}: {e}"}, status_code=400)


@app.exception_handler(ValueError)          # LoginError ham shu yerga tushadi
async def _value(request: Request, e: ValueError):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(asyncio.TimeoutError)
async def _timeout(request: Request, e: asyncio.TimeoutError):
    return JSONResponse({"detail": "Telegram javob bermadi (timeout)"}, status_code=504)


# ---------- Schemalar ----------
class PhoneIn(BaseModel):
    phone: str


class CodeIn(BaseModel):
    phone: str
    code: str


class PasswordIn(BaseModel):
    phone: str
    password: str


class SessionStringIn(BaseModel):
    session: str


class TelegramConfigIn(BaseModel):
    api_id: int
    api_hash: str


class OptionsIn(BaseModel):
    CONNECT_MODE: str | None = None
    IDLE_MINUTES: int | None = None
    DAILY_MSG_LIMIT: int | None = None
    ACTION_DELAY: int | None = None


class AccountPatch(BaseModel):
    tags: list[str] | str | None = None
    note: str | None = None
    proxy: str | None = None
    api_id: int | str | None = None
    api_hash: str | None = None
    device_model: str | None = None
    system_version: str | None = None
    app_version: str | None = None
    lang_code: str | None = None
    system_lang_code: str | None = None


class MessageIn(BaseModel):
    to: str          # @username, +telefon yoki chat id (-100...)
    text: str


class ProfileIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    about: str | None = None


class UsernameIn(BaseModel):
    username: str    # bo'sh — username olib tashlanadi


class TwoFAIn(BaseModel):
    current_password: str | None = None
    new_password: str
    hint: str = ""


class JoinChannelIn(BaseModel):
    channel: str       # @username yoki https://t.me/...
    count: int = 0     # 0 = barcha faol akkauntlar
    tag: str | None = None


class SendReactionIn(BaseModel):
    channel: str
    emoji: str = "👍"
    count: int = 0
    tag: str | None = None


class WarmupIn(BaseModel):
    tag: str | None = None


class BotStartIn(BaseModel):
    bot: str              # @BotName yoki https://t.me/BotName?start=ref123
    start_param: str = "" # ref kodi (link ichida bo'lsa shart emas)
    count: int = 0        # 0 = barcha faol akkauntlar
    tag: str | None = None


class BotAutopilotIn(BaseModel):
    bot: str              # @BotName yoki t.me/Bot?start=ref123
    start_param: str = "" # boshlang'ich param
    count: int = 0
    tag: str | None = None


class RefChainIn(BaseModel):
    bot: str              # @BotName yoki t.me/Bot?start=ref123
    start_param: str = "" # 1-akkaunt uchun boshlang'ich ref
    count: int = 0
    tag: str | None = None


class AIAgentIn(BaseModel):
    bot: str              # @BotName yoki t.me/Bot?start=ref
    start_param: str = ""
    count: int = 0
    tag: str | None = None


# ---------- Veb panel ----------
@app.get("/", include_in_schema=False)
async def web_panel():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@app.get("/health")
async def health():
    return {"ok": True, "configured": manager.configured, "connected": len(manager.clients)}


# ---------- Sozlamalar ----------
@router.get("/config")
async def get_config():
    return {
        "configured": manager.configured, "api_id": manager.api_id or None,
        "api_hash": manager.api_hash[:4] + "…" if manager.api_hash else None,
        "options": manager.options, "defaults": OPTION_DEFAULTS,
    }


@router.post("/config/telegram")
async def set_telegram_config(data: TelegramConfigIn):
    """my.telegram.org dagi api_id / api_hash ni o'rnatish (.env ga saqlanadi)."""
    await manager.configure(data.api_id, data.api_hash)
    return {"status": "ok"}


@router.post("/config/options")
async def set_options(data: OptionsIn):
    """Ulanish rejimi, bo'sh turish vaqti, kunlik limit, amallar orasidagi pauza."""
    await manager.set_options(data.model_dump(exclude_none=True))
    return {"status": "ok", "options": manager.options}


@router.get("/config/openai")
async def get_openai_config():
    """OpenAI API key o'rnatilganligini tekshirish."""
    key = os.getenv("OPENAI_API_KEY", "")
    return {"configured": bool(key), "key_hint": key[:8] + "…" if len(key) > 8 else ""}


@router.post("/config/openai")
async def set_openai_config(data: dict):
    """OpenAI API key ni .env ga saqlash."""
    key = data.get("api_key", "").strip()
    if not key:
        raise HTTPException(400, "API key kiritilmagan")
    env_path = os.path.join(BASE_DIR, ".env")
    # .env faylni o'qish
    lines = []
    found = False
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.strip().startswith("OPENAI_API_KEY="):
                    lines.append(f"OPENAI_API_KEY={key}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"OPENAI_API_KEY={key}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)
    # Runtime'da ham yangilash
    os.environ["OPENAI_API_KEY"] = key
    return {"status": "ok", "key_hint": key[:8] + "…"}


# ---------- Login ----------
@router.post("/accounts/send-code")
async def send_code(data: PhoneIn):
    phone = await manager.send_code(data.phone)
    return {"status": "code_sent", "phone": phone}


@router.post("/accounts/confirm-code")
async def confirm_code(data: CodeIn):
    res = await manager.confirm_code(data.phone, data.code)
    if res["status"] in ("invalid_code", "expired", "not_registered"):
        raise HTTPException(400, res["status"])
    return res          # status: "ok" yoki "password_needed"


@router.post("/accounts/confirm-password")
async def confirm_password(data: PasswordIn):
    return await manager.confirm_password(data.phone, data.password)


@router.post("/accounts/cancel-login")
async def cancel_login(data: PhoneIn):
    await manager.cancel_login(data.phone)
    return {"status": "cancelled"}


# ---------- Tayyor sessiyalarni import ----------
@router.post("/accounts/import-string")
async def import_string(data: SessionStringIn):
    return await manager.import_string(data.session)


@router.post("/accounts/import-files")
async def import_files(files: list[UploadFile] = File(...)):
    """.session (+ bir xil nomli .json) va/yoki .zip fayllarni birdaniga yuklash."""
    manager._require_config()
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        up_dir = os.path.join(tmp, "up")          # to'g'ridan-to'g'ri yuklangan .session/.json lar
        os.makedirs(up_dir)
        to_import: list[tuple[str, str]] = []     # (ko'rsatiladigan nom, yo'l)
        for f in files:
            name = os.path.basename(f.filename or "")
            low = name.lower()
            if low.endswith(".json"):
                content = await f.read()
                if len(content) <= MAX_UPLOAD:
                    with open(os.path.join(up_dir, name), "wb") as out:
                        out.write(content)
                continue
            is_zip = low.endswith(".zip")
            if not (name.endswith(".session") or is_zip):
                results.append({"file": name, "ok": False, "error": ".session, .json yoki .zip emas"})
                continue
            content = await f.read()
            if len(content) > (MAX_ZIP_UPLOAD if is_zip else MAX_UPLOAD):
                results.append({"file": name, "ok": False, "error": "Fayl juda katta"})
                continue
            if is_zip:
                zip_path = os.path.join(tmp, "upload.zip")
                with open(zip_path, "wb") as out:
                    out.write(content)
                unpack = tempfile.mkdtemp(dir=tmp)
                try:
                    paths = extract_sessions_from_zip(zip_path, unpack)
                except ValueError as e:
                    results.append({"file": name, "ok": False, "error": str(e)})
                    continue
                finally:
                    os.remove(zip_path)
                to_import += [(f"{name}/{os.path.basename(p)}", p) for p in paths]
            else:
                path = os.path.join(up_dir, name)
                with open(path, "wb") as out:
                    out.write(content)
                to_import.append((name, path))

        for label, path in to_import:
            try:
                results.append({"file": label, "ok": True, **await manager.import_session_file(path)})
            except NotConfiguredError:
                raise
            except FloodWaitError as e:
                results.append({"file": label, "ok": False, "error": f"FloodWait {e.seconds}s — keyinroq qayta yuklang"})
            except Exception as e:
                results.append({"file": label, "ok": False, "error": str(e) or type(e).__name__})
            await asyncio.sleep(0.5)                    # akkauntlar orasida pauza
    return results


@router.post("/accounts/scan")
async def scan_now():
    """sessions/ papkani darhol tekshirish (10 soniya kutmasdan)."""
    return await manager.scan_folder(SESSIONS_DIR)


# ---------- Akkauntlar ----------
@router.get("/accounts")
async def list_accounts():
    return await manager.list_accounts()


@router.get("/accounts/{phone}")
async def get_account(phone: str):
    acc = await manager.get_account(normalize_phone(phone))
    if not acc:
        raise HTTPException(404, "Akkaunt topilmadi")
    out = manager.public(acc)
    out["sent_today"] = await manager.count_today(acc["phone"], "send_message")
    return out


@router.patch("/accounts/{phone}")
async def patch_account(phone: str, data: AccountPatch):
    """Teglar, izoh, proxy, qurilma ma'lumoti, akkauntning o'z api_id/api_hash i."""
    return await manager.update_account(phone, data.model_dump(exclude_unset=True))


@router.delete("/accounts/{phone}")
async def remove_account(phone: str, logout: bool = False):
    """logout=true — sessiya Telegram'da ham o'chadi, qaytarib bo'lmaydi."""
    if not await manager.remove(phone, logout=logout):
        raise HTTPException(404, "Akkaunt topilmadi")
    return {"status": "removed", "logged_out": logout}


@router.post("/accounts/{phone}/enable")
async def enable_account(phone: str):
    """Nofaol akkauntni qayta yoqish va ulashga urinish."""
    return {"connected": await manager.enable(phone)}


@router.post("/accounts/{phone}/disconnect")
async def disconnect_account(phone: str):
    await manager.disconnect(normalize_phone(phone))
    return {"status": "disconnected"}


@router.get("/accounts/{phone}/actions")
async def account_actions(phone: str, limit: int = 100):
    return await manager.get_actions(normalize_phone(phone), max(1, min(limit, 1000)))


@router.get("/actions")
async def all_actions(limit: int = 200):
    return await manager.get_actions(None, max(1, min(limit, 1000)))


@router.get("/accounts/{phone}/me")
async def me(phone: str):
    async def fn(client):
        u = await client.get_me()
        return {"id": u.id, "first_name": u.first_name, "last_name": u.last_name,
                "username": u.username, "phone": u.phone, "premium": bool(u.premium)}
    return await manager.run(phone, "get_me", fn)


@router.get("/accounts/{phone}/dialogs")
async def dialogs(phone: str, limit: int = 50):
    limit = max(1, min(limit, 500))

    async def fn(client):
        return [
            {"id": d.id, "name": d.name, "unread": d.unread_count}
            async for d in client.iter_dialogs(limit=limit)
        ]
    return await manager.run(phone, "dialogs", fn)


@router.post("/accounts/{phone}/send-message")
async def send_message(phone: str, data: MessageIn):
    to = data.to.strip()
    target = int(to) if to.lstrip("-").isdigit() else to    # chat id raqam sifatida
    if not data.text.strip():
        raise HTTPException(400, "Matn bo'sh")

    async def fn(client):
        try:
            msg = await client.send_message(target, data.text)
        except ValueError:
            raise HTTPException(404, "Qabul qiluvchi topilmadi")
        return {"status": "sent", "message_id": msg.id}
    return await manager.run(phone, "send_message", fn, limited=True)


# ---------- Profil ----------
@router.post("/accounts/{phone}/profile")
async def update_profile(phone: str, data: ProfileIn):
    async def fn(client):
        u = await client(UpdateProfileRequest(
            first_name=data.first_name, last_name=data.last_name, about=data.about,
        ))
        return {"first_name": u.first_name, "last_name": u.last_name}
    res = await manager.run(phone, "profile", fn)
    if data.first_name is not None:
        await manager._update(normalize_phone(phone), first_name=res["first_name"])
    return res


@router.post("/accounts/{phone}/username")
async def update_username(phone: str, data: UsernameIn):
    username = data.username.strip().lstrip("@")

    async def fn(client):
        u = await client(UpdateUsernameRequest(username=username))
        return {"username": u.username}
    res = await manager.run(phone, "username", fn)
    await manager._update(normalize_phone(phone), username=res["username"])
    return res


@router.post("/accounts/{phone}/avatar")
async def upload_avatar(phone: str, file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > MAX_AVATAR:
        raise HTTPException(400, "Rasm juda katta (5 MB gacha)")
    if not (content[:3] == b"\xff\xd8\xff" or content[:8] == b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(400, "Faqat JPG yoki PNG")

    async def fn(client):
        uploaded = await client.upload_file(io.BytesIO(content), file_name=file.filename or "avatar.jpg")
        await client(UploadProfilePhotoRequest(file=uploaded))
        return {"status": "ok"}
    return await manager.run(phone, "avatar", fn)


# ---------- Xavfsizlik: sessiyalar, 2FA, SpamBot ----------
@router.get("/accounts/{phone}/authorizations")
async def authorizations(phone: str):
    """Akkauntga kirgan barcha qurilmalar."""
    async def fn(client):
        res = await client(GetAuthorizationsRequest())
        return [
            {
                "hash": str(a.hash), "current": bool(a.current), "device": a.device_model,
                "platform": a.platform, "system": a.system_version, "app": f"{a.app_name} {a.app_version}",
                "official": bool(a.official_app), "ip": a.ip, "country": a.country,
                "created": int(a.date_created.timestamp()), "active": int(a.date_active.timestamp()),
            }
            for a in res.authorizations
        ]
    return await manager.run(phone, "authorizations", fn)


@router.delete("/accounts/{phone}/authorizations/{auth_hash}")
async def terminate_authorization(phone: str, auth_hash: str):
    """Begona qurilmani chiqarib yuborish (joriy sessiyani emas)."""
    try:
        h = int(auth_hash)
    except ValueError:
        raise HTTPException(400, "hash noto'g'ri")
    if h == 0:
        raise HTTPException(400, "Joriy sessiyani bu yerdan o'chirib bo'lmaydi")

    async def fn(client):
        await client(ResetAuthorizationRequest(hash=h))
        return {"status": "terminated"}
    return await manager.run(phone, "terminate_session", fn)


@router.get("/accounts/{phone}/2fa")
async def twofa_status(phone: str):
    async def fn(client):
        p = await client(GetPasswordRequest())
        return {"has_password": bool(p.has_password), "hint": p.hint}
    res = await manager.run(phone, "2fa_status", fn)
    acc = await manager.get_account(normalize_phone(phone))
    res["saved_in_db"] = bool(acc and acc["twofa"])
    return res


@router.post("/accounts/{phone}/2fa")
async def twofa_set(phone: str, data: TwoFAIn):
    """2FA parol qo'yish yoki almashtirish. Parol bazaga ham yoziladi (zaxira eksport uchun)."""
    if len(data.new_password) < 6:
        raise HTTPException(400, "Parol kamida 6 belgi bo'lsin")
    acc = await manager.get_account(normalize_phone(phone))
    current = data.current_password or (acc["twofa"] if acc else None)

    async def fn(client):
        await client.edit_2fa(current_password=current or None, new_password=data.new_password, hint=data.hint)
        return {"status": "ok"}
    res = await manager.run(phone, "2fa_set", fn)
    await manager._update(normalize_phone(phone), twofa=data.new_password)
    return res


@router.post("/accounts/{phone}/spam-check")
async def spam_check(phone: str):
    """@SpamBot orqali cheklov holatini tekshirish."""
    return await manager.spam_check(phone)


@router.post("/jobs/spam-check")
async def spam_check_all(tag: str | None = None):
    """Barcha faol akkauntlarni fonda navbat bilan tekshirish; holatini GET /jobs dan kuzating."""
    return await manager.start_spam_check_all(tag=tag or None)


@router.post("/jobs/join-channel")
async def join_channel(data: JoinChannelIn):
    """N ta akkauntni kanalga obuna qilish (fon ishi)."""
    return await manager.start_join_channel(data.channel, count=data.count, tag=data.tag or None)


@router.post("/jobs/send-reaction")
async def send_reaction_job(data: SendReactionIn):
    """N ta akkauntdan so'ngi postga reaksiya qo'yish (fon ishi)."""
    return await manager.start_send_reaction(
        data.channel, emoji=data.emoji, count=data.count, tag=data.tag or None,
    )


@router.post("/jobs/warmup")
async def warmup_job(data: WarmupIn = WarmupIn()):
    """Barcha akkauntlarda warm-up (sessiyani tirik tutish)."""
    return await manager.start_warmup(tag=data.tag or None)


@router.post("/accounts/{phone}/randomize-device")
async def randomize_device(phone: str):
    """Akkauntga tasodifiy haqiqiy qurilma profili qo'yish."""
    return await manager.randomize_device(phone)


@router.post("/jobs/bot-start")
async def bot_start_job(data: BotStartIn):
    """N ta akkauntdan botga /start yuborish (referral). t.me/Bot?start=code formatini qabul qiladi."""
    return await manager.start_bot_start(
        data.bot, start_param=data.start_param, count=data.count, tag=data.tag or None,
    )


@router.post("/jobs/bot-autopilot")
async def bot_autopilot_job(data: BotAutopilotIn):
    """Bot Autopilot: /start → majburiy kanalga obuna → tekshirish tugmasini bosish → ref link olish."""
    return await manager.start_bot_autopilot(
        data.bot, start_param=data.start_param, count=data.count, tag=data.tag or None,
    )


@router.post("/jobs/ref-chain")
async def ref_chain_job(data: RefChainIn):
    """Referral zanjiri: har akkaunt ref link oladi va keyingisiga uzatadi."""
    return await manager.start_ref_chain(
        data.bot, start_param=data.start_param, count=data.count, tag=data.tag or None,
    )


@router.post("/jobs/ai-agent")
async def ai_agent_job(data: AIAgentIn):
    """AI Bot Agent: GPT-4o bilan har qanday botni avtomatik bajarish — kanal, tugma, savol-javob."""
    return await manager.start_ai_bot_agent(
        data.bot, start_param=data.start_param, count=data.count, tag=data.tag or None,
    )


@router.get("/jobs")
async def job_status():
    return manager.job


# ---------- Zaxira ----------
@router.get("/export")
async def export_backup():
    """Barcha akkauntlar: .session + .json + strings.txt bitta zip da."""
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        await manager.export_zip(path)
    except BaseException:
        os.remove(path)
        raise
    name = time.strftime("tg-accounts-%Y%m%d-%H%M.zip")
    return FileResponse(path, filename=name, media_type="application/zip",
                        background=BackgroundTask(os.remove, path))


app.include_router(router)

# Tashqi manzil yo'l ostida: topen.uz/7878/... (BASE_PATH=/7878). Lokal: 127.0.0.1:8010/7878/
BASE_PATH = "/" + os.getenv("BASE_PATH", "").strip("/") if os.getenv("BASE_PATH", "").strip("/") else ""
if BASE_PATH:
    from fastapi.responses import RedirectResponse

    root = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    root.mount(BASE_PATH, app)

    @root.get("/", include_in_schema=False)
    async def _to_base():
        return RedirectResponse(BASE_PATH + "/")
else:
    root = app
