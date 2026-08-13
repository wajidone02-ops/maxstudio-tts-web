"""
voice_clone.py — Audio sample se HeyGen voice clone banana.

Flow (HAR se reverse-engineered, do baar verify kiya — avatar-flow aur
standalone Voice-page flow dono se, dono same core endpoints use karte hain):
  1. POST /v1/pacific/voice_clone/voice.get_upload_url        -> presigned S3 URL
  2. PUT  seedha S3 pe audio bytes
  3. POST /v2/voice/voice_clone/create                        -> job_id (= voice_id)
  4. GET  /v2/pacific/voice_clone/clone-options                -> available engines
  5. PATCH /v1/voice.update                                    -> engine finalize
  6. Poll GET /v1/voice/voice_clone/create_status?job_id=...  -> voice_id confirm
"""
import asyncio
import json
import mimetypes

from api_client import browser_fetch, s3_put_upload, ApiError


POLL_INTERVAL = 5
MAX_WAIT = 300  # voice cloning mein time lag sakta hai, 5 min tak wait


async def clone_voice_from_audio(
    page, audio_path: str, voice_name: str, language: str = "en",
    remove_background_noise: bool = True, status_cb=None
) -> dict:
    """
    Phase A: upload + create + poll till voice_id confirm + available engines
    list nikaalta hai. ENGINE FINALIZE NAHI karta — wo Phase C mein alag se
    (finalize_voice_clone) hota hai, jab user 3 options mein se ek choose
    kar le (preview sun ke).

    Returns: {"voice_id": str, "engines": [str, ...]}
    """

    async def _status(msg):
        if status_cb:
            await status_cb(msg)

    content_type = mimetypes.guess_type(audio_path)[0] or "audio/wav"

    # Step 1: presigned upload URL maango
    await _status("Voice sample upload URL maang raha hoon...")
    upload_data = await browser_fetch(
        page, "POST", "/v1/pacific/voice_clone/voice.get_upload_url",
        json_body={"is_video": False, "request_source": "IVC"},
    )
    upload_url = upload_data["file_upload_url"]
    file_url = upload_data["file_url"]  # s3://...

    # Step 2: seedha S3 pe upload
    await _status("Voice sample S3 pe upload ho raha hai...")
    await s3_put_upload(upload_url, audio_path, content_type)

    # Step 3: clone job trigger karo (verified via HAR: enable_source_review bhi chahiye)
    await _status("Voice clone shuru ho rahi hai...")
    create_data = await browser_fetch(
        page, "POST", "/v2/voice/voice_clone/create",
        json_body={
            "file_url": file_url,
            "voice_name": voice_name,
            "language": language,
            "is_video": False,
            "request_source": "IVC",
            "remove_background_noise": remove_background_noise,
            "normalize_volume": False,
            "enable_source_review": False,
        },
    )
    job_id = create_data["job_id"]

    # Step 4: status poll — voice_id CONFIRM karo
    await _status("Voice clone process ho rahi hai (thoda time lagega)...")
    voice_id = None
    elapsed = 0
    while elapsed < MAX_WAIT:
        status_data = await browser_fetch(
            page, "GET", f"/v1/voice/voice_clone/create_status?job_id={job_id}"
        )
        if status_data.get("error_msg"):
            raise ApiError(f"Voice clone fail ho gayi: {status_data['error_msg']}")
        voice_id = status_data.get("voice_id")
        if voice_id:
            break
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    if not voice_id:
        raise ApiError("Voice clone ready hone ka timeout ho gaya (5 min).")

    # Step 5: available engines list nikaalo (HeyGen HAR se confirmed:
    # e.g. ["elevenLabsV3", "fish", "elevenLabs"] — dynamic ho sakti hai)
    await _status("Voice engines fetch kar raha hoon...")
    engines = await get_clone_engines(page, voice_id, language)

    await _status("Preview ke liye ready — koi engine choose karo.")
    return {"voice_id": voice_id, "engines": engines}


async def get_clone_engines(page, voice_id: str, language: str = "en") -> list[str]:
    """HAR-verified: GET /v2/pacific/voice_clone/clone-options -> engines list."""
    options = await browser_fetch(
        page, "GET", f"/v2/pacific/voice_clone/clone-options?voice_id={voice_id}&language={language}-US"
    )
    return options.get("engines") or []


async def fetch_voice_preview_bytes(page, voice_id: str, voice_engine: str, language: str = "en") -> bytes:
    """
    VERIFIED (real HAR, ChatGPT-cross-checked): POST /v2/online/voice.stream_preview
    Response NDJSON hai (multi-line). Har line: {"sequence_number":N, "audio_bytes":"<base64>",
    "format":"mp3", ...}. Confirmed decoded bytes MP3 frame se start hote hain (FF FB...).

    Chunk count engine ke hisaab se alag hota hai (elevenLabs ~29-30 chunks,
    fish ~3-4) — isliye fixed count assume nahi karna. Har line ka audio_bytes
    ALAG-ALAG decode karke append karo (concatenate-then-decode NAHI —
    base64 padding chunk-boundary pe galat ban sakti hai). Khali audio_bytes
    ("") skip karo. sequence_number == -1 wali line final marker hai (usme
    audio_bytes khali hota hai, sirf audio_url — jo hum ignore kar sakte hain
    kyunki humare paas already poore decoded bytes hain).
    """
    import base64

    result = await browser_fetch(
        page, "POST", "/v2/online/voice.stream_preview",
        json_body={"voice_id": voice_id, "language": language, "voice_engine": voice_engine},
    )
    text_body = result.get("raw", "") if isinstance(result, dict) else ""

    audio_parts = []
    for line in text_body.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue

        b64 = chunk.get("audio_bytes")
        if b64:  # khali string ("") skip
            audio_parts.append(base64.b64decode(b64))

        if chunk.get("sequence_number") == -1:
            break  # final marker — stream khatam

    if not audio_parts:
        raise ApiError(f"Preview response mein audio_bytes nahi mila: {text_body[:300]}")

    return b"".join(audio_parts)


async def finalize_voice_clone(page, voice_id: str, voice_engine: str):
    """Phase C: user ke chosen engine ko confirm karta hai (HAR-verified: /v1/voice.update)."""
    await browser_fetch(
        page, "PATCH", "/v1/voice.update",
        json_body={"voice_id": voice_id, "voice_engine": voice_engine},
    )


async def list_account_voices(page) -> list[dict]:
    """
    Account mein already-cloned SAARI voices HeyGen se seedha fetch karta hai.
    Returns: [{"voice_id": str, "name": str}, ...]
    """
    data = await browser_fetch(
        page, "GET", "/v2/pacific/voice_clone/voice.list",
        extra_headers={"x-path": "/avatar/voices", "x-ver": "4.1.0"},
    )
    voices = (data or {}).get("data") or []
    result = []
    for v in voices:
        name = v.get("display_name") or v.get("voice_name") or v.get("name") or v.get("voice_id", "")
        result.append({"voice_id": v.get("voice_id"), "name": name})
    return result
