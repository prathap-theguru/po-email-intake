"""Deterministic tests for refdata.py (spec section 6). No model, no network."""

import pytest

from po_intake.config import ConfigError
from po_intake.refdata import (
    is_approved_sender,
    load_approved_senders,
    load_vendors,
    sender_address,
)

APPROVED = frozenset({"jane.doe@example.com"})


def write(tmp_path, text, name="f.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# --- Sender check -----------------------------------------------------------

@pytest.mark.parametrize(
    "from_header",
    ["jane.doe@example.com", "Jane Doe <JANE.DOE@example.com>"],
)
def test_sender_approved(from_header):
    assert is_approved_sender([from_header], APPROVED)


@pytest.mark.parametrize(
    "from_header",
    [
        "jane.doe@example.com.evil.net",
        "xjane.doe@example.com",
        "jane.doe@example.com <attacker@evil.net>",  # approved address as display name
        '"jane.doe@example.com" <attacker@evil.net>',
        "jane.doe@example.com, attacker@evil.net",  # two addresses in one From
        "",
    ],
)
def test_sender_not_approved(from_header):
    assert not is_approved_sender([from_header], APPROVED)


def test_missing_from_not_approved():
    assert not is_approved_sender([], APPROVED)


def test_doubled_from_not_approved():
    assert not is_approved_sender(["jane.doe@example.com", "jane.doe@example.com"], APPROVED)


def test_sender_address_lowercases():
    assert sender_address(["Jane <Jane.Doe@Example.COM>"]) == "jane.doe@example.com"


# --- Approved-sender loader ---------------------------------------------------

def test_approved_loader_trims_lowercases_merges(tmp_path):
    p = write(tmp_path, "email\n  Jane.Doe@Example.com \njane.doe@example.com\n\nyou@example.com\n")
    assert load_approved_senders(p) == {"jane.doe@example.com", "you@example.com"}


def test_approved_loader_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="Cannot read"):
        load_approved_senders(tmp_path / "nope.csv")


def test_approved_loader_header_only(tmp_path):
    with pytest.raises(ConfigError, match="no entries"):
        load_approved_senders(write(tmp_path, "email\n"))


def test_approved_loader_entry_without_at(tmp_path):
    with pytest.raises(ConfigError, match="janedoe.example.com"):
        load_approved_senders(write(tmp_path, "email\njanedoe.example.com\n"))


def test_approved_loader_wrong_column(tmp_path):
    with pytest.raises(ConfigError, match="missing column"):
        load_approved_senders(write(tmp_path, "address\njane.doe@example.com\n"))


# --- Vendor loader ------------------------------------------------------------

def test_vendor_loader_reads_real_file():
    vendors = load_vendors("ref/vendors.csv")
    by_id = {v.vendor_id: v for v in vendors}
    assert by_id["V1042"].vendor_name == "Acme Supply Co."
    assert by_id["V1042"].normalized == "acme supply"
    assert len(vendors) == 3


def test_vendor_loader_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="Cannot read"):
        load_vendors(tmp_path / "nope.csv")


def test_vendor_loader_empty_file(tmp_path):
    with pytest.raises(ConfigError):
        load_vendors(write(tmp_path, ""))


def test_vendor_loader_header_only(tmp_path):
    with pytest.raises(ConfigError, match="no vendors"):
        load_vendors(write(tmp_path, "vendor_id,vendor_name\n"))


def test_vendor_loader_missing_column(tmp_path):
    with pytest.raises(ConfigError, match="vendor_name"):
        load_vendors(write(tmp_path, "vendor_id\nV1\n"))


def test_vendor_loader_duplicate_id(tmp_path):
    with pytest.raises(ConfigError, match="duplicate vendor_id V1"):
        load_vendors(write(tmp_path, "vendor_id,vendor_name\nV1,A\nV1,B\n"))
