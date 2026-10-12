"""One email parser, shared by the Gmail poller and the tests (spec sections 3 and 6).

Standard library only. Attachments are ignored; only body text is used.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from po_intake.refdata import sender_address

# Tags that end a line of text when HTML is converted.
_BLOCK_TAGS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}


@dataclass(frozen=True)
class ParsedEmail:
    message_id: str  # Message-ID header, or a SHA-256 of the raw bytes if missing
    from_headers: list[str]  # every raw From value, for the sender check
    sender: str | None  # the single From address, lowercased; None if not exactly one
    subject: str
    received_at: datetime | None  # from the Date header; None means use the IMAP internal date
    body: str  # text/plain parts joined, else text from text/html; "" if none


class _HTMLText(HTMLParser):
    """Collect visible text from HTML, dropping script and style."""

    def __init__(self):
        super().__init__()  # convert_charrefs=True decodes &amp; etc.
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._parts).splitlines())
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    parser.close()
    return parser.text()


def _parse(raw: bytes) -> EmailMessage:
    return message_from_bytes(raw, policy=policy.default)


def _raw_from_headers(msg: EmailMessage) -> list[str]:
    # raw_items() gives the header values as received, before the policy
    # re-renders addresses, so the sender check sees what the sender wrote.
    return [value for name, value in msg.raw_items() if name.lower() == "from"]


def from_headers(raw: bytes) -> list[str]:
    """Raw From values from a full message or from headers alone (BODY.PEEK[HEADER.FIELDS (FROM)])."""
    return _raw_from_headers(_parse(raw))


def _part_text(part: EmailMessage) -> str:
    try:
        return part.get_content()
    except (LookupError, UnicodeError):  # unknown or wrong charset
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")


def _body_text(msg: EmailMessage) -> str:
    plain: list[str] = []
    html: list[str] = []
    for part in msg.walk():
        if part.is_multipart() or part.is_attachment():
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain.append(_part_text(part))
        elif content_type == "text/html":
            html.append(_part_text(part))
    if plain:
        text = "\n".join(plain)
    elif html:
        text = html_to_text(html[0])
    else:
        text = ""
    return text.replace("\r\n", "\n").strip()


def _received_at(msg: EmailMessage) -> datetime | None:
    raw_date = next((v for n, v in msg.raw_items() if n.lower() == "date"), None)
    if raw_date is None:
        return None
    try:
        parsed = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError):
        return None
    # A date with no zone (or -0000) means UTC per RFC 5322.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_email(raw: bytes) -> ParsedEmail:
    msg = _parse(raw)
    headers = _raw_from_headers(msg)
    message_id = (msg.get("Message-ID") or "").strip() or hashlib.sha256(raw).hexdigest()
    return ParsedEmail(
        message_id=message_id,
        from_headers=headers,
        sender=sender_address(headers),
        subject=str(msg.get("Subject") or "").strip(),
        received_at=_received_at(msg),
        body=_body_text(msg),
    )
