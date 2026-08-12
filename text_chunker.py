"""
text_chunker.py — Lambe scripts ko HeyGen ke TTS 5000-char limit ke andar
chunks mein todta hai, sentence boundaries pe (beech mein nahi kaatta).

NOTE: Ye WEB/TTS-only project hai (koi avatar-video motion nahi banti) —
isliye yahan sirf HeyGen ki 5000-char TTS-limit ka hisaab rakhna hai.
(Doosre project — HeyGen avatar-video wale — mein chunk size 980 chars
rakhi gayi thi kyunki wahan avatar_iii motion-engine ke gestures chhote
chunks pe hi sahi bante hain. Yahan motion ka koi concept nahi hai,
isliye wo 980 wali size copy NAHI karni.)
"""
import re

MAX_CHUNK_CHARS = 4500  # 5000 se safe margin

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def split_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    sentences = _SENTENCE_SPLIT_RE.split(text)
    chunks = []
    current = ""

    for sentence in sentences:
        # Agar ek hi sentence itna lamba hai ke akela hi limit se bada hai
        # (rare case), usse hard-split karna padega.
        if len(sentence) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            for i in range(0, len(sentence), max_chars):
                chunks.append(sentence[i:i + max_chars].strip())
            continue

        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > max_chars:
            chunks.append(current.strip())
            current = sentence
        else:
            current = candidate

    if current:
        chunks.append(current.strip())

    return [c for c in chunks if c]
