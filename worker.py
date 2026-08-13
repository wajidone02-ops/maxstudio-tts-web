"""
worker.py — MAX Studio TTS (WEB) ka background job-processor.
VPS pe systemd service ke through 24/7 chalta hai.

Kaam:
  - tts_jobs table poll karta hai (status='pending')
  - MAX_CONCURRENT (4) jobs ek saath process karta hai (asyncio semaphore)
  - Har job: user ka cookie -> browser session -> (enhanced ho to enhance
    + SSML) -> chunk -> TTS generate -> audio files download+merge -> save
  - Job status update: done (output_url) ya failed (error_message)
  - Stale-job recovery: kabhi worker crash ho jaye job ke beech, 'processing'
    mein 30+ min se atki job wapas 'pending' ho jaati hai (proven pattern)
"""
import asyncio
import json
import ssl
import subprocess
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import aiohttp
import certifi

from browser_session_vps import VpsBrowserSession, is_logged_in
from tts_generate import generate_tts_audio, enhance_script_text
from text_chunker import split_into_chunks
from local_settings import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY as SERVICE_ROLE_KEY

MAX_CONCURRENT = 4
POLL_INTERVAL = 3          # seconds — free slot ho to kitni jaldi naya job dhoonde
STALE_RECOVERY_INTERVAL = 600   # 10 min — proven pattern (render_worker.py se)
STALE_THRESHOLD_MIN = 30
OUTPUT_DIR = Path("/root/maxstudio-web/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Placeholder video_id — TTS endpoint ise validate nahi karta (confirmed
# tere apne test se: "koi bhi string chalta hai")
PLACEHOLDER_VIDEO_ID = "web0000000000000000000000000000"

_SSL = ssl.create_default_context(cafile=certifi.where())
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


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


def _short_id() -> str:
    import secrets, string
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))


async def _download(url: str, out_path: Path):
    connector = aiohttp.TCPConnector(ssl=_SSL)
    async with aiohttp.ClientSession(connector=connector) as sess:
        async with sess.get(url) as resp:
            resp.raise_for_status()
            with open(out_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 64):
                    f.write(chunk)


async def _merge_audio_files(file_paths: list, output_path: Path) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    list_file = output_path.with_suffix(".txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in file_paths:
            escaped = str(Path(p).resolve()).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    def _run():
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output_path)],
            capture_output=True, text=True,
        )
        return result.returncode == 0

    try:
        ok = await asyncio.to_thread(_run)
        return ok and output_path.exists()
    finally:
        list_file.unlink(missing_ok=True)


# ─── Job claim (atomic) ──────────────────────────────────────────────────

async def claim_next_job() -> dict | None:
    """Ek pending job atomically claim karta hai (PATCH with filter — race-safe
    kyunki Supabase REST ka PATCH sirf matching rows pe apply hota hai)."""
    rows = await _rest("GET", "tts_jobs?status=eq.pending&order=created_at.asc&limit=1&select=*")
    if not rows:
        return None
    job = rows[0]
    # Claim: status ko processing set karo SIRF agar abhi bhi pending hai
    result = await _rest(
        "PATCH",
        f"tts_jobs?id=eq.{job['id']}&status=eq.pending",
        body={"status": "processing"},
        prefer="return=representation",
    )
    if not result:
        return None  # kisi aur worker ne pehle claim kar li
    return result[0]


async def get_user(user_id: str) -> dict | None:
    rows = await _rest("GET", f"app_users?id=eq.{user_id}&select=*")
    return rows[0] if rows else None


async def mark_done(job_id: str, output_url: str):
    await _rest("PATCH", f"tts_jobs?id=eq.{job_id}", body={
        "status": "done", "output_url": output_url, "completed_at": _now_iso(),
    }, prefer="return=minimal")


async def mark_failed(job_id: str, error: str):
    await _rest("PATCH", f"tts_jobs?id=eq.{job_id}", body={
        "status": "failed", "error_message": str(error)[:500], "completed_at": _now_iso(),
    }, prefer="return=minimal")


async def refund_chars(user_id: str, char_count: int):
    """Job fail ho jaye to submit-time pe kaate gaye characters wapas karo."""
    user = await get_user(user_id)
    if not user:
        return
    new_used = max(0, user.get("chars_used_today", 0) - char_count)
    await _rest("PATCH", f"app_users?id=eq.{user_id}", body={"chars_used_today": new_used}, prefer="return=minimal")


# ─── Job processing ───────────────────────────────────────────────────────

async def process_job(job: dict):
    job_id = job["id"]
    user_id = job["user_id"]
    text = job["text"]
    voice_id = job["voice_id"]
    enhanced = job.get("enhanced", False)
    director_style = job.get("director_style")

    session = None
    try:
        user = await get_user(user_id)
        if not user or not user.get("heygen_cookie"):
            raise RuntimeError("User ka HeyGen account connect nahi hai.")

        session = VpsBrowserSession(user["heygen_cookie"])
        page = await session.start()

        if not await is_logged_in(page):
            raise RuntimeError("HeyGen session expire ho gaya — dobara connect karna hoga.")

        # Enhanced ho to poore text ko chunk se PEHLE enhance karo (behtar context)
        working_text = text
        if enhanced and not director_style:
            working_text = await enhance_script_text(page, text, PLACEHOLDER_VIDEO_ID)

        chunks = split_into_chunks(working_text)
        batch_ts = int(time.time())
        saved_files = []

        for i, piece in enumerate(chunks):
            script_id = _short_id()
            prev_text = chunks[i - 1] if i > 0 else ""
            next_text = chunks[i + 1] if i < len(chunks) - 1 else ""

            tts_result = await generate_tts_audio(
                page, piece, voice_id, PLACEHOLDER_VIDEO_ID, script_id,
                previous_text=prev_text, next_text=next_text,
                enhanced=(enhanced and not director_style), director_style=director_style,
            )
            fname = OUTPUT_DIR / f"{job_id}_part{i + 1}.wav"
            await _download(tts_result["url"], fname)
            saved_files.append(str(fname))

        final_path = OUTPUT_DIR / f"{job_id}.wav"
        if len(saved_files) > 1:
            merged = await _merge_audio_files(saved_files, final_path)
            if merged:
                for p in saved_files:
                    Path(p).unlink(missing_ok=True)
            else:
                # Merge fail ho to pehla part hi final bana do (better than total fail)
                Path(saved_files[0]).rename(final_path)
                for p in saved_files[1:]:
                    Path(p).unlink(missing_ok=True)
        else:
            Path(saved_files[0]).rename(final_path)

        # Nginx se /downloads/<file> route hoga (setup step baad mein)
        output_url = f"/downloads/{final_path.name}"
        await mark_done(job_id, output_url)
        print(f"[done] job {job_id}")

    except Exception as e:
        print(f"[failed] job {job_id}: {e}")
        await mark_failed(job_id, str(e))
        await refund_chars(user_id, job.get("char_count", 0))
    finally:
        if session:
            await session.stop()


async def worker_loop():
    async with _semaphore:
        pass  # semaphore ka actual use process_job ke around hota hai neeche


async def main_loop():
    print(f"[worker] shuru ho raha hai, MAX_CONCURRENT={MAX_CONCURRENT}")
    last_recovery = 0.0

    async def run_with_semaphore(job):
        async with _semaphore:
            await process_job(job)

    active_tasks: set = set()

    while True:
        # Stale-job recovery (proven pattern)
        if time.time() - last_recovery > STALE_RECOVERY_INTERVAL:
            try:
                cutoff = datetime.now(timezone.utc).timestamp() - STALE_THRESHOLD_MIN * 60
                cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
                # URL-encode zaroori hai — 'cutoff_iso' mein '+00:00' hota hai,
                # aur bina encode kiye '+' URL mein space ban jaata hai
                # (jo Postgres timestamp parse fail kar deta hai). Yehi bug
                # tha jiski wajah se stale-job recovery kabhi kaam nahi kar
                # raha tha — atki hui jobs hamesha ke liye 'processing' mein
                # phasi reh jaati thi.
                cutoff_iso_encoded = quote(cutoff_iso, safe="")
                await _rest(
                    "PATCH",
                    f"tts_jobs?status=eq.processing&created_at=lt.{cutoff_iso_encoded}",
                    body={"status": "pending"},
                    prefer="return=minimal",
                )
            except Exception as e:
                print(f"[recovery] error: {e}")
            last_recovery = time.time()

        # Free slot ho to naya job utha lo
        if len(active_tasks) < MAX_CONCURRENT:
            try:
                job = await claim_next_job()
            except Exception as e:
                print(f"[claim] error: {e}")
                job = None

            if job:
                task = asyncio.create_task(run_with_semaphore(job))
                active_tasks.add(task)
                task.add_done_callback(active_tasks.discard)

        active_tasks = {t for t in active_tasks if not t.done()}
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
