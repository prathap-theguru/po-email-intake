"""Deterministic tests for rules.py (spec section 6). No model, no network."""

from decimal import Decimal

import pytest

from po_intake.models import ExtractedLine, ExtractedPO
from po_intake.normalize import normalize_vendor_name
from po_intake.refdata import Vendor, load_vendors
from po_intake.rules import (
    AMBIGUOUS_REQUEST,
    BODY_TOO_LONG,
    INVALID_DATE,
    INVALID_NUMBER,
    LINE_FIELD_MISSING,
    NO_BODY_TEXT,
    NO_LINES,
    PASSED,
    PENDING,
    READY,
    REVIEW,
    SHIP_TO_MISSING,
    TOTAL_MISMATCH,
    TOTAL_NOT_STATED,
    VENDOR_AMBIGUOUS,
    VENDOR_NOT_STATED,
    Issue,
    approval_status,
    check_body,
    line_amount,
    match_vendor,
    route,
    validate,
)

THRESHOLD = Decimal("1000.00")
VENDORS = load_vendors("ref/vendors.csv")


def vendor(vid, name):
    return Vendor(vid, name, normalize_vendor_name(name))


def line(**overrides):
    fields = dict(
        description="copy paper", quantity="20", uom="box", unit_price="32.50",
        need_by_date="2026-10-20", cost_center="CC-4410",
    )
    fields.update(overrides)
    return ExtractedLine(**fields)


def po(lines=None, **overrides):
    fields = dict(
        vendor_name="Northwind Office Products",
        ship_to="Building 3 loading dock",
        stated_total="650.00",
        lines=[line()] if lines is None else lines,
        ambiguities=[],
    )
    fields.update(overrides)
    return ExtractedPO(**fields)


def codes(decision):
    return [i.code for i in decision.issues]


# --- Threshold and rounding -----------------------------------------------------

@pytest.mark.parametrize(
    ("total", "expected"),
    [("999.99", PASSED), ("1000.00", PASSED), ("1000.01", PENDING)],
)
def test_threshold(total, expected):
    assert approval_status(Decimal(total), THRESHOLD) == expected


def test_threshold_uses_po_total_not_line():
    # Two lines of 600 each: no single line is over 1000, the total is.
    d = validate(po([line(quantity="1", unit_price="600"), line(quantity="1", unit_price="600")],
                    stated_total="1200"), VENDORS, THRESHOLD)
    assert d.route == READY
    assert d.po_total == Decimal("1200.00")
    assert d.approval_status == PENDING


def test_rounding_half_up():
    assert line_amount(Decimal("3"), Decimal("0.335")) == Decimal("1.01")


def test_total_is_sum_of_rounded_lines():
    # 1.005 + 1.005 = 2.01 unrounded, but each line rounds to 1.01 first.
    d = validate(po([line(quantity="3", unit_price="0.335"), line(quantity="3", unit_price="0.335")]),
                 VENDORS, THRESHOLD)
    assert d.po_total == Decimal("2.02")


# --- Vendor matching -----------------------------------------------------------

def test_vendor_one_match_resolves():
    m = match_vendor("Northwind Office Products", VENDORS)
    assert m.issue is None
    assert m.vendor.vendor_id == "V1077"
    assert m.vendor.vendor_name == "Northwind Office Products LLC"


@pytest.mark.parametrize("name", ["Acme Suply", "Zebra Widgets"])
def test_vendor_not_in_list_counts_as_not_stated(name):
    # No fuzzy matching: a typo is treated exactly like a missing vendor.
    m = match_vendor(name, VENDORS)
    assert m.vendor is None
    assert m.issue.code == VENDOR_NOT_STATED
    assert m.candidates == []
    assert f"'{name}'" in m.issue.message


def test_vendor_ambiguous():
    vendors = [vendor("V1", "Acme Supply Co."), vendor("V2", "ACME SUPPLY, INC."), vendor("V3", "Globex")]
    m = match_vendor("Acme Supply", vendors)
    assert m.vendor is None
    assert m.issue.code == VENDOR_AMBIGUOUS
    assert {v.vendor_id for v in m.candidates} == {"V1", "V2"}


@pytest.mark.parametrize("name", [None, "", "   "])
def test_vendor_not_stated(name):
    assert match_vendor(name, VENDORS).issue.code == VENDOR_NOT_STATED


def test_chosen_vendor_name_wins_over_extracted():
    d = validate(po(vendor_name="Acme Suply"), VENDORS, THRESHOLD, chosen_vendor_name="Acme Supply")
    assert d.vendor.vendor_id == "V1042"
    assert d.vendor_as_written == "Acme Supply"


# --- Routing -------------------------------------------------------------------

def test_route():
    assert route([]) == READY
    assert route([Issue(NO_LINES, "x")]) == REVIEW


def test_complete_po_is_ready():
    d = validate(po(), VENDORS, THRESHOLD)
    assert d.issues == []
    assert d.route == READY
    assert d.vendor.vendor_id == "V1077"
    assert d.po_total == Decimal("650.00")
    assert d.approval_status == PASSED
    assert d.lines[0].uom == "BOX"


def test_missing_cost_center():
    d = validate(po([line(cost_center=None)]), VENDORS, THRESHOLD)
    assert d.route == REVIEW
    assert codes(d) == [LINE_FIELD_MISSING]
    assert d.issues[0].message == "Line 1: cost center is missing."
    assert d.po_total is None and d.approval_status is None


def test_whitespace_counts_as_missing():
    d = validate(po([line(unit_price="  ")], ship_to=" "), VENDORS, THRESHOLD)
    assert codes(d) == [SHIP_TO_MISSING, LINE_FIELD_MISSING]


def test_invalid_number_and_date():
    d = validate(po([line(quantity="twenty-one", need_by_date="next Friday")]), VENDORS, THRESHOLD)
    assert codes(d) == [INVALID_NUMBER, INVALID_DATE]


def test_no_lines():
    d = validate(po(lines=[]), VENDORS, THRESHOLD)
    assert codes(d) == [NO_LINES]
    assert d.po_total is None


def test_ambiguity_goes_to_review():
    d = validate(po(ambiguities=["Asks to add shipping."]), VENDORS, THRESHOLD)
    assert d.route == REVIEW
    assert codes(d) == [AMBIGUOUS_REQUEST]


def test_one_bad_line_sends_whole_po_to_review():
    d = validate(po([line(), line(unit_price=None)]), VENDORS, THRESHOLD)
    assert d.route == REVIEW
    assert d.issues[0].message == "Line 2: unit price is missing."


def test_all_issues_collected():
    d = validate(po([line(description=None, quantity="0")], vendor_name=None, ship_to=None,
                    stated_total=None), VENDORS, THRESHOLD)
    assert codes(d) == [VENDOR_NOT_STATED, SHIP_TO_MISSING, LINE_FIELD_MISSING, INVALID_NUMBER,
                        TOTAL_NOT_STATED]


# --- Stated total ---------------------------------------------------------------

@pytest.mark.parametrize("stated", [None, "", "  "])
def test_total_not_stated(stated):
    d = validate(po(stated_total=stated), VENDORS, THRESHOLD)
    assert d.route == REVIEW
    assert codes(d) == [TOTAL_NOT_STATED]


@pytest.mark.parametrize("stated", ["650", "650.00", "650.0000"])
def test_stated_total_matches(stated):
    assert validate(po(stated_total=stated), VENDORS, THRESHOLD).route == READY


def test_total_mismatch():
    d = validate(po(stated_total="600.00"), VENDORS, THRESHOLD)
    assert d.route == REVIEW
    assert codes(d) == [TOTAL_MISMATCH]
    assert d.issues[0].message == "The email states a total of 600.00, but the lines add up to 650.00."


def test_stated_total_invalid():
    assert codes(validate(po(stated_total="about 650"), VENDORS, THRESHOLD)) == [INVALID_NUMBER]


def test_approval_uses_computed_total_not_stated():
    # Lines add up to 5000; a stated 999 is a mismatch and never makes it Passed.
    d = validate(po([line(quantity="10", unit_price="500")], stated_total="999"), VENDORS, THRESHOLD)
    assert d.route == REVIEW
    assert codes(d) == [TOTAL_MISMATCH]
    assert d.approval_status == PENDING


# --- Body checks ---------------------------------------------------------------

def test_check_body():
    assert check_body("hello", 10) is None
    assert check_body("  \n ", 10).code == NO_BODY_TEXT
    assert check_body("x" * 11, 10).code == BODY_TOO_LONG
