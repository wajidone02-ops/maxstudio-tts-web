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
import mimetypes

from api_client import browser_fetch, s3_put_upload, ApiError


POLL_INTERVAL = 5
MAX_WAIT = 300  # voice cloning mein time lag sakta hai, 5 min tak wait


async def clone_voice_from_audio(
    page, audio_path: str, voice_name: str, language: str = "en", status_cb=None
) -> str:
    """Returns voice_id (string)."""

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

    # Step 3: clone job trigger karo
    await _status("Voice clone shuru ho rahi hai...")
    create_data = await browser_fetch(
        page, "POST", "/v2/voice/voice_clone/create",
        json_body={
            "file_url": file_url,
            "voice_name": voice_name,
            "language": language,
            "is_video": False,
            "request_source": "IVC",
            "remove_background_noise": True,
            "normalize_volume": False,
        },
    )
    job_id = create_data["job_id"]  # ye hi aage voice_id banega

    # Step 4: status poll — pehle voice_id CONFIRM karo (voice.update se pehle
    # zaroori hai, warna 404 aata hai — voice abhi register hi nahi hui hoti)
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

    # Step 5-6: AB engine finalize karo (voice confirm ho chuki hai, ab
    # 404 nahi aayega). Non-fatal — fail ho bhi jaye to voice phir bhi
    # usable hai (default engine ke saath), poora avatar-creation crash
    # nahi hona chahiye isi wajah se.
    await _status("Voice engine finalize kar raha hoon...")
    try:
        options = await browser_fetch(
            page, "GET", f"/v2/pacific/voice_clone/clone-options?voice_id={voice_id}&language={language}-US"
        )
        engines = options.get("engines") or []
        chosen_engine = "elevenLabs" if "elevenLabs" in engines else (engines[0] if engines else "elevenLabs")

        await browser_fetch(
            page, "PATCH", "/v1/voice.update",
            json_body={"voice_id": voice_id, "voice_engine": chosen_engine},
        )
    except ApiError as e:
        await _status(f"Engine finalize skip ho gaya ({e}) — voice phir bhi usable hai.")

    await _status("Voice clone ready hai!")
    return voice_id


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
