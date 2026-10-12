"""Deterministic tests for emailparse.py. No model, no network."""

import hashlib
from datetime import datetime, timezone

from po_intake.emailparse import from_headers, html_to_text, parse_email

PLAIN = b"""\
From: Jane Doe <Jane.Doe@Example.com>
To: po@example.com
Subject: Copy paper
Date: Sat, 10 Oct 2026 09:15:30 -0400
Message-ID: <abc123@example.com>
Content-Type: text/plain; charset="utf-8"

Please order 20 boxes of copy paper.
"""


def test_plain_message():
    e = parse_email(PLAIN)
    assert e.message_id == "<abc123@example.com>"
    assert e.sender == "jane.doe@example.com"
    assert e.subject == "Copy paper"
    assert e.received_at == datetime(2026, 10, 10, 13, 15, 30, tzinfo=timezone.utc)
    assert e.body == "Please order 20 boxes of copy paper."


def test_crlf_line_endings():
    assert parse_email(PLAIN.replace(b"\n", b"\r\n")).body == "Please order 20 boxes of copy paper."


def test_multipart_prefers_plain_and_ignores_attachment():
    raw = b"""\
From: jane.doe@example.com
Subject: Order
Message-ID: <m1@example.com>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="OUT"

--OUT
Content-Type: multipart/alternative; boundary="ALT"

--ALT
Content-Type: text/plain; charset="utf-8"

Plain body.
--ALT
Content-Type: text/html; charset="utf-8"

<p>HTML body.</p>
--ALT--
--OUT
Content-Type: text/plain; name="notes.txt"
Content-Disposition: attachment; filename="notes.txt"

Attachment text, must be ignored.
--OUT--
"""
    assert parse_email(raw).body == "Plain body."


def test_html_only_drops_script_and_style():
    raw = b"""\
From: jane.doe@example.com
Content-Type: text/html; charset="utf-8"

<html><head><style>p {color: red}</style><script>alert('x')</script></head>
<body><p>Order 5 chairs &amp; 2 desks.</p><p>Ship to Building 2.</p></body></html>
"""
    assert parse_email(raw).body == "Order 5 chairs & 2 desks.\nShip to Building 2."


def test_html_to_text_line_breaks():
    assert html_to_text("a<br>b<div>c</div>") == "a\nb\nc"


def test_quoted_printable_and_encoded_subject():
    raw = b"""\
From: jane.doe@example.com
Subject: =?utf-8?q?Caf=C3=A9_order?=
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: quoted-printable

Price is 1,250.50 =E2=80=94 caf=C3=A9 chairs.
"""
    e = parse_email(raw)
    assert e.subject == "Café order"
    assert e.body == "Price is 1,250.50 — café chairs."


def test_missing_message_id_uses_hash_of_raw_bytes():
    raw = b"From: jane.doe@example.com\n\nHello\n"
    assert parse_email(raw).message_id == hashlib.sha256(raw).hexdigest()


def test_missing_or_bad_date_is_none():
    assert parse_email(b"From: a@b.com\n\nx\n").received_at is None
    assert parse_email(b"From: a@b.com\nDate: not a date\n\nx\n").received_at is None


def test_date_without_zone_is_utc():
    e = parse_email(b"From: a@b.com\nDate: Sat, 10 Oct 2026 09:15:30 -0000\n\nx\n")
    assert e.received_at.tzinfo is not None
    assert e.received_at.utcoffset().total_seconds() == 0


def test_empty_body():
    assert parse_email(b"From: jane.doe@example.com\nSubject: hi\n\n   \n").body == ""


def test_doubled_from_gives_no_sender():
    e = parse_email(b"From: jane.doe@example.com\nFrom: jane.doe@example.com\n\nx\n")
    assert len(e.from_headers) == 2
    assert e.sender is None


def test_display_name_spoof_keeps_raw_value():
    e = parse_email(b"From: jane.doe@example.com <attacker@evil.net>\n\nx\n")
    assert e.from_headers == ["jane.doe@example.com <attacker@evil.net>"]
    assert e.sender != "jane.doe@example.com"


def test_from_headers_on_header_only_fetch():
    # What BODY.PEEK[HEADER.FIELDS (FROM)] returns: just the header and a blank line.
    assert from_headers(b"From: Jane Doe <jane.doe@example.com>\r\n\r\n") == [
        "Jane Doe <jane.doe@example.com>"
    ]
    assert from_headers(b"\r\n") == []
