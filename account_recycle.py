"""
account_recycle.py — Secondary account ko DELETE karke wapas SIGNUP karna
(cookie fresh karne ke liye, jab account ki daily TTS-limit khatam ho jaye).

DELETE flow — 100% confirmed HAR se, pure API calls hain, koi
browser-UI-interaction nahi chahiye:
    1. POST /v1/pacific/account/deletion.request_code  -> email pe OTP
    2. (email se OTP fetch — email_fetch.py)
    3. POST /v1/pacific/user.delete  {"verification_code": "XXXXXX"}

SIGNUP flow — HAR se confirm hua ke iska magic_link request mein
'turnstile_token' aur 'fingerprint' hote hain — dono browser ke andar
Cloudflare Turnstile widget se generate hote hain, HAR se REPLAY nahi ho
sakte (har baar naye/unique). Isliye ye asli browser-UI interaction se
karna padta hai (email-input bharo, submit dabao) — page khud magic_link
request bhejegi turnstile-token ke saath, hum sirf UI-interact karte hain.

NOTE: signup page ke email-input/submit-button ke selectors abhi BEST-GUESS
hain (generic patterns) — agar fail ho, exact selector confirm karna hoga
(DOM inspect karke).
"""
import asyncio

from api_client import browser_fetch, ApiError
import config


# ─── DELETE flow (confirmed, pure API) ──────────────────────────────────────

async def request_deletion_code(page):
    """Email pe 6-digit OTP bhejta hai. Returns HeyGen ka raw response."""
    return await browser_fetch(page, "POST", "/v1/pacific/account/deletion.request_code", json_body={})


async def confirm_deletion(page, verification_code: str):
    """OTP submit karke account PERMANENTLY delete karta hai."""
    return await browser_fetch(
        page, "POST", "/v1/pacific/user.delete",
        json_body={"verification_code": verification_code},
    )


# ─── SIGNUP flow (real browser-UI interaction — Turnstile ki wajah se) ──────

SIGNUP_URL = "https://app.heygen.com/signup"
MAGIC_LINK_WAIT_AFTER_SUBMIT = 3  # seconds — turnstile solve + request jaane ka waqt

_CLICK_BY_TEXT_JS = """
(() => {{
    const target = {text!r};
    const buttons = Array.from(document.querySelectorAll('button'));
    const btn = buttons.find(b => b.textContent.trim().toLowerCase().includes(target.toLowerCase()));
    if (!btn) return false;
    btn.click();
    return true;
}})()
"""

_FILL_EMAIL_JS = """
(() => {{
    const input = document.querySelector('input[type="email"]');
    if (!input) return false;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, {email!r});
    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
    return true;
}})()
"""


async def trigger_signup(browser, email_addr: str, status_cb=None):
    """
    Signup page kholta hai, UI flow chalata hai:
      1. "Use email" button click
      2. Email input bharo
      3. (kabhi-kabhi) Cloudflare captcha-click — best-effort
      4. "Send a secure magic link" button click
    """
    async def _status(msg):
        if status_cb:
            await status_cb(msg)

    await _status("Signup page khol raha hoon...")
    page = await browser.get(SIGNUP_URL)
    await asyncio.sleep(3)

    await _status("'Use email' button dhoondh raha hoon...")
    clicked = await page.evaluate(_CLICK_BY_TEXT_JS.format(text="Use email"), await_promise=False)
    if not clicked:
        raise RuntimeError("'Use email' button nahi mila — HeyGen ne UI badal di ho sakti hai.")
    await asyncio.sleep(2)

    await _status(f"Email bhar raha hoon ({email_addr})...")
    filled = await page.evaluate(_FILL_EMAIL_JS.format(email=email_addr), await_promise=False)
    if not filled:
        raise RuntimeError("Email input nahi mila.")
    await asyncio.sleep(1)

    await _status("Captcha check kar raha hoon (agar ho to)...")
    try:
        captcha_checkbox = await page.select("iframe[src*='challenges.cloudflare.com']", timeout=4)
        if captcha_checkbox:
            await captcha_checkbox.click()
            await _status("Captcha click ho gaya, thoda wait kar raha hoon...")
            await asyncio.sleep(3)
    except Exception:
        pass

    await _status("'Send a secure magic link' dabाa raha hoon...")
    sent = await page.evaluate(_CLICK_BY_TEXT_JS.format(text="magic link"), await_promise=False)
    if not sent:
        raise RuntimeError("'Send a secure magic link' button nahi mila.")

    await asyncio.sleep(MAGIC_LINK_WAIT_AFTER_SUBMIT)
    await _status("Signup submit ho gaya — email pe magic-link ka wait kar rahe hain...")


async def login_via_magic_link(browser, magic_link_url: str, status_cb=None):
    """
    Magic-link URL kholta hai — HeyGen ye link kholne pe LOGIN kar deta hai.
    Confirmed: 4-6 sec mein 'heygen_is_login'/'heygen_session' cookies aa
    jaate hain — 'heygen_token' ka wait NAHI karna (wo is flow mein kabhi
    nahi aata).
    """
    async def _status(msg):
        if status_cb:
            await status_cb(msg)

    await _status("Magic-link khol raha hoon (login ho jayega)...")
    page = await browser.get(magic_link_url)

    elapsed = 0
    while elapsed < 40:
        cookies = await browser.cookies.get_all()
        heygen_cookie_names = sorted(set(c.name for c in cookies if "heygen" in c.name.lower()))
        has_auth = "heygen_is_login" in heygen_cookie_names or "heygen_session" in heygen_cookie_names
        if has_auth:
            break
        await asyncio.sleep(2)
        elapsed += 2
    else:
        raise RuntimeError("Login ke baad bhi 'heygen_is_login'/'heygen_session' cookie nahi mila (40s timeout) — login fail hua lagta hai.")

    await asyncio.sleep(3)  # extra buffer — baaki auth-cookies settle hone ke liye
    return page
