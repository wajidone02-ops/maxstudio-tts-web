"""
account_recycler_web.py — MAX Studio TTS (WEB) ka account-automation service.
VPS pe systemd service ke through 24/7 chalta hai (loop-mode).

Do triggers handle karta hai:
  A) Naya approved user, agent_status='not_connected' -> pehla signup
     (Gmail login + HeyGen signup + magic-link) — turant jab approve ho.
  B) agent_status='needs_recycle' (worker ne set kiya, rate-limit error pe)
     -> delete purana account + resignup + voices re-clone.

IMPORTANT — headless=True hi hai (koi Xvfb/special setup nahi chahiye) —
video-project mein isi tareeke se VPS pe proven/confirmed hai, signup ke
liye bhi.
"""
import asyncio
import json
import ssl

import aiohttp
import certifi

from browser_session_vps import VpsBrowserSession, extract_cookie_string
from account_recycle import trigger_signup, login_via_magic_link, request_deletion_code, confirm_deletion
from email_fetch import fetch_otp_code, fetch_magic_link
from voice_clone import clone_voice_from_audio, finalize_voice_clone
from local_settings import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY as SERVICE_ROLE_KEY

CHECK_INTERVAL_SECONDS = 30  # kitni jaldi naye-approved/needs_recycle users check kare
_SSL = ssl.create_default_context(cafile=certifi.where())


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


# ─── Trigger A: pehla signup (naya approved user) ──────────────────────────

async def find_pending_first_signups() -> list[dict]:
    return await _rest(
        "GET",
        "app_users?approved=eq.true&agent_status=eq.not_connected"
        "&recycle_gmail=not.is.null&select=*",
    )


async def do_first_signup(user: dict):
    uid = user["id"]
    gmail = user["recycle_gmail"]
    app_password = user["recycle_gmail_app_password"]
    print(f"[recycler][{uid}] Pehla signup shuru — Gmail: {gmail}")

    await _rest("PATCH", f"app_users?id=eq.{uid}", body={"agent_status": "recycling"}, prefer="return=minimal")

    session = VpsBrowserSession(cookie_string="")
    try:
        await session.start(navigate_url="about:blank")

        async def _status(msg):
            print(f"[recycler][{uid}] {msg}")

        await trigger_signup(session.browser, gmail, status_cb=_status)

        print(f"[recycler][{uid}] Email se magic-link ka wait kar raha hoon...")
        magic_link = await fetch_magic_link(gmail, app_password)

        await login_via_magic_link(session.browser, magic_link, status_cb=_status)

        new_cookie = await extract_cookie_string(session.browser)
        if not new_cookie:
            raise RuntimeError("Login ke baad bhi cookie khali hai.")

        await _rest("PATCH", f"app_users?id=eq.{uid}", body={
            "heygen_cookie": new_cookie, "agent_status": "ready",
        }, prefer="return=minimal")
        print(f"[recycler][{uid}] Pehla signup complete, account ready hai.")
    except Exception as e:
        print(f"[recycler][{uid}] FAIL (first signup): {type(e).__name__}: {e}")
        await _rest("PATCH", f"app_users?id=eq.{uid}", body={"agent_status": "not_connected"}, prefer="return=minimal")
    finally:
        await session.stop()


# ─── Trigger B: recycle (rate-limit hone ke baad) ──────────────────────────

async def find_needs_recycle() -> list[dict]:
    return await _rest("GET", "app_users?agent_status=eq.needs_recycle&select=*")


async def do_recycle(user: dict):
    uid = user["id"]
    gmail = user["recycle_gmail"]
    app_password = user["recycle_gmail_app_password"]
    old_cookie = user.get("heygen_cookie")
    print(f"[recycler][{uid}] Recycle shuru — Gmail: {gmail}")

    await _rest("PATCH", f"app_users?id=eq.{uid}", body={"agent_status": "recycling"}, prefer="return=minimal")

    # ── STEP 1: DELETE (purani cookie se) ───────────────────────────────
    if old_cookie:
        del_session = VpsBrowserSession(cookie_string=old_cookie, headless=True)
        try:
            page = await del_session.start()
            await asyncio.sleep(2)
            try:
                await request_deletion_code(page)
            except Exception as e:
                if "navigated" in str(e).lower() or "closed" in str(e).lower():
                    await asyncio.sleep(3)
                    await request_deletion_code(page)
                else:
                    raise
            print(f"[recycler][{uid}] Email se OTP ka wait kar raha hoon...")
            otp = await fetch_otp_code(gmail, app_password)
            await confirm_deletion(page, otp)
            print(f"[recycler][{uid}] Purana account delete ho gaya.")
        except Exception as e:
            print(f"[recycler][{uid}] FAIL (delete step): {type(e).__name__}: {e} — signup phir bhi try karenge.")
        finally:
            await del_session.stop()

    # ── STEP 2: SIGNUP (fresh) ───────────────────────────────────────────
    new_cookie = None
    signup_session = VpsBrowserSession(cookie_string="")
    try:
        async def _status(msg):
            print(f"[recycler][{uid}] {msg}")

        await signup_session.start(navigate_url="about:blank")
        await trigger_signup(signup_session.browser, gmail, status_cb=_status)

        print(f"[recycler][{uid}] Email se magic-link ka wait kar raha hoon...")
        magic_link = await fetch_magic_link(gmail, app_password)
        await login_via_magic_link(signup_session.browser, magic_link, status_cb=_status)

        new_cookie = await extract_cookie_string(signup_session.browser)
        if not new_cookie:
            raise RuntimeError("Naye login ke baad bhi cookie khali hai.")

        await _rest("PATCH", f"app_users?id=eq.{uid}", body={"heygen_cookie": new_cookie}, prefer="return=minimal")
        print(f"[recycler][{uid}] Naya cookie save ho gaya.")
    except Exception as e:
        print(f"[recycler][{uid}] FAIL (signup step): {type(e).__name__}: {e}")
        await _rest("PATCH", f"app_users?id=eq.{uid}", body={"agent_status": "not_connected"}, prefer="return=minimal")
        return
    finally:
        await signup_session.stop()

    # ── STEP 3: Saari voices re-clone karo (naya account = naye voice_ids) ──
    voices = await _rest("GET", f"tts_voices?user_id=eq.{uid}&select=*")
    reclone_session = VpsBrowserSession(cookie_string=new_cookie, headless=True)
    try:
        page = await reclone_session.start()
        for v in voices:
            if not v.get("source_audio_path"):
                print(f"[recycler][{uid}] Voice '{v['name']}' ka source-audio nahi hai — skip.")
                continue
            try:
                print(f"[recycler][{uid}] Voice '{v['name']}' re-clone kar raha hoon...")
                result = await clone_voice_from_audio(page, v["source_audio_path"], v["name"], status_cb=None)
                voice_id = result["voice_id"]
                if v.get("chosen_engine"):
                    await finalize_voice_clone(page, voice_id, v["chosen_engine"])
                await _rest("PATCH", f"tts_voices?id=eq.{v['id']}", body={"voice_id": voice_id}, prefer="return=minimal")
                print(f"[recycler][{uid}] Voice '{v['name']}' re-clone ho gayi.")
            except Exception as e:
                print(f"[recycler][{uid}] Voice '{v['name']}' re-clone FAIL: {type(e).__name__}: {e}")
    finally:
        await reclone_session.stop()

    await _rest("PATCH", f"app_users?id=eq.{uid}", body={"agent_status": "ready"}, prefer="return=minimal")
    print(f"[recycler][{uid}] Recycle complete — account ready hai.")


# ─── Loop ───────────────────────────────────────────────────────────────

async def loop_mode():
    print("[account_recycler_web] Loop mode shuru.")
    while True:
        try:
            for user in await find_pending_first_signups():
                await do_first_signup(user)
            for user in await find_needs_recycle():
                await do_recycle(user)
        except Exception as e:
            print(f"[account_recycler_web] Loop error (non-fatal): {type(e).__name__}: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(loop_mode())
