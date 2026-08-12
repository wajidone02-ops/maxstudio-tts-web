"""
browser_session_vps.py — VPS worker ke liye. Desktop wale browser_session.py
se halka hai kyunki VPS pe koi screen hi nahi — window-hide/taskbar-hide
logic (Windows-specific) yahan zaroori nahi. Seedha headless=True.

Login user ke khud form-fill karne se nahi hota — humare paas already
customer ka heygen_cookie (Supabase se) hota hai, wahi seedha inject karte
hain browser start hote hi.
"""
import tempfile
from pathlib import Path

import zendriver as zd
from zendriver.cdp import network as cdp_network

HEYGEN_APP_URL = "https://app.heygen.com/"


class VpsBrowserSession:
    def __init__(self, cookie_string: str):
        self.cookie_string = cookie_string
        self.profile_dir = Path(tempfile.mkdtemp(prefix="tts_worker_"))
        self.browser: zd.Browser | None = None
        self.page = None

    async def start(self):
        cfg = zd.Config(
            headless=True,
            user_data_dir=str(self.profile_dir),
            browser_args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        self.browser = await zd.start(cfg)

        # Cookies inject karo (login-form fill karne ki zaroorat nahi)
        params = []
        for part in self.cookie_string.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, value = part.split("=", 1)
            params.append(
                cdp_network.CookieParam(
                    name=name.strip(),
                    value=value.strip(),
                    domain=".heygen.com",
                    url="https://app.heygen.com",
                    secure=True,
                )
            )
        if params:
            await self.browser.cookies.set_all(params)

        self.page = await self.browser.get(HEYGEN_APP_URL)
        return self.page

    async def stop(self):
        if self.browser:
            await self.browser.stop()
            self.browser = None
        # Temp profile cleanup — VPS disk bharne se bachne ke liye
        import shutil
        shutil.rmtree(self.profile_dir, ignore_errors=True)


async def is_logged_in(page) -> bool:
    """Cookie valid hai ya expire ho gayi — real API call se confirm karo
    (sirf URL dekhna false-positive de sakta hai)."""
    import json
    js = """
    (async () => {
        const res = await fetch("https://api2.heygen.com/v1/space.get", {
            credentials: "include"
        });
        return res.status;
    })()
    """
    try:
        status = await page.evaluate(js, await_promise=True)
        return int(status) == 200
    except Exception:
        return False
