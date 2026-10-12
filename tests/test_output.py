"""Deterministic tests for output.py (spec sections 5 and 6). No model, no network."""

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from po_intake.emailparse import ParsedEmail
from po_intake.models import ExtractedLine, ExtractedPO
from po_intake.output import READY_COLUMNS, REVIEW_COLUMNS, write_ready, write_review
from po_intake.refdata import load_vendors
from po_intake.rules import validate

TZ = "America/New_York"
BUYER = "B-0001"
THRESHOLD = Decimal("1000.00")
VENDORS = load_vendors("ref/vendors.csv")
MSG_ID = "<complete-over@example.com>"
MSGID8 = hashlib.sha256(MSG_ID.encode()).hexdigest()[:8]


def email(**overrides):
    fields = dict(
        message_id=MSG_ID,
        from_headers=["Jane Doe <Jane.Doe@Example.com>"],
        sender="jane.doe@example.com",
        subject="Monitor stands",
        received_at=datetime(2026, 10, 10, 13, 15, 30, tzinfo=timezone.utc),  # 09:15:30 in New York
        body="...",
    )
    fields.update(overrides)
    return ParsedEmail(**fields)


def complete_over():
    """Hand-built extraction of the complete_over sample (spec section 6)."""
    common = dict(need_by_date="2026-11-02", cost_center="CC-5120")
    return ExtractedPO(
        vendor_name="Acme Supply",
        ship_to="Building 1 reception",
        stated_total="1050.00",
        lines=[
            ExtractedLine(description="monitor stands", quantity="4", uom="each", unit_price="150", **common),
            ExtractedLine(description="keyboard trays", quantity="5", uom="each", unit_price="90", **common),
        ],
        ambiguities=[],
    )


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


# --- complete_over: the step 2 "done when" check --------------------------------

def test_complete_over_ready_file(tmp_path):
    decision = validate(complete_over(), VENDORS, THRESHOLD)
    path = write_ready(email(), decision, tmp_path, BUYER, TZ)

    assert path.name == f"jane.doe_at_example.com_20261010_091530_{MSGID8}_PO_ready.csv"
    header, rows = read_rows(path)
    assert header == READY_COLUMNS
    assert len(rows) == 2
    ref = f"20261010-091530-{MSGID8}"
    common = {
        "request_reference": ref, "vendor_id": "V1042", "vendor_name": "Acme Supply Co.",
        "buyer_id": BUYER, "requester_email": "jane.doe@example.com", "currency": "USD",
        "order_date": "2026-10-10", "ship_to": "Building 1 reception", "uom": "EACH",
        "need_by_date": "2026-11-02", "cost_center": "CC-5120", "po_total": "1050.00",
        "approval_status": "Pending", "source_message_id": MSG_ID,
    }
    assert rows[0] == {**common, "line_number": "1", "description": "monitor stands",
                       "quantity": "4", "unit_price": "150.00", "line_amount": "600.00"}
    assert rows[1] == {**common, "line_number": "2", "description": "keyboard trays",
                       "quantity": "5", "unit_price": "90.00", "line_amount": "450.00"}


def test_unit_price_keeps_up_to_4_decimals(tmp_path):
    po = complete_over().model_copy(update={"stated_total": "1.01", "lines": [
        ExtractedLine(description="x", quantity="3", uom="each", unit_price="0.335",
                      need_by_date="2026-11-02", cost_center="CC-1"),
    ]})
    path = write_ready(email(), validate(po, VENDORS, THRESHOLD), tmp_path, BUYER, TZ)
    row = read_rows(path)[1][0]
    assert (row["unit_price"], row["line_amount"], row["approval_status"]) == ("0.335", "1.01", "Passed")


def test_write_ready_refuses_review_decision(tmp_path):
    po = complete_over().model_copy(update={"vendor_name": "Acme Suply"})
    with pytest.raises(ValueError):
        write_ready(email(), validate(po, VENDORS, THRESHOLD), tmp_path, BUYER, TZ)
    assert list(tmp_path.iterdir()) == []


# --- Review file -----------------------------------------------------------------

def test_review_file(tmp_path):
    po = complete_over().model_copy(update={"vendor_name": "Acme Suply"})
    po.lines[1].cost_center = None
    decision = validate(po, VENDORS, THRESHOLD)
    path = write_review(email(), decision, po, tmp_path, TZ)

    assert path.name == f"jane.doe_at_example.com_20261010_091530_{MSGID8}_review.csv"
    header, rows = read_rows(path)
    assert header == REVIEW_COLUMNS
    assert len(rows) == 1
    row = rows[0]
    assert row["review_reference"] == f"20261010-091530-{MSGID8}"
    assert row["received_at"] == "2026-10-10T09:15:30-04:00"
    assert row["sender_email"] == "jane.doe@example.com"
    assert row["subject"] == "Monitor stands"
    assert row["issue_count"] == "2"
    assert row["issues"] == (
        "(1) Vendor 'Acme Suply' is not in the vendor list, so no vendor is stated.; "
        "(2) Line 2: cost center is missing."
    )
    assert row["vendor_as_written"] == "Acme Suply"
    assert row["vendor_candidates"] == ""
    assert json.loads(row["extracted_json"]) == po.model_dump()
    assert row["source_message_id"] == MSG_ID


def test_review_without_extraction(tmp_path):
    from po_intake.rules import check_body, review_without_extraction

    decision = review_without_extraction(check_body("", 20000))
    row = read_rows(write_review(email(body=""), decision, None, tmp_path, TZ))[1][0]
    assert row["issues"] == "(1) The email has no readable text."
    assert row["extracted_json"] == ""


# --- File names --------------------------------------------------------------------

def test_file_name_pattern_and_time_zone(tmp_path):
    path = write_ready(email(), validate(complete_over(), VENDORS, THRESHOLD), tmp_path, BUYER, TZ)
    assert re.fullmatch(r"[a-z0-9._-]+_\d{8}_\d{6}_[0-9a-f]{8}_PO_ready\.csv", path.name)
    # Same instant, different zone: the name follows the configured zone.
    other = write_ready(email(), validate(complete_over(), VENDORS, THRESHOLD), tmp_path / "la", BUYER,
                        "America/Los_Angeles")
    assert "_20261010_061530_" in other.name


def test_collision_adds_suffix_and_removes_tmp(tmp_path):
    decision = validate(complete_over(), VENDORS, THRESHOLD)
    first = write_ready(email(), decision, tmp_path, BUYER, TZ)
    second = write_ready(email(), decision, tmp_path, BUYER, TZ)
    third = write_ready(email(), decision, tmp_path, BUYER, TZ)

    assert second.name == first.name.replace("_PO_ready", "-2_PO_ready")
    assert third.name == first.name.replace("_PO_ready", "-3_PO_ready")
    assert sorted(p.suffix for p in tmp_path.iterdir()) == [".csv"] * 3  # no .tmp left behind
    assert first.read_text() == second.read_text()  # first file not overwritten


def test_missing_received_at_raises(tmp_path):
    with pytest.raises(ValueError, match="received_at"):
        write_ready(email(received_at=None), validate(complete_over(), VENDORS, THRESHOLD),
                    tmp_path, BUYER, TZ)
