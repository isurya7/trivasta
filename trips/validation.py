"""Single source of truth for trip-input limits and text cleaning."""
import re

# ── Limits ────────────────────────────────────────────────────────────────
MIN_PER_PERSON = 8000        # ₹ per person
MIN_GROUP_TOTAL = 20000      # ₹ total, when more than one traveller
MIN_DAYS = 1
MAX_DAYS = 30
MIN_PEOPLE = 1
MAX_PEOPLE = 50
MAX_BUDGET = 10_000_000      # ₹1 crore sanity cap on the amount entered
MAX_PREFS_LEN = 500

# Control characters except tab (\x09) and newline (\x0a).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Any control character or whitespace run (used for single-line fields).
_CONTROL_OR_WS = re.compile(r"[\x00-\x1f\x7f\s]+")


def clean_single_line(value: str) -> str:
    """For destination/origin: one line, collapsed whitespace, no control chars."""
    return _CONTROL_OR_WS.sub(" ", value or "").strip()


def clean_multiline(value: str) -> str:
    """For free-text preferences: keep line breaks, drop control characters."""
    value = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    value = _CONTROL_CHARS.sub("", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()