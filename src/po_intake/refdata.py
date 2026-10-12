"""Reference files and the approved-sender check (spec section 4).

Both CSVs are loaded at the start of each run. Any problem raises ConfigError
so the run stops before Gmail is contacted; a missing file is never treated
as an empty list.
"""

import csv
from dataclasses import dataclass
from email.utils import getaddresses
from pathlib import Path

from po_intake.config import ConfigError
from po_intake.normalize import normalize_vendor_name


@dataclass(frozen=True)
class Vendor:
    vendor_id: str
    vendor_name: str  # canonical name as written in vendors.csv
    normalized: str  # normalize_vendor_name(vendor_name), used for matching


def _read_rows(path: str | Path, columns: list[str]) -> list[dict]:
    """Read a UTF-8 CSV with a header row and check the required columns exist."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:  # -sig tolerates an Excel BOM
            reader = csv.DictReader(f)
            missing = [c for c in columns if c not in (reader.fieldnames or [])]
            if missing:
                raise ConfigError(f"{path}: missing column(s) {', '.join(missing)}")
            return list(reader)
    except OSError as e:
        raise ConfigError(f"Cannot read {path}: {e.strerror}") from None


def load_vendors(path: str | Path) -> list[Vendor]:
    rows = _read_rows(path, ["vendor_id", "vendor_name"])
    vendors: list[Vendor] = []
    seen_ids: set[str] = set()
    for n, row in enumerate(rows, start=2):  # line 1 is the header
        vendor_id = (row["vendor_id"] or "").strip()
        vendor_name = (row["vendor_name"] or "").strip()
        if not vendor_id or not vendor_name:
            raise ConfigError(f"{path} line {n}: vendor_id and vendor_name are both required")
        if vendor_id in seen_ids:
            raise ConfigError(f"{path} line {n}: duplicate vendor_id {vendor_id}")
        seen_ids.add(vendor_id)
        vendors.append(Vendor(vendor_id, vendor_name, normalize_vendor_name(vendor_name)))
    if not vendors:
        raise ConfigError(f"{path} has no vendors")
    return vendors


def load_approved_senders(path: str | Path) -> frozenset[str]:
    """Trimmed, lowercased, de-duplicated addresses. Blank rows are skipped."""
    rows = _read_rows(path, ["email"])
    senders: set[str] = set()
    for row in rows:
        email = (row["email"] or "").strip().lower()
        if not email:
            continue
        if "@" not in email:
            raise ConfigError(f"{path}: entry without @: {email!r}")
        senders.add(email)
    if not senders:
        raise ConfigError(f"{path} has no entries")
    return frozenset(senders)


def sender_address(from_headers: list[str]) -> str | None:
    """The single From address, lowercased, or None if there isn't exactly one.

    from_headers is every From header value on the message (msg.get_all("From")).
    The display name and Reply-To are ignored.
    """
    if len(from_headers) != 1:
        return None
    addresses = getaddresses(from_headers)  # strict parsing: malformed -> ('', '')
    if len(addresses) != 1:
        return None
    address = addresses[0][1].strip().lower()
    return address if "@" in address else None


def is_approved_sender(from_headers: list[str], approved: frozenset[str]) -> bool:
    """Exact match of the whole From address against approved_senders.csv."""
    address = sender_address(from_headers)
    return address is not None and address in approved
