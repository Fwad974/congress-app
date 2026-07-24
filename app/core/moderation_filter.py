"""
Lightweight content auto-flagging.

``scan(text)`` returns a list of reason tags — ``profanity``, ``spam``,
``external link`` — used to auto-create a moderation report so flagged content
surfaces in the moderator queue (it is *flagged*, not blocked). Intentionally
simple and dependency-free; tune the word/pattern lists for your event.
"""
import re
from typing import List

# Mild, family-conference-oriented profanity list (kept short on purpose).
_PROFANITY = {
    "damn", "crap", "bastard", "bitch", "asshole", "dick", "piss",
    "fuck", "shit", "cunt", "slut", "whore", "fag", "retard",
}

# Obvious promotional / scam spam phrases.
_SPAM_PHRASES = [
    "buy now", "click here", "free money", "make money fast", "work from home",
    "act now", "limited offer", "100% free", "risk free", "casino", "viagra",
    "crypto giveaway", "double your", "wire transfer", "nigerian prince",
    "you have won", "congratulations you", "get rich",
]

_URL_RE = re.compile(r"(https?://|www\.)", re.IGNORECASE)
# Bare domains like "buy.example.com" or "deals.ru".
_DOMAIN_RE = re.compile(
    r"\b[a-z0-9][a-z0-9-]*\.(?:com|net|org|io|ru|xyz|info|biz|shop|top|link|"
    r"click|online|site|store|app|co)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z']+", re.IGNORECASE)


def _has_profanity(text: str) -> bool:
    return any(w.lower() in _PROFANITY for w in _WORD_RE.findall(text))


def _has_link(text: str) -> bool:
    return bool(_URL_RE.search(text) or _DOMAIN_RE.search(text))


def _looks_spammy(text: str) -> bool:
    low = text.lower()
    if any(p in low for p in _SPAM_PHRASES):
        return True
    letters = [c for c in text if c.isalpha()]
    # Shouty: mostly caps in a message of reasonable length.
    if len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        return True
    # Character spam: the same char repeated 6+ times ("!!!!!!", "soooooo").
    if re.search(r"(.)\1{5,}", text):
        return True
    return False


def scan(text: str) -> List[str]:
    """Return the reason tags that fire for ``text`` (possibly empty)."""
    text = text or ""
    reasons = []
    if _has_profanity(text):
        reasons.append("profanity")
    if _has_link(text):
        reasons.append("external link")
    if _looks_spammy(text):
        reasons.append("spam")
    return reasons


def describe(reasons: List[str]) -> str:
    return "Auto-flagged: " + ", ".join(reasons) if reasons else ""
