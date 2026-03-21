"""
Trivasta Contact Guard — Hardened
Blocks: phone numbers, emails, UPI IDs, WhatsApp refs, Telegram, Instagram,
        external booking platforms, and coded evasion attempts.
"""

import re

# ── Compiled patterns ─────────────────────────────────────────────────────────

_PHONE = re.compile(
    r"""
    (?<!\w)                      # not part of a longer word
    (?:\+91[\s\-\.]?)?           # optional +91
    [6-9]\d{4}                   # first 5 digits (Indian mobile)
    [\s\-\.\u00b7\u2022]*        # separators (including unicode dots/bullets)
    \d{5}                        # last 5 digits
    (?!\w)
    """,
    re.VERBOSE,
)

_PHONE_SPACED = re.compile(
    r"(?<!\d)(\d[\s\.\-]{0,2}){9,11}(?!\d)"   # spaced-out digits
)

_EMAIL = re.compile(
    r"[a-zA-Z0-9_.+\-]{2,}\s*[@＠at]\s*[a-zA-Z0-9\-]{2,}\s*[.,]\s*[a-zA-Z]{2,6}"
)

_UPI = re.compile(
    r"[a-zA-Z0-9.\-_+]{2,}@(?:okaxis|okhdfcbank|okicici|oksbi|upi|paytm|"
    r"ybl|ibl|axl|waicici|apl|mbk|timecosmos|slice|freecharge|"
    r"naviaxis|juspay|razorpay|[a-zA-Z]{2,})",
    re.IGNORECASE,
)

_WHATSAPP = re.compile(
    r"\bwhat\s*s?\s*app\b|\bwa\.me\b|\bwhtspp\b|\bw\.app\b",
    re.IGNORECASE,
)

_TELEGRAM = re.compile(
    r"\bt\.me\b|\btelegram\b|\btele\s*gram\b|\b@[a-zA-Z0-9_]{3,}\b",
    re.IGNORECASE,
)

_INSTAGRAM = re.compile(
    r"\binstagram\b|\binsta\b|\big\s*:\s*@?\w+|\b@[a-zA-Z0-9_.]+\s",
    re.IGNORECASE,
)

_EXTERNAL_PLATFORMS = re.compile(
    r"\b(?:makemytrip|mmt|cleartrip|yatra|booking\.com|airbnb|tripadvisor|"
    r"goibibo|ixigo|easemytrip|thrillophilia|holidify)\b",
    re.IGNORECASE,
)

_CODED_EVASION = re.compile(
    # "call me", "message me", "text me", "reach me", "contact me" + number hints
    r"\b(?:call|msg|message|text|reach|ping|dm|contact)\s+(?:me|us|directly|outside|"
    r"on\s+(?:whatsapp|telegram|insta)|at\s+\d)",
    re.IGNORECASE,
)

_SHARE_SIGNAL = re.compile(
    r"\b(?:my\s+number|my\s+email|my\s+contact|share\s+(?:my|our)\s+(?:number|email|contact|details)|"
    r"send\s+(?:me\s+)?(?:your|ur)\s+(?:number|email|contact|whatsapp))\b",
    re.IGNORECASE,
)

_ALL_PATTERNS = [
    ("phone",             _PHONE),
    ("phone_spaced",      _PHONE_SPACED),
    ("email",             _EMAIL),
    ("upi",               _UPI),
    ("whatsapp",          _WHATSAPP),
    ("telegram",          _TELEGRAM),
    ("instagram",         _INSTAGRAM),
    ("external_platform", _EXTERNAL_PLATFORMS),
    ("coded_evasion",     _CODED_EVASION),
    ("share_signal",      _SHARE_SIGNAL),
]

# ── Public API ────────────────────────────────────────────────────────────────

def is_violation(text: str) -> bool:
    """Return True if the message contains any guarded pattern."""
    for _, pattern in _ALL_PATTERNS:
        if pattern.search(text):
            return True
    return False


def classify_violation(text: str) -> str:
    """Return the category of the first violation found."""
    for name, pattern in _ALL_PATTERNS:
        if pattern.search(text):
            return name
    return "unknown"


def get_all_violations(text: str) -> list[str]:
    """Return all violation categories found in the text."""
    return [name for name, pattern in _ALL_PATTERNS if pattern.search(text)]


def redact(text: str) -> str:
    """
    Return the text with all detected violations redacted.
    Useful for logging without storing sensitive data.
    """
    result = text
    for _, pattern in _ALL_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result