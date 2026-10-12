"""All business rules, in plain Python (spec section 4).

Model output only reaches a file, an approval status, a vendor ID or a routing
decision through validate(). Every issue is collected, not just the first, so
the reviewer sees everything that is wrong in one pass.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from po_intake.models import ExtractedLine, ExtractedPO
from po_intake.normalize import normalize_vendor_name, parse_date, parse_number
from po_intake.refdata import Vendor

READY = "READY"
REVIEW = "REVIEW"
PASSED = "Passed"
PENDING = "Pending"

# Issue codes (spec section 4, with two project changes: a vendor name not in
# vendors.csv counts as VENDOR_NOT_STATED, and the email must state the PO total).
NO_BODY_TEXT = "NO_BODY_TEXT"
BODY_TOO_LONG = "BODY_TOO_LONG"
EXTRACTION_FAILED = "EXTRACTION_FAILED"
VENDOR_NOT_STATED = "VENDOR_NOT_STATED"
VENDOR_AMBIGUOUS = "VENDOR_AMBIGUOUS"
SHIP_TO_MISSING = "SHIP_TO_MISSING"
NO_LINES = "NO_LINES"
LINE_FIELD_MISSING = "LINE_FIELD_MISSING"
INVALID_NUMBER = "INVALID_NUMBER"
INVALID_DATE = "INVALID_DATE"
TOTAL_NOT_STATED = "TOTAL_NOT_STATED"
TOTAL_MISMATCH = "TOTAL_MISMATCH"
AMBIGUOUS_REQUEST = "AMBIGUOUS_REQUEST"

_CENT = Decimal("0.01")

# Line fields in check order, with the wording the reviewer sees.
_LINE_FIELDS = {
    "description": "description",
    "quantity": "quantity",
    "uom": "unit of measure",
    "unit_price": "unit price",
    "need_by_date": "need-by date",
    "cost_center": "cost center",
}


@dataclass(frozen=True)
class Issue:
    code: str
    message: str  # plain English, for the human reviewer


@dataclass(frozen=True)
class VendorMatch:
    vendor: Vendor | None  # set only when exactly one entry matched
    candidates: list[Vendor]  # the clashing entries when ambiguous; never auto-selected
    issue: Issue | None


@dataclass(frozen=True)
class ValidLine:
    line_number: int
    description: str
    quantity: Decimal
    uom: str  # uppercase
    unit_price: Decimal
    line_amount: Decimal
    need_by_date: date
    cost_center: str


@dataclass
class Decision:
    route: str  # READY or REVIEW
    issues: list[Issue]
    vendor: Vendor | None = None
    vendor_as_written: str | None = None
    vendor_candidates: list[Vendor] = field(default_factory=list)  # only for VENDOR_AMBIGUOUS
    ship_to: str | None = None
    lines: list[ValidLine] = field(default_factory=list)
    po_total: Decimal | None = None  # computed from the lines; set only when every line is valid
    approval_status: str | None = None  # set only when po_total is


def _text(value: str | None) -> str | None:
    """Trimmed text, or None when empty or only whitespace."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _vendor_list(vendors: list[Vendor]) -> str:
    return "; ".join(f"{v.vendor_id} {v.vendor_name}" for v in vendors)


def route(issues: list[Issue]) -> str:
    return REVIEW if issues else READY


def line_amount(quantity: Decimal, unit_price: Decimal) -> Decimal:
    return (quantity * unit_price).quantize(_CENT, rounding=ROUND_HALF_UP)


def approval_status(po_total: Decimal, threshold: Decimal) -> str:
    """Strictly over the threshold is Pending; exactly the threshold is Passed."""
    return PENDING if po_total > threshold else PASSED


def check_body(body: str, max_body_chars: int) -> Issue | None:
    """Pre-extraction check. An issue here means no model call is made."""
    if not body.strip():
        return Issue(NO_BODY_TEXT, "The email has no readable text.")
    if len(body) > max_body_chars:
        return Issue(
            BODY_TOO_LONG,
            f"The email body is {len(body)} characters, over the {max_body_chars} limit; it was not read.",
        )
    return None


def review_without_extraction(issue: Issue) -> Decision:
    """Decision for an email that never got a usable extraction."""
    return Decision(route=REVIEW, issues=[issue])


def match_vendor(name: str | None, vendors: list[Vendor]) -> VendorMatch:
    name = _text(name)
    if name is None:
        return VendorMatch(None, [], Issue(VENDOR_NOT_STATED, "No vendor is named in the email."))

    wanted = normalize_vendor_name(name)
    exact = [v for v in vendors if v.normalized == wanted]
    if len(exact) == 1:
        return VendorMatch(exact[0], [], None)
    if len(exact) > 1:
        return VendorMatch(None, exact, Issue(
            VENDOR_AMBIGUOUS,
            f"Vendor '{name}' matches more than one entry: {_vendor_list(exact)}",
        ))

    # No fuzzy matching: a name not in the list (typo included) counts as not stated.
    return VendorMatch(None, [], Issue(
        VENDOR_NOT_STATED, f"Vendor '{name}' is not in the vendor list, so no vendor is stated.",
    ))


def _check_line(n: int, line: ExtractedLine, issues: list[Issue]) -> ValidLine | None:
    """Append this line's issues; return a ValidLine only if it has none."""
    values = {f: _text(getattr(line, f)) for f in _LINE_FIELDS}
    before = len(issues)

    for f, label in _LINE_FIELDS.items():
        if values[f] is None:
            issues.append(Issue(LINE_FIELD_MISSING, f"Line {n}: {label} is missing."))

    numbers: dict[str, Decimal | None] = {}
    for f in ("quantity", "unit_price"):
        if values[f] is not None:
            numbers[f] = parse_number(values[f])
            if numbers[f] is None:
                issues.append(Issue(
                    INVALID_NUMBER,
                    f"Line {n}: {_LINE_FIELDS[f]} '{values[f]}' is not a usable positive number.",
                ))

    need_by = None
    if values["need_by_date"] is not None:
        need_by = parse_date(values["need_by_date"])
        if need_by is None:
            issues.append(Issue(
                INVALID_DATE,
                f"Line {n}: need-by date '{values['need_by_date']}' is not a valid YYYY-MM-DD date.",
            ))

    if len(issues) > before:
        return None
    return ValidLine(
        line_number=n,
        description=values["description"],
        quantity=numbers["quantity"],
        uom=values["uom"].upper(),
        unit_price=numbers["unit_price"],
        line_amount=line_amount(numbers["quantity"], numbers["unit_price"]),
        need_by_date=need_by,
        cost_center=values["cost_center"],
    )


def validate(
    po: ExtractedPO,
    vendors: list[Vendor],
    threshold: Decimal,
    chosen_vendor_name: str | None = None,
) -> Decision:
    """Apply every rule to an extraction and decide READY or REVIEW.

    chosen_vendor_name is VendorChoice.name_as_written from the vendor loop;
    when it is None, the extracted vendor_name is used instead.
    """
    issues: list[Issue] = []

    vendor_as_written = _text(chosen_vendor_name) or _text(po.vendor_name)
    match = match_vendor(vendor_as_written, vendors)
    if match.issue:
        issues.append(match.issue)

    ship_to = _text(po.ship_to)
    if ship_to is None:
        issues.append(Issue(SHIP_TO_MISSING, "Ship-to is missing."))

    if not po.lines:
        issues.append(Issue(NO_LINES, "No items were found in the email."))
    checked = [_check_line(n, line, issues) for n, line in enumerate(po.lines, start=1)]

    lines: list[ValidLine] = []
    po_total = None
    if po.lines and all(checked):
        lines = checked
        po_total = sum((ln.line_amount for ln in checked), Decimal("0.00"))
    _check_stated_total(po.stated_total, po_total, issues)

    for text in po.ambiguities:
        if _text(text):
            issues.append(Issue(AMBIGUOUS_REQUEST, f"Unclear request: {text.strip()}"))

    return Decision(
        route=route(issues),
        issues=issues,
        vendor=match.vendor,
        vendor_as_written=vendor_as_written,
        vendor_candidates=match.candidates,
        ship_to=ship_to,
        lines=lines,
        po_total=po_total,
        # Always from the computed total, never from the stated one.
        approval_status=approval_status(po_total, threshold) if po_total is not None else None,
    )


def _check_stated_total(stated: str | None, po_total: Decimal | None, issues: list[Issue]) -> None:
    """The email must state the PO total, and it must equal the computed total."""
    stated = _text(stated)
    if stated is None:
        issues.append(Issue(TOTAL_NOT_STATED, "The email does not state the PO total."))
        return
    value = parse_number(stated)
    if value is None:
        issues.append(Issue(INVALID_NUMBER, f"Stated total '{stated}' is not a usable positive number."))
    elif po_total is not None and value != po_total:
        issues.append(Issue(
            TOTAL_MISMATCH,
            f"The email states a total of {stated}, but the lines add up to {po_total}.",
        ))
