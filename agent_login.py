"""
agent_login.py — SIRF Wajid ke apne trusted PC pe chalana hai. VPS pe NAHI.

Kaam:
  1. Supabase se un customers ki list nikaalta hai jinka agent_status =
     'pending_agent_login' (matlab unhone apna HeyGen email/password diya
     hai, par abhi login karke cookie nahi nikaali gayi).
  2. Ek-ek karke: customer ka HeyGen email+password screen pe dikhata hai
     (copy-paste ke liye), ek REAL VISIBLE Chrome kholता hai HeyGen login
     page pe.
  3. Wajid khud us browser mein manually login karta hai (email+password
     paste karke) — agar Cloudflare/Turnstile challenge aaye to khud solve
     kar sakta hai, kyunki real insaan real browser use kar raha hai.
  4. Login success detect hote hi (URL/API check se) cookies extract karke
     Supabase mein save kar deta hai (agent_status='ready'), browser band,
     agla customer.

Usage:
    python agent_login.py            # sabhi pending customers, ek ke baad ek
    python agent_login.py <user_id>  # sirf ek specific customer
"""
import asyncio
import json
import ssl
import sys

import aiohttp
import certifi
import zendriver as zd
from zendriver.cdp import network as cdp_network
from local_settings import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY as SERVICE_ROLE_KEY

HEYGEN_LOGIN_URL = "https://app.heygen.com/login"
LOGIN_SUCCESS_URL_MARKERS = ["/home", "/avatar", "/create-v4", "/dashboard"]

_SSL = ssl.create_default_context(cafile=certifi.where())


def _headers() -> dict:
    return {
        "apikey": SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }


async def _rest_get(path: str) -> list:
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    connector = aiohttp.TCPConnector(ssl=_SSL)
    async with aiohttp.ClientSession(connector=connector) as s:
        async with s.get(url, headers=_headers()) as r:
            return await r.json()


async def _rest_patch(path: str, body: dict):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    connector = aiohttp.TCPConnector(ssl=_SSL)
    headers = _headers()
    headers["Prefer"] = "return=minimal"
    async with aiohttp.ClientSession(connector=connector) as s:
        async with s.patch(url, headers=headers, json=body) as r:
            if r.status not in (200, 204):
                text = await r.text()
                raise RuntimeError(f"Supabase update fail ({r.status}): {text}")


async def fetch_pending_users(single_id: str | None = None) -> list:
    if single_id:
        rows = await _rest_get(f"app_users?id=eq.{single_id}&select=*")
    else:
        rows = await _rest_get("app_users?agent_status=eq.pending_agent_login&select=*")
    return rows


async def is_logged_in(page) -> bool:
    await asyncio.sleep(1.5)
    current_url = page.url or ""
    if "/login" in current_url or "/signin" in current_url:
        return False
    return any(m in current_url for m in LOGIN_SUCCESS_URL_MARKERS) or "heygen.com" in current_url


async def extract_cookie_string(browser) -> str:
    """Browser ki saari cookies nikaal ke 'name1=value1; name2=value2' string banata hai."""
    all_cookies = await browser.cookies.get_all()
    parts = [f"{c.name}={c.value}" for c in all_cookies if "heygen" in (c.domain or "")]
    return "; ".join(parts)


async def process_one(user: dict):
    uid = user["id"]
    phone = user.get("phone") or user.get("email") or uid
    hg_email = user.get("heygen_email")
    hg_password = user.get("heygen_password")

    if not hg_email or not hg_password:
        print(f"[SKIP] {phone} — HeyGen email/password missing hai DB mein.")
        return

    print("\n" + "=" * 60)
    print(f"Customer : {phone}")
    print(f"HeyGen email    : {hg_email}")
    print(f"HeyGen password : {hg_password}")
    print("=" * 60)
    print("Browser khul raha hai — is email/password se manually login karo.")
    print("Agar captcha/verification aaye to khud solve karo.")

    browser = await zd.start(headless=False)
    page = await browser.get(HEYGEN_LOGIN_URL)

    print("Login ka wait ho raha hai... (jab tak tu login nahi karta, ye ruka rahega)")
    while not await is_logged_in(page):
        await asyncio.sleep(2)

    print("✓ Login detect ho gaya. Cookies nikaal raha hoon...")
    cookie_string = await extract_cookie_string(browser)

    if not cookie_string:
        print("✗ Cookies nahi mili — kuch galat hua, is user ko skip kar raha hoon.")
        await browser.stop()
        return

    await _rest_patch(
        f"app_users?id=eq.{uid}",
        {"heygen_cookie": cookie_string, "agent_status": "ready"},
    )
    print(f"✓ Saved! {phone} ka agent_status ab 'ready' hai.")

    await browser.stop()


async def main():
    single_id = sys.argv[1] if len(sys.argv) > 1 else None
    users = await fetch_pending_users(single_id)

    if not users:
        print("Koi pending customer nahi mila (agent_status='pending_agent_login').")
        return

    print(f"{len(users)} customer(s) pending hain. Ek-ek karke process karte hain.\n")
    for user in users:
        await process_one(user)
        print("\nAgle customer ke liye Enter dabao (ya Ctrl+C se rok do)...")
        input()

    print("\nSab ho gaya!")


if __name__ == "__main__":
    asyncio.run(main())
