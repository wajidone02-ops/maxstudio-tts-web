"""
api_server_web.py — MAX Studio TTS (WEB). VPS pe 24/7 chalta hai (systemd service).

Endpoints:
  POST /api/register      -> naya user (email+password+heygen creds), pending
  POST /api/login         -> verify + session token issue
  GET  /api/session       -> token validate + usage info (dashboard ke liye)
  POST /api/logout        -> session_token clear
  POST /api/submit-job    -> text submit, quota check+deduct, tts_jobs insert
  GET  /api/jobs          -> user ki saari jobs (status/output_url) list

NOTE: SERVICE_ROLE key yahan seedha hai kyunki ye sirf VPS (server-side) pe
chalta hai, kabhi client ko nahi milta — jaisa render_worker.py mein hai.
"""
import json
import secrets
import ssl
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import bcrypt
import certifi
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from browser_session_vps import VpsBrowserSession, is_logged_in
from voice_clone import clone_voice_from_audio
from local_settings import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY as SERVICE_ROLE_KEY

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

UPLOADS_DIR = Path("/root/maxstudio-web/uploads")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Voice-clone progress track karne ke liye (task_id -> status), jaisa desktop app mein tha
CLONE_TASKS: dict = {}

_SSL = ssl.create_default_context(cafile=certifi.where())
RESET_INTERVAL_SECONDS = 24 * 3600


def _headers(prefer: str | None = None) -> dict:
    h = {
        "apikey": SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


async def _rest(method: str, path: str, body: dict | None = None, prefer: str | None = None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    connector = aiohttp.TCPConnector(ssl=_SSL)
    async with aiohttp.ClientSession(connector=connector) as s:
        async with s.request(method, url, headers=_headers(prefer), json=body) as r:
            text = await r.text()
            data = json.loads(text) if text else None
            if r.status >= 400:
                raise RuntimeError(f"Supabase {method} {path} failed ({r.status}): {text}")
            return data


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Auth helpers ──────────────────────────────────────────────────────────

async def get_user_by_email(email: str) -> dict | None:
    rows = await _rest("GET", f"app_users?email=eq.{email}&select=*")
    return rows[0] if rows else None


async def get_user_by_token(token: str) -> dict | None:
    rows = await _rest("GET", f"app_users?session_token=eq.{token}&select=*")
    return rows[0] if rows else None


def _ensure_fresh_quota(user: dict) -> dict:
    """Lazy daily reset — agar 24h se zyada ho gaye last reset ko, quota wapas bhar do.
    Ye sirf in-memory dict update karta hai; caller isko DB mein likhna chahe to likh sakta hai."""
    reset_at = user.get("usage_reset_at")
    needs_reset = True
    if reset_at:
        try:
            last = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
            needs_reset = (datetime.now(timezone.utc) - last).total_seconds() > RESET_INTERVAL_SECONDS
        except Exception:
            needs_reset = True
    if needs_reset:
        user["chars_used_today"] = 0
        user["usage_reset_at"] = _now_iso()
    return user


# ─── Models ────────────────────────────────────────────────────────────────

class RegisterBody(BaseModel):
    email: str
    password: str
    heygen_email: str
    heygen_password: str


class LoginBody(BaseModel):
    email: str
    password: str


class SubmitJobBody(BaseModel):
    token: str
    text: str
    voice_id: str
    enhanced: bool = False


# ─── Register ──────────────────────────────────────────────────────────────

@app.post("/api/register")
async def api_register(body: RegisterBody):
    email = body.email.strip().lower()
    if not email or not body.password or not body.heygen_email or not body.heygen_password:
        return {"ok": False, "error": "Saari fields bharo (login email/password, HeyGen email/password)."}
    if len(body.password) < 4:
        return {"ok": False, "error": "Password kam se kam 4 characters ka rakho."}

    existing = await get_user_by_email(email)
    if existing:
        return {"ok": False, "error": "Ye email pehle se registered hai."}

    hashed = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    try:
        await _rest("POST", "app_users", body={
            "email": email,
            "password_hash": hashed,
            "phone": email,  # phone column NOT NULL/unique hai; email hi duplicate rakh dete hain
            "heygen_email": body.heygen_email.strip(),
            "heygen_password": body.heygen_password,
            "agent_status": "pending_agent_login",
            "chars_used_today": 0,
            "usage_reset_at": _now_iso(),
        }, prefer="return=minimal")
    except RuntimeError as e:
        return {"ok": False, "error": f"Register nahi ho paya: {e}"}

    return {"ok": True, "pending": True}


# ─── Login ─────────────────────────────────────────────────────────────────

@app.post("/api/login")
async def api_login(body: LoginBody):
    email = body.email.strip().lower()
    user = await get_user_by_email(email)

    if not user or not bcrypt.checkpw(body.password.encode(), user["password_hash"].encode()):
        return {"ok": False, "error": "Galat email ya password."}
    if not user.get("approved"):
        return {"ok": False, "pending": True, "error": "Account abhi approve nahi hua. Admin se contact karo."}
    if not user.get("is_active", True):
        return {"ok": False, "error": "Account band kar diya gaya hai."}
    expiry = user.get("expiry_date")
    if expiry and datetime.fromisoformat(expiry.replace("Z", "+00:00")) < datetime.now(timezone.utc):
        return {"ok": False, "expired": True, "error": "Plan expire ho gaya hai. Renew ke liye contact karo."}

    token = secrets.token_hex(32)
    await _rest("PATCH", f"app_users?id=eq.{user['id']}", body={
        "session_token": token,
        "last_login_at": _now_iso(),
    }, prefer="return=minimal")

    user = _ensure_fresh_quota(user)
    return {
        "ok": True,
        "token": token,
        "email": user["email"],
        "agent_status": user.get("agent_status"),
        "chars_used_today": user["chars_used_today"],
        "daily_char_limit": user.get("daily_char_limit", 100000),
        "expiry_date": user.get("expiry_date"),
    }


# ─── Session (dashboard load pe validate) ──────────────────────────────────

@app.get("/api/session")
async def api_session(token: str):
    user = await get_user_by_token(token)
    if not user:
        return {"ok": False, "reason": "invalid_session"}
    if not user.get("approved"):
        return {"ok": False, "reason": "pending", "error": "Account approve nahi hua."}
    if not user.get("is_active", True):
        return {"ok": False, "reason": "disabled", "error": "Account band hai."}
    expiry = user.get("expiry_date")
    if expiry and datetime.fromisoformat(expiry.replace("Z", "+00:00")) < datetime.now(timezone.utc):
        return {"ok": False, "reason": "expired", "error": "Plan expire ho gaya hai."}

    user = _ensure_fresh_quota(user)
    return {
        "ok": True,
        "email": user["email"],
        "agent_status": user.get("agent_status"),
        "chars_used_today": user["chars_used_today"],
        "daily_char_limit": user.get("daily_char_limit", 100000),
        "expiry_date": user.get("expiry_date"),
    }


@app.post("/api/logout")
async def api_logout(token: str):
    user = await get_user_by_token(token)
    if user:
        await _rest("PATCH", f"app_users?id=eq.{user['id']}", body={"session_token": None}, prefer="return=minimal")
    return {"ok": True}


# ─── Submit job ─────────────────────────────────────────────────────────────

@app.post("/api/submit-job")
async def api_submit_job(body: SubmitJobBody):
    user = await get_user_by_token(body.token)
    if not user:
        return {"ok": False, "error": "Session invalid, dobara login karo."}
    if user.get("agent_status") != "ready":
        return {"ok": False, "error": "Aapka HeyGen account abhi connect nahi hua. Admin se contact karo."}

    text = body.text.strip()
    if not text:
        return {"ok": False, "error": "Text khali hai."}

    char_count = len(text)
    user = _ensure_fresh_quota(user)
    limit = user.get("daily_char_limit", 100000)
    used = user["chars_used_today"]

    if used + char_count > limit:
        remaining = max(0, limit - used)
        return {"ok": False, "error": f"Aaj ka quota khatam ho raha hai. Sirf {remaining} characters bache hain."}

    # Submit-time hi deduct karo (jaisa decide hua)
    new_used = used + char_count
    await _rest("PATCH", f"app_users?id=eq.{user['id']}", body={
        "chars_used_today": new_used,
        "usage_reset_at": user["usage_reset_at"],
    }, prefer="return=minimal")

    job = await _rest("POST", "tts_jobs", body={
        "user_id": user["id"],
        "text": text,
        "voice_id": body.voice_id,
        "char_count": char_count,
        "enhanced": body.enhanced,
        "status": "pending",
    }, prefer="return=representation")

    return {"ok": True, "job_id": job[0]["id"], "chars_used_today": new_used, "daily_char_limit": limit}


# ─── List jobs ──────────────────────────────────────────────────────────────

@app.get("/api/jobs")
async def api_jobs(token: str):
    user = await get_user_by_token(token)
    if not user:
        return {"ok": False, "error": "Session invalid."}
    rows = await _rest(
        "GET",
        f"tts_jobs?user_id=eq.{user['id']}&select=id,status,output_url,error_message,char_count,text,created_at,completed_at&order=created_at.desc&limit=50",
    )
    for row in rows:
        row["text_preview"] = (row.get("text") or "")[:60]
    return {"ok": True, "jobs": rows}


# ─── Voice cloning ──────────────────────────────────────────────────────────

@app.get("/api/voices")
async def api_list_voices(token: str):
    user = await get_user_by_token(token)
    if not user:
        return {"ok": False, "error": "Session invalid."}
    rows = await _rest("GET", f"tts_voices?user_id=eq.{user['id']}&select=id,name,voice_id,created_at&order=created_at.desc")
    return {"ok": True, "voices": rows}


@app.post("/api/voices/clone")
async def api_voices_clone(token: str = Form(...), name: str = Form(...), audio: UploadFile = File(...)):
    user = await get_user_by_token(token)
    if not user:
        return {"ok": False, "error": "Session invalid, dobara login karo."}
    if user.get("agent_status") != "ready":
        return {"ok": False, "error": "Aapka HeyGen account abhi connect nahi hua."}
    if not user.get("heygen_cookie"):
        return {"ok": False, "error": "HeyGen session nahi mili."}

    task_id = str(uuid.uuid4())
    CLONE_TASKS[task_id] = {"status": "running", "messages": [], "error": None, "result": None}

    audio_path = UPLOADS_DIR / f"{task_id}_{audio.filename}"
    with open(audio_path, "wb") as f:
        f.write(await audio.read())

    import asyncio
    asyncio.create_task(_run_clone(task_id, user["id"], user["heygen_cookie"], name, str(audio_path)))
    return {"ok": True, "task_id": task_id}


async def _run_clone(task_id: str, user_id: str, cookie_string: str, name: str, audio_path: str):
    async def status_cb(msg):
        CLONE_TASKS[task_id]["messages"].append(msg)

    session = None
    try:
        session = VpsBrowserSession(cookie_string)
        page = await session.start()
        if not await is_logged_in(page):
            raise RuntimeError("HeyGen session expire ho gayi — admin se dobara connect karwao.")

        voice_id = await clone_voice_from_audio(page, audio_path, name, status_cb=status_cb)

        await _rest("POST", "tts_voices", body={
            "user_id": user_id, "name": name, "voice_id": voice_id,
        }, prefer="return=minimal")

        CLONE_TASKS[task_id]["status"] = "done"
        CLONE_TASKS[task_id]["result"] = {"voice_id": voice_id, "name": name}
    except Exception as e:
        CLONE_TASKS[task_id]["status"] = "failed"
        CLONE_TASKS[task_id]["error"] = str(e)
    finally:
        if session:
            await session.stop()
        Path(audio_path).unlink(missing_ok=True)


@app.get("/api/voices/clone/status/{task_id}")
async def api_voices_clone_status(task_id: str):
    task = CLONE_TASKS.get(task_id)
    if not task:
        return {"ok": False, "error": "task not found"}
    return {"ok": True, **task}
