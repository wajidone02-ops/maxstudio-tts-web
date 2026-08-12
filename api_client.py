"""
api_client.py — Do tareeke se HeyGen se baat karte hain:

1. browser_fetch()  -> api2.heygen.com wali saari calls, browser ke JS context
                        se (credentials: include) — cookies automatically jaati hain,
                        humein manually cookie header banane ki zaroorat nahi.

2. s3_put_upload()   -> Photo/audio S3 presigned URL pe seedha Python se upload
                        (cookies ki zaroorat nahi, presigned URL khud auth hai) —
                        isse bade files browser JS mein base64 nahi karne padte.
"""
import json

import aiohttp
import certifi
import ssl

import config


class ApiError(Exception):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


# Kuch machines (jaise corporate/antivirus-managed laptops) ke SSL certificate
# store mein AWS ke root CA properly nahi hote — isliye certifi ka apna
# bundled CA-bundle explicitly use karte hain, system pe depend nahi karte.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


async def browser_fetch(page, method: str, path: str, json_body: dict | None = None,
                         extra_headers: dict | None = None) -> dict:
    """
    path: "/v1/..." (API_BASE khud prepend ho jayega)
    Browser ke andar hi fetch() chalata hai taaki session cookies (credentials: include)
    automatically use ho jayein — bilkul render script wala tareeka.
    extra_headers: render calls ke liye heygen-video-id/heygen-script-id jaisi
                   extra headers chahiye hoti hain.
    """
    url = f"{config.API_BASE}{path}"
    body_js = json.dumps(json_body) if json_body is not None else "null"
    extra_headers_js = json.dumps(extra_headers or {})

    js = f"""
    (async () => {{
        const opts = {{
            method: {json.dumps(method)},
            credentials: "include",
            headers: Object.assign({{
                "accept": "application/json, text/plain, */*",
                "x-client-request-id": crypto.randomUUID(),
                "x-language-override": "en",
                "x-heygen-service": "novel"
            }}, {extra_headers_js})
        }};
        const body = {body_js};
        if (body !== null) {{
            opts.headers["content-type"] = "application/json";
            opts.body = JSON.stringify(body);
        }}
        const res = await fetch({json.dumps(url)}, opts);
        const status = res.status;
        const text = await res.text();
        return JSON.stringify({{ status, text }});
    }})()
    """
    raw = await page.evaluate(js, await_promise=True)
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    status = parsed.get("status")
    text = parsed.get("text", "")

    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        data = {"raw": text}

    if status is None or status >= 400:
        raise ApiError(f"{method} {path} failed (status={status})", status=status, body=data)

    # HeyGen ke saare responses {"code":100, "data": {...}, "msg":..., "message":...}
    # wrapper mein aate hain — code 100 = success. Andar wala "data" hi return karo.
    if isinstance(data, dict) and "code" in data:
        if data.get("code") != 100:
            err_msg = data.get("message") or data.get("msg") or f"code={data.get('code')}"
            raise ApiError(f"{method} {path} -> HeyGen error: {err_msg}", status=status, body=data)
        return data.get("data") or {}

    return data


async def s3_put_upload(upload_url: str, file_path: str, content_type: str):
    """
    Presigned S3 URL pe file seedha Python se upload karo.
    x-amz-server-side-encryption: AES256 header zaroori hai — warna signature mismatch
    (presigned URL ke SignedHeaders mein ye included hai, HAR se confirm kiya).
    """
    with open(file_path, "rb") as f:
        data = f.read()

    headers = {
        "Content-Type": content_type,
        "x-amz-server-side-encryption": "AES256",
    }

    connector = aiohttp.TCPConnector(ssl=_SSL_CONTEXT)
    async with aiohttp.ClientSession(connector=connector) as sess:
        async with sess.put(upload_url, data=data, headers=headers) as resp:
            if resp.status not in (200, 201):
                body = await resp.text()
                raise ApiError(f"S3 upload failed (status={resp.status})", status=resp.status, body=body)
