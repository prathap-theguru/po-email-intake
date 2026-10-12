"""Ready and review CSV writers (spec section 5).

Each processed email produces exactly one file. Files are written to a .tmp
name in the same folder, then linked to the final name, which fails instead
of overwriting if that name was taken in the meantime.
"""

import csv
import hashlib
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from po_intake.emailparse import ParsedEmail
from po_intake.models import ExtractedPO
from po_intake.normalize import sanitize_sender
from po_intake.rules import READY, Decision

CURRENCY = "USD"

# Column lists live here only, so the layout can change in one place.
READY_COLUMNS = [
    "request_reference", "line_number", "vendor_id", "vendor_name", "buyer_id",
    "requester_email", "currency", "order_date", "ship_to", "description",
    "quantity", "uom", "unit_price", "line_amount", "need_by_date",
    "cost_center", "po_total", "approval_status", "source_message_id",
]

REVIEW_COLUMNS = [
    "review_reference", "received_at", "sender_email", "subject", "issue_count",
    "issues", "vendor_as_written", "vendor_candidates", "extracted_json",
    "source_message_id",
]

_CENT = Decimal("0.01")


def msgid8(message_id: str) -> str:
    return hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:8]


def _local_time(email: ParsedEmail, timezone: str) -> datetime:
    if email.received_at is None:
        raise ValueError("received_at must be set (use the IMAP internal date) before writing")
    return email.received_at.astimezone(ZoneInfo(timezone))


def _sender(email: ParsedEmail) -> str:
    if email.sender is None:
        raise ValueError("Cannot write a file for an email without a single sender address")
    return email.sender


def reference(email: ParsedEmail, timezone: str) -> str:
    """<YYYYMMDD>-<HHMMSS>-<msgid8>: request_reference and review_reference."""
    return f"{_local_time(email, timezone):%Y%m%d-%H%M%S}-{msgid8(email.message_id)}"


def file_stem(email: ParsedEmail, timezone: str) -> str:
    """<sender>_<YYYYMMDD>_<HHMMSS>_<msgid8>; the -n suffix and kind are added later."""
    local = _local_time(email, timezone)
    return f"{sanitize_sender(_sender(email))}_{local:%Y%m%d_%H%M%S}_{msgid8(email.message_id)}"


def _money(value: Decimal) -> str:
    return str(value.quantize(_CENT))


def _unit_price(value: Decimal) -> str:
    """2 to 4 decimals: 150 -> 150.00, 0.335 stays 0.335."""
    return str(value.quantize(_CENT)) if value.as_tuple().exponent > -2 else str(value)


def _write_csv(folder: str | Path, stem: str, kind: str, columns: list[str], rows: list[dict]) -> Path:
    """Write rows to <stem><-n><kind>.csv via a .tmp file. Never overwrites."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{stem}{kind}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    try:
        n = 1
        while True:
            final = folder / f"{stem}{'' if n == 1 else f'-{n}'}{kind}.csv"
            try:
                os.link(tmp, final)  # atomic, and fails if final exists
                return final
            except FileExistsError:
                n += 1
    finally:
        tmp.unlink(missing_ok=True)


def write_ready(email: ParsedEmail, decision: Decision, ready_dir: str | Path,
                buyer_id: str, timezone: str) -> Path:
    """One row per PO line; PO-level fields repeated on every row."""
    if decision.route != READY or decision.issues or not decision.lines or decision.po_total is None:
        raise ValueError("write_ready needs a READY decision from rules.validate")
    ref = reference(email, timezone)
    order_date = _local_time(email, timezone).date().isoformat()
    rows = [
        {
            "request_reference": ref,
            "line_number": line.line_number,
            "vendor_id": decision.vendor.vendor_id,
            "vendor_name": decision.vendor.vendor_name,
            "buyer_id": buyer_id,
            "requester_email": _sender(email),
            "currency": CURRENCY,
            "order_date": order_date,
            "ship_to": decision.ship_to,
            "description": line.description,
            "quantity": str(line.quantity),
            "uom": line.uom,
            "unit_price": _unit_price(line.unit_price),
            "line_amount": _money(line.line_amount),
            "need_by_date": line.need_by_date.isoformat(),
            "cost_center": line.cost_center,
            "po_total": _money(decision.po_total),
            "approval_status": decision.approval_status,
            "source_message_id": email.message_id,
        }
        for line in decision.lines
    ]
    return _write_csv(ready_dir, file_stem(email, timezone), "_PO_ready", READY_COLUMNS, rows)


def write_review(email: ParsedEmail, decision: Decision, extracted: ExtractedPO | None,
                 review_dir: str | Path, timezone: str) -> Path:
    """One row per email, listing every issue for the human reviewer."""
    if not decision.issues:
        raise ValueError("write_review needs a decision with at least one issue")
    row = {
        "review_reference": reference(email, timezone),
        "received_at": _local_time(email, timezone).isoformat(),
        "sender_email": _sender(email),
        "subject": email.subject,
        "issue_count": len(decision.issues),
        "issues": "; ".join(f"({n}) {i.message}" for n, i in enumerate(decision.issues, start=1)),
        "vendor_as_written": decision.vendor_as_written or "",
        "vendor_candidates": "; ".join(f"{v.vendor_id} {v.vendor_name}" for v in decision.vendor_candidates),
        "extracted_json": json.dumps(extracted.model_dump(), separators=(",", ":"), ensure_ascii=False)
        if extracted else "",
        "source_message_id": email.message_id,
    }
    return _write_csv(review_dir, file_stem(email, timezone), "_review", REVIEW_COLUMNS, [row])
