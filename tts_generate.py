"""
tts_generate.py — Har naye text ke liye real TTS audio generate karna.

CONFIRMED by user's working console script: TTS endpoint ko render wale headers
se ALAG headers chahiye — x-heygen-service: "voice" (render mein "novel" hota
hai), plus x-path/x-ver/x-last-build/heygen-video-id/heygen-script-id.
Payload bhi zyada fields maangta hai (rate, pitch, settings, preview,
force_regenerate, product_source, generation_type) — sab niche match kiya hai.

ENHANCED DELIVERY (verified 14 Jul via HAR — enhanced.har + longend.har):
  1. Pehle /v1/online/enhance_script_text.create call karo plain text ke saath
     -> HeyGen khud script mein "[emotion]" labels insert kar deta hai
     (jaise [sad], [cold], [bitter]) — enhance_script_text() function.
  2. Us bracket-label-wale text ko SSML mein convert karo (build_enhanced_ssml)
     -> [sad] ban jaata hai <markup value="sad"/>, baaki text XML-escaped.
  3. TTS request mein text_type="ssml" + has_enhanced_markup_tags=true bhejo.
     ZAROORI: voice_engine="auto" bhejo (na ke "elevenLabs") — dono working
     HARs "auto" use karte hain. Forced "elevenLabs" markup-capable engine pe
     route NAHI hota, isliye <markup> emotion tags perform hone ke bajaye
     read/ignore ho jaate hain (banda "[curious]" bol deta hai). "auto" HeyGen
     ko markup-capable engine (elevenLabs enhanced/V3) pe route karne deta hai.
  4. Render payload mein bhi has_enhanced_markup_tags=true set karna hai
     (render.py mein), aur script ka "text" field ORIGINAL bracket-text
     rakhna hai (SSML nahi) — HAR mein yehi confirm hua tha.
"""
import json
import re

import config

# NOTE: x-ver aur x-last-build HeyGen ke current app build se hardcoded liye
# hain (working console script se). Agar future mein HeyGen naya build deploy
# kare aur ye stale ho jayein, TTS call fail ho sakti hai — tab in values ko
# fresh HAR se update karna hoga.
X_VER = "4.1.0"
X_LAST_BUILD = "1783453057492"

TTS_JS_TEMPLATE = """
(async () => {{
    const res = await fetch({url!r}, {{
        method: "POST",
        credentials: "include",
        headers: {{
            "accept": "*/*",
            "content-type": "application/json",
            "x-client-request-id": crypto.randomUUID(),
            "x-language-override": "en-US",
            "x-heygen-service": "voice",
            "x-path": {x_path!r},
            "x-ver": {x_ver!r},
            "x-last-build": {x_last_build!r},
            "heygen-video-id": {video_id!r},
            "heygen-script-id": {script_id!r}
        }},
        body: JSON.stringify({body})
    }});
    const status = res.status;
    const text = await res.text();
    return JSON.stringify({{ status, text }});
}})()
"""

# ─── Enhanced delivery: script-enhance call ───────────────────────────────

ENHANCE_JS_TEMPLATE = """
(async () => {{
    const res = await fetch({url!r}, {{
        method: "POST",
        credentials: "include",
        headers: {{
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "x-language-override": "en-US",
            "x-path": {x_path!r},
            "x-ver": {x_ver!r},
            "x-last-build": {x_last_build!r}
        }},
        body: JSON.stringify({{ text: {text!r} }})
    }});
    const status = res.status;
    const text = await res.text();
    return JSON.stringify({{ status, text }});
}})()
"""


async def enhance_script_text(page, text: str, video_id: str) -> str:
    """
    HeyGen ke apne 'Enhance Script' feature ko call karta hai — plain text
    deta hai, wapas usi text mein '[emotion]' delivery-labels lage hue milte
    hain (jaise '[sad]', '[cold]', '[bitter]'). Verified real HAR se
    (endpoint: /v1/online/enhance_script_text.create).

    Returns: enhanced_text (str) — bracket-labels ke saath, SSML/markup mein
    convert NAHI hua hai abhi, wo build_enhanced_ssml() alag se karta hai.
    """
    url = f"{config.API_BASE}/v1/online/enhance_script_text.create"
    js = ENHANCE_JS_TEMPLATE.format(
        url=url,
        x_path=f"/create-v4/{video_id}",
        x_ver=X_VER,
        x_last_build=X_LAST_BUILD,
        text=text,
    )
    raw = await page.evaluate(js, await_promise=True)
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    status = parsed.get("status")
    text_body = parsed.get("text", "")

    if status != 200:
        raise RuntimeError(f"Enhance-script fail (status={status}): {text_body[:300]}")

    try:
        data = json.loads(text_body)
    except json.JSONDecodeError:
        raise RuntimeError(f"Enhance-script response JSON parse nahi hui: {text_body[:300]}")

    enhanced_text = (data.get("data") or {}).get("enhanced_text")
    if not enhanced_text:
        raise RuntimeError(f"Enhance-script response mein 'enhanced_text' nahi mila: {text_body[:300]}")

    return enhanced_text


# ─── Enhanced delivery: SSML conversion helpers ───────────────────────────

_DELIVERY_LABEL_RE = re.compile(r"\[(\w+)\]")


def convert_delivery_labels_to_markup(text: str) -> str:
    """'[sad]' -> '<markup value="sad"/>' — text pehle hi XML-escaped hona
    chahiye jab ye call ho (escape sirf &<>\" ko touch karta hai, brackets
    ko nahi, isliye order sahi rehta hai)."""
    return _DELIVERY_LABEL_RE.sub(r'<markup value="\1"/>', text)


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_enhanced_ssml(text: str, voice_id: str, rate: float = 1, pitch: int = 0) -> str:
    """
    Bracket-label-wala text (jo enhance_script_text() se aaya) SSML mein
    convert karta hai. Verified format (HAR se):
        <speak><voice name="VOICE_ID"><prosody rate="1" pitch="0%">
            ...text... <markup value="sad"/> ...text...
        </prosody></voice></speak>
    """
    escaped = _xml_escape(text)
    marked = convert_delivery_labels_to_markup(escaped)
    return f'<speak><voice name="{voice_id}"><prosody rate="{rate}" pitch="{pitch}%">{marked}</prosody></voice></speak>'


async def generate_tts_audio(
    page, text: str, voice_id: str, video_id: str, script_id: str,
    voice_engine: str = "auto", seed: int | None = None,
    previous_text: str = "", next_text: str = "", force_regenerate: bool = False,
    enhanced: bool = False, avatar_id: str | None = None,
) -> dict:
    """
    Returns: {"url": str, "duration": float, "words": [...], "seed": int}
    words list mein <start> aur <end> markers included hain.

    previous_text/next_text: multi-chunk (lambe) scripts mein continuity ke liye —
    CONFIRMED real HAR se: pehle chunk mein previous_text key hi nahi hoti, aakhri
    chunk mein next_text key hi nahi hoti (empty string nahi — poori key missing).
    force_regenerate: real multi-chunk flow mein False confirmed hua.

    enhanced: True ho to 'text' already enhance_script_text() se aaya bracket-
    label-wala text hona chahiye — ye function usse khud SSML mein convert
    kar dega. avatar_id: enhanced mode mein HAR ke payload mein present tha
    (normal mode mein nahi dekha gaya) — safety ke liye pass karo agar pata ho.
    """
    import secrets
    if seed is None:
        seed = secrets.randbelow(999_999_999)

    url = f"{config.API_BASE}/v2/online/text_to_speech.stream"

    if enhanced:
        ssml_text = build_enhanced_ssml(text, voice_id)
        body = {
            "text_type": "ssml",
            "text": ssml_text,
            "voice_id": voice_id,
            "rate": 1,
            "pitch": 0,
            "settings": {
                "pitch": 0,
                "speed": 1,
                "volume": 1,
                "voice_engine_settings": {
                    "seed": seed,
                    "director_style": None,
                    "is_starfish_sts": False,
                    "has_enhanced_markup_tags": True,
                },
            },
            "with_timestamps": True,
            "preview": True,
            "video_id": video_id,
            "force_regenerate": force_regenerate,
            "script_id": script_id,
            "product_source": "avatar_video",
            "generation_type": "preview",
            "voice_engine": "auto",
        }
        if avatar_id:
            body["avatar_id"] = avatar_id
    else:
        body = {
            "text_type": "text",
            "text": text,
            "voice_id": voice_id,
            "rate": 1,
            "pitch": 0,
            "settings": {
                "pitch": 0,
                "speed": 1,
                "volume": 1,
                "voice_engine_settings": {"engine_type": voice_engine, "seed": seed, "has_enhanced_markup_tags": False},
            },
            "with_timestamps": True,
            "preview": True,
            "video_id": video_id,
            "force_regenerate": force_regenerate,
            "script_id": script_id,
            "product_source": "avatar_video",
            "generation_type": "preview",
            "voice_engine": voice_engine,
        }

    if previous_text:
        body["previous_text"] = previous_text
    if next_text:
        body["next_text"] = next_text

    js = TTS_JS_TEMPLATE.format(
        url=url,
        x_path=f"/create-v4/{video_id}",
        x_ver=X_VER,
        x_last_build=X_LAST_BUILD,
        video_id=video_id,
        script_id=script_id,
        body=json.dumps(body),
    )
    raw = await page.evaluate(js, await_promise=True)
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    status = parsed.get("status")
    text_body = parsed.get("text", "")

    if status != 200:
        raise RuntimeError(f"TTS stream fail (status={status}): {text_body[:300]}")

    audio_url = None
    duration = None
    raw_words = []

    for line in text_body.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        if chunk.get("audio_url"):
            audio_url = chunk["audio_url"]
        if chunk.get("url"):
            audio_url = chunk["url"]
        if isinstance(chunk.get("duration"), (int, float)):
            duration = chunk["duration"]
        if isinstance(chunk.get("audio_duration"), (int, float)):
            duration = chunk["audio_duration"]

        timestamps = chunk.get("word_timestamps") or chunk.get("words") or []
        for item in timestamps:
            word = item.get("word") or item.get("text") or ""
            start = item.get("start_time", item.get("start", 0))
            end = item.get("end_time", item.get("end", start))
            if word:
                raw_words.append({"word": word, "start_time": start, "end_time": end})

    if not audio_url:
        raise RuntimeError(f"TTS audio_url nahi mila. Raw tail: {text_body[-300:]}")

    if duration is None:
        duration = raw_words[-1]["end_time"] if raw_words else 3

    words = [{"word": "<start>", "start_time": 0, "end_time": 0}]
    for w in raw_words:
        if w["word"] not in ("<start>", "<end>"):
            words.append(w)
    words.append({"word": "<end>", "start_time": duration, "end_time": duration})

    return {"url": audio_url, "duration": duration, "words": words, "seed": seed, "voice_id": voice_id}
