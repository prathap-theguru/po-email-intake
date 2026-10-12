"""Pure text helpers (spec sections 4 and 5). No I/O, no model.

Money and quantities are always Decimal, never float.
"""

import re
from datetime import date
from decimal import Decimal

# Trailing words dropped from vendor names, repeatedly (spec section 4).
_VENDOR_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited",
    "corp", "corporation", "co", "company",
}

_SPELLED_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}

# Plain digits, or digits with correctly placed thousands commas;
# optional decimal part of at most 4 places. No sign, so -5 never matches.
_NUMBER_RE = re.compile(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,4})?")

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

_SENDER_MAX_LEN = 64


def normalize_vendor_name(name: str) -> str:
    """Normalize a vendor name for matching.

    Acme Supply Co. -> acme supply; Smith & Sons LLC -> smith and sons.
    """
    s = name.casefold().replace("&", " and ")
    s = "".join(ch if ch.isalnum() else " " for ch in s)
    words = s.split()  # also collapses whitespace and trims
    while len(words) > 1 and words[-1] in _VENDOR_SUFFIXES:
        words.pop()  # never reduces the name to empty: "Co" stays "co"
    return " ".join(words)


def sanitize_sender(address: str) -> str:
    """Turn a sender address into a safe file-name part.

    jane.doe@example.com -> jane.doe_at_example.com. The result never
    contains a path separator.
    """
    s = address.lower().replace("@", "_at_")
    s = re.sub(r"[^a-z0-9._-]", "_", s)
    s = s.lstrip(".")
    return s[:_SENDER_MAX_LEN]


def parse_number(text: str) -> Decimal | None:
    """Parse a quantity or unit price. Returns None if it can't be used.

    Accepts 1,250.50, 20, or a spelled-out whole number from one to twenty.
    Rejects zero, negatives, more than 4 decimals, and anything else.
    """
    s = text.strip()
    word = _SPELLED_NUMBERS.get(s.casefold())
    if word is not None:
        return Decimal(word)
    if not _NUMBER_RE.fullmatch(s):
        return None
    value = Decimal(s.replace(",", ""))
    return value if value > 0 else None


def parse_date(text: str) -> date | None:
    """Parse a YYYY-MM-DD date. Returns None if it isn't a real date in that form."""
    s = text.strip()
    if not _DATE_RE.fullmatch(s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:  # e.g. 2026-02-30
        return None
