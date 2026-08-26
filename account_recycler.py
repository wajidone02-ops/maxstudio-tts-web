"""
account_recycler.py — Secondary account ka poora RECYCLE cycle:
  1. Current cookie se login karke DELETE karo (OTP email se)
  2. Usi email se dobara SIGNUP karo (magic-link email se login)
  3. Naya cookie Supabase mein save karo
  4. disabled_until CLEAR karo — account fresh, turant use ke liye ready

RISK (bahut zaroori): account DELETE PERMANENT hai. Isliye:
  - Pehle SIRF EK account pe MANUAL test karo (neeche "Manual test" dekho)
  - Tabhi loop-mode chalao jab manual test 100% pass ho jaye
  - Har step Telegram pe alert karta hai (kuch bhi galat ho turant pata chale)

Chalane ka tareeka:
  Manual test (EK account):
    python3 account_recycler.py secondary1

  Loop mode (SAB disabled accounts, har X ghante check):
    python3 account_recycler.py --loop
"""
import asyncio
import sys
import ssl

import aiohttp
import certifi

import config
from local_settings import SUPABASE_SERVICE_ROLE_KEY
from browser_session import BrowserSession
from backend_client import extract_heygen_cookie_string, sync_secondary_account
from account_recycle import request_deletion_code, confirm_deletion, trigger_signup, login_via_magic_link
from email_fetch import fetch_otp_code, fetch_magic_link

_REST_BASE = f"{config.SUPABASE_URL.rstrip('/')}/rest/v1"
_REST_HEADERS = {
    "apikey": SUPABASE_SERVICE_ROLE_KEY,
    "authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
}
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

CHECK_INTERVAL_SECONDS = 3600  # loop-mode mein har 1h check


async def _fetch_account_and_email(label: str) -> tuple[dict, dict] | None:
    """heygen_secondary_accounts + secondary_email_credentials dono se
    is label ki row leke aata hai. Dono mein row na ho to None."""
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_SSL_CONTEXT)) as sess:
        url1 = f"{_REST_BASE}/heygen_secondary_accounts?label=eq.{label}&select=label,cookie_string,video_id"
        async with sess.get(url1, headers=_REST_HEADERS) as resp:
            rows1 = await resp.json() if resp.status < 400 else []
        url2 = f"{_REST_BASE}/secondary_email_credentials?secondary_label=eq.{label}&select=email,app_password"
        async with sess.get(url2, headers=_REST_HEADERS) as resp:
            rows2 = await resp.json() if resp.status < 400 else []
    if not rows1 or not rows2:
        return None
    return rows1[0], rows2[0]


async def _fetch_disabled_labels_with_email() -> list[str]:
    """Sab disabled_until > now waale accounts, JINKE liye email-credentials
    bhi maujood hain (baaki abhi is flow mein cover nahi honge)."""
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_SSL_CONTEXT)) as sess:
        url = f"{_REST_BASE}/heygen_secondary_accounts?disabled_until=not.is.null&select=label"
        async with sess.get(url, headers=_REST_HEADERS) as resp:
            disabled_rows = await resp.json() if resp.status < 400 else []
        url2 = f"{_REST_BASE}/secondary_email_credentials?select=secondary_label"
        async with sess.get(url2, headers=_REST_HEADERS) as resp:
            email_rows = await resp.json() if resp.status < 400 else []
    email_labels = {r["secondary_label"] for r in email_rows}
    return [r["label"] for r in disabled_rows if r["label"] in email_labels]


async def _clear_disabled(label: str):
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_SSL_CONTEXT)) as sess:
        url = f"{_REST_BASE}/heygen_secondary_accounts?label=eq.{label}"
        await sess.patch(url, headers=_REST_HEADERS, json={"disabled_until": None, "updated_at": "now()"})


async def recycle_account(label: str) -> bool:
    """
    Poora cycle. Returns True on success, False on failure (details
    print() ho jaate hain — is function ka print()-log dhyan se padhna).
    """
    print(f"[recycle][{label}] ==== RECYCLE SHURU ====")

    row = await _fetch_account_and_email(label)
    if not row:
        print(f"[recycle][{label}] FAIL: account ya email-credentials nahi mile Supabase mein.")
        return False
    account_row, email_row = row
    old_cookie = account_row["cookie_string"]
    video_id = account_row["video_id"]
    email_addr = email_row["email"]
    app_password = email_row["app_password"]

    # ── STEP 1: DELETE (purani cookie se login karke) ──────────────────
    session = BrowserSession(start_hidden=True, ephemeral=True)
    browser = await session.start()
    try:
        await session.inject_cookie_string(old_cookie)
        page = await browser.get(config.HEYGEN_APP_URL)
        await asyncio.sleep(4)  # pehle 2 sec tha — fresh/naye account kabhi
        # onboarding-redirect karte hain, jisse beech-mein-navigate ho jaata
        # tha aur API-call "target navigated or closed" se toot jaati thi.

        print(f"[recycle][{label}] Deletion-code maang raha hoon...")
        try:
            await request_deletion_code(page)
        except Exception as e:
            if "navigated" in str(e).lower() or "closed" in str(e).lower():
                # Page redirect ho rahi thi jab call ki — ab settle ho chuki
                # hogi, EK baar turant retry karo.
                print(f"[recycle][{label}] Page navigate ho rahi thi, thoda wait karke retry kar raha hoon...")
                await asyncio.sleep(3)
                await request_deletion_code(page)
            else:
                raise

        print(f"[recycle][{label}] Email se OTP ka wait kar raha hoon...")
        otp = await fetch_otp_code(email_addr, app_password)
        print(f"[recycle][{label}] OTP mila: {otp}")

        print(f"[recycle][{label}] Account DELETE kar raha hoon (PERMANENT)...")
        await confirm_deletion(page, otp)
        print(f"[recycle][{label}] Account delete ho gaya.")
    except Exception as e:
        print(f"[recycle][{label}] FAIL (delete step): {type(e).__name__}: {e}")
        await session.stop()
        return False
    finally:
        try:
            await session.stop()
        except Exception:
            pass

    # ── STEP 2: SIGNUP (fresh browser session, koi purani cookie nahi) ──
    session2 = BrowserSession(start_hidden=True, ephemeral=True)
    browser2 = await session2.start()
    try:
        async def _status(msg):
            print(f"[recycle][{label}] {msg}")

        await trigger_signup(browser2, email_addr, status_cb=_status)

        print(f"[recycle][{label}] Email se magic-link ka wait kar raha hoon...")
        magic_link = await fetch_magic_link(email_addr, app_password)
        print(f"[recycle][{label}] Magic-link mila, khol raha hoon...")

        await login_via_magic_link(browser2, magic_link, status_cb=_status)

        new_cookie = await extract_heygen_cookie_string(browser2)
        if not new_cookie:
            raise RuntimeError("Naye login ke baad bhi cookie khali hai — login fail hua lagta hai.")

        await sync_secondary_account(label, new_cookie, video_id)
        print(f"[recycle][{label}] Naya cookie save ho gaya.")
    except Exception as e:
        print(f"[recycle][{label}] FAIL (signup step): {type(e).__name__}: {e}")
        print(f"[recycle][{label}] ⚠️ ACCOUNT DELETE HO CHUKA HAI PAR SIGNUP FAIL HUA — is account ko MANUALLY dobara signup karna padega!")
        await session2.stop()
        return False
    finally:
        try:
            await session2.stop()
        except Exception:
            pass

    await _clear_disabled(label)
    print(f"[recycle][{label}] ==== RECYCLE SUCCESS — account fresh hai ====")
    return True


async def loop_mode():
    print("[account_recycler] Loop mode shuru — har 1h disabled accounts check karunga.")
    while True:
        try:
            labels = await _fetch_disabled_labels_with_email()
            if labels:
                print(f"[account_recycler] {len(labels)} disabled account(s) mile (email-credentials waale): {labels}")
                for label in labels:
                    await recycle_account(label)
            else:
                print("[account_recycler] Koi disabled account nahi (email-credentials waala).")
        except Exception as e:
            print(f"[account_recycler] Loop error (non-fatal): {type(e).__name__}: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 account_recycler.py <secondary_label>   (manual, EK account test)")
        print("   ya: python3 account_recycler.py --loop              (sab disabled accounts, automatic)")
        sys.exit(1)

    if sys.argv[1] == "--loop":
        asyncio.run(loop_mode())
    else:
        label = sys.argv[1]
        success = asyncio.run(recycle_account(label))
        sys.exit(0 if success else 1)
