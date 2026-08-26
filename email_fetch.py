"""
email_fetch.py — Secondary-account emails se OTP code / magic-link fetch
karna (IMAP se, read-only-ish — sirf padhते hain, HeyGen ke emails ko
"seen" mark NAHI karte — BODY.PEEK use karte hain, jaanbujh ke, kyunki
plain RFC822 fetch email ko "read" mark kar deta hai aur agli baar UNSEEN
search mein wo mil hi nahi paata — real bug tha, 26 Aug ko confirm hua).

Account-recycle flow (delete + signup) ke liye zaroori — email-access
already CONFIRMED working hai (25 Aug test).
"""
import asyncio
import email as email_lib
import imaplib
import re
import ssl
from collections import Counter

_SSL_CONTEXT = ssl.create_default_context()

OTP_POLL_INTERVAL = 5
OTP_MAX_WAIT = 90  # HeyGen ka OTP email aane mein kitni der lag sakti hai

# Clean 6-digit token (jab digits aapas mein directly juде hue hon)
_OTP_RE = re.compile(r"\b(\d{6})\b")
# HeyGen ke deletion-OTP email mein code EK SPECIFIC styled <div> ke andar
# hota hai — class name mein 'verification-code' hamesha hota hai (Wajid ne
# actual HTML confirm kiya, 26 Aug). Ye SABSE PRECISE tareeka hai — seedha
# usी div ke andar se code nikalta hai, baaki poore email ke junk/tracking
# numbers ko chhuta hi nahi. Class ke aage kabhi Gmail apna "m_XXXXX" hash-
# prefix bhi laga deta hai, isliye 'contains verification-code' match karta
# hai, exact-match nahi.
_VERIFICATION_BOX_RE = re.compile(
    r'class="[^"]*verification-code[^"]*"[^>]*>\s*(\d{6})\s*<',
    re.IGNORECASE,
)
# Digits jinke BEECH mein spaces/invisible-characters ho sakte hain — HeyGen
# ke verification-code email mein har digit ALAG <span> mein hota hai
# (styling ke liye), isliye text-extraction mein '2 2 4 1 0 3' jaisa aata
# hai, seedha '224103' nahi. Confirmed bug (26 Aug) — clean regex ne isse
# miss kar diya tha, sirf junk (tracking-ID) numbers candidate bane the.
_SPACED_DIGIT_RE = re.compile(r"(?:\d[\s\u00a0\u200b\u200c\u200d\u2060]*){6}")
_LINK_RE = re.compile(r'https://auth\.heygen\.com/magic-web/[^\s"\'<>]+')


def _strip_invisible_chars(s: str) -> str:
    """Copy-paste se aaye invisible unicode characters hatana (25 Aug se
    confirmed issue — App Password/email mein chipak jaate the)."""
    for ch in ("\u200e", "\u200f", "\u200b", "\u200c", "\u200d", "\ufeff"):
        s = s.replace(ch, "")
    return s.strip()


def _connect(email_addr: str, app_password: str) -> imaplib.IMAP4_SSL:
    email_addr = _strip_invisible_chars(email_addr)
    app_password = _strip_invisible_chars(app_password)
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=_SSL_CONTEXT)
    imap.login(email_addr, app_password)
    return imap


def _search_unseen_from_heygen(imap: imaplib.IMAP4_SSL) -> list[bytes]:
    imap.select("INBOX")
    status, msg_nums = imap.search(None, '(UNSEEN FROM "heygen")')
    if status != "OK" or not msg_nums[0]:
        return []
    ids = msg_nums[0].split()
    ids.reverse()  # SABSE NAYA pehle check karo (purani stale unseen emails skip)
    return ids


def _get_body_parts(imap: imaplib.IMAP4_SSL, msg_id: bytes) -> tuple[str, str]:
    """
    Returns (plain_text, html_text) ALAG-ALAG — HTML mein tracking-pixel/
    unsubscribe-link jaise hidden 6-digit numbers hote hain jo galti se OTP
    samajh liye jaate the (confirmed bug — '000000'/'170711' galat match
    hue the).

    BODY.PEEK use karte hain — (RFC822) NAHI — kyunki RFC822 fetch email ko
    "\\Seen" mark kar deta hai turant. Agar us turn pe extraction fail ho
    (jaisa spaced-digit bug ki wajah se hua), email agli baar UNSEEN search
    mein milta hi nahi tha — isliye magic-link 90s timeout ho raha tha
    jabki email actually turant aa chuki thi (26 Aug confirmed bug).
    """
    status, msg_data = imap.fetch(msg_id, "(BODY.PEEK[])")
    if status != "OK" or not msg_data or not msg_data[0]:
        return "", ""
    msg = email_lib.message_from_bytes(msg_data[0][1])
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            try:
                payload = part.get_payload(decode=True).decode(errors="ignore")
            except Exception:
                continue
            if ctype == "text/plain":
                plain += payload
            elif ctype == "text/html":
                html += payload
    else:
        try:
            plain = msg.get_payload(decode=True).decode(errors="ignore")
        except Exception:
            pass
    return plain, html


def _find_otp_candidates(text: str) -> list[str]:
    """
    SABSE PEHLE 'verification-code' CSS-class wale div se seedha extract
    karta hai — ye SABSE PRECISE hai (Wajid ne actual email-HTML confirm
    kiya, 26 Aug), baaki email ke junk-numbers (tracking-pixels waghera) ko
    chhuta hi nahi. Isse candidate mila to seedha wahi return.

    Na mile (HeyGen ne template badal di ho) to purane fallback tareeke:
    spaced-digit pattern, phir frequency-based clean-6-digit scan.
    """
    class_matches = _VERIFICATION_BOX_RE.findall(text)
    if class_matches:
        seen = []
        for c in class_matches:
            if len(set(c)) > 1 and c not in seen:
                seen.append(c)
        if seen:
            return seen

    spaced_codes = []
    for m in _SPACED_DIGIT_RE.finditer(text):
        digits_only = re.sub(r"\D", "", m.group(0))
        if len(digits_only) == 6 and len(set(digits_only)) > 1:
            spaced_codes.append(digits_only)
    if spaced_codes:
        # Duplicates hata do, order (pehla-mila-pehla) rakho.
        seen = []
        for c in spaced_codes:
            if c not in seen:
                seen.append(c)
        return seen

    # Fallback: clean 6-digit, frequency-sorted (purana tareeka).
    raw = _OTP_RE.findall(text)
    filtered = [c for c in raw if len(set(c)) > 1]
    counts = Counter(filtered)
    return sorted(counts.keys(), key=lambda c: counts[c])


def _mark_seen(imap: imaplib.IMAP4_SSL, msg_id: bytes):
    """Email SUCCESSFULLY use ho chuki (OTP/link nikal liya) — ab isko
    explicitly 'read' mark karo, taake future runs mein ye purani email
    dobara na uthe (confusion/galat-code-uthane ka risk khatam)."""
    try:
        imap.store(msg_id, "+FLAGS", "\\Seen")
    except Exception as e:
        print(f"[email_fetch] mark-as-seen fail (non-fatal): {e}")


async def fetch_otp_code(email_addr: str, app_password: str) -> str:
    """
    HeyGen ke deletion-OTP email ka wait karta hai (poll karke), 6-digit
    code return karta hai. Timeout ho to RuntimeError raise karta hai.
    """
    elapsed = 0
    while elapsed < OTP_MAX_WAIT:
        imap = _connect(email_addr, app_password)
        try:
            for msg_id in _search_unseen_from_heygen(imap):
                plain, html = _get_body_parts(imap, msg_id)
                candidates = _find_otp_candidates(plain)
                source = "plain-text"
                if not candidates:
                    candidates = _find_otp_candidates(html)
                    source = "html (fallback)"
                if candidates:
                    if len(candidates) > 1:
                        print(f"[email_fetch] OTP ke {len(candidates)} candidates mile ({source}): {candidates} — pehla use kar raha hoon.")
                    _mark_seen(imap, msg_id)
                    return candidates[0]
        finally:
            imap.logout()
        await asyncio.sleep(OTP_POLL_INTERVAL)
        elapsed += OTP_POLL_INTERVAL
    raise RuntimeError(f"OTP email {OTP_MAX_WAIT}s mein nahi mila ({email_addr}).")


async def fetch_magic_link(email_addr: str, app_password: str) -> str:
    """
    HeyGen ke signup magic-link email ka wait karta hai, poora link URL
    return karta hai. Timeout ho to RuntimeError raise karta hai.
    """
    elapsed = 0
    while elapsed < OTP_MAX_WAIT:
        imap = _connect(email_addr, app_password)
        try:
            for msg_id in _search_unseen_from_heygen(imap):
                plain, html = _get_body_parts(imap, msg_id)
                match = _LINK_RE.search(plain) or _LINK_RE.search(html)
                if match:
                    _mark_seen(imap, msg_id)
                    return match.group(0)
        finally:
            imap.logout()
        await asyncio.sleep(OTP_POLL_INTERVAL)
        elapsed += OTP_POLL_INTERVAL
    raise RuntimeError(f"Magic-link email {OTP_MAX_WAIT}s mein nahi mila ({email_addr}).")
