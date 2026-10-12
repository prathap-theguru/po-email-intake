"""Deterministic tests for normalize.py (spec section 6). No model, no network."""

from datetime import date
from decimal import Decimal

import pytest

from po_intake.normalize import (
    normalize_vendor_name,
    parse_date,
    parse_number,
    sanitize_sender,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme Supply Co.", "acme supply"),
        ("ACME  SUPPLY, INC.", "acme supply"),
        ("Smith & Sons LLC", "smith and sons"),
        ("Acme Co Inc", "acme"),
        ("Co", "co"),
        ("Northwind Office Products LLC", "northwind office products"),
    ],
)
def test_vendor_normalization(raw, expected):
    assert normalize_vendor_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Jane.Doe@Example.com", "jane.doe_at_example.com"),
        ("o'brien+po@example.com", "o_brien_po_at_example.com"),
    ],
)
def test_sender_sanitization(raw, expected):
    assert sanitize_sender(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["../../etc/passwd@x.com", "a/b\\c@example.com", "...hidden@example.com", "x" * 200 + "@example.com"],
)
def test_sanitized_sender_is_safe(raw):
    s = sanitize_sender(raw)
    assert "/" not in s and "\\" not in s
    assert not s.startswith(".")
    assert len(s) <= 64


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,250.50", Decimal("1250.50")),
        ("two", Decimal("2")),
        ("Twenty", Decimal("20")),
        ("20", Decimal("20")),
        ("32.50", Decimal("32.50")),
        ("0.3350", Decimal("0.3350")),
    ],
)
def test_number_accepted(raw, expected):
    assert parse_number(raw) == expected


@pytest.mark.parametrize(
    "raw", ["0", "-5", "1.23456", "twenty-one", "", "   ", "$5", "1,25", "1e3", "0.00"]
)
def test_number_rejected(raw):
    assert parse_number(raw) is None


def test_number_is_never_float():
    assert isinstance(parse_number("1.1"), Decimal)


def test_date_valid():
    assert parse_date("2026-10-20") == date(2026, 10, 20)


@pytest.mark.parametrize("raw", ["2026-02-30", "10/20/2026", "2026-1-5", "next Friday", ""])
def test_date_invalid(raw):
    assert parse_date(raw) is None
