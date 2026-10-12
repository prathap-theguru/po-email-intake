"""Run sample emails through the graph and compare with expected.json (spec section 6).

Run all:   uv run python -m po_intake.samples
Run some:  uv run python -m po_intake.samples complete_small vendor_typo

Calls the Anthropic API (except for ignored senders and empty bodies).
Files are written under out/samples/<run time>/, never to the real output folders.
"""

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from po_intake.config import load_config, require_env
from po_intake.emailparse import parse_email
from po_intake.extract import build_extractor
from po_intake.graph import build_graph, run_email
from po_intake.refdata import is_approved_sender, load_approved_senders, load_vendors
from po_intake.rules import PASSED, READY
from po_intake.vendor_agent import build_vendor_agent, build_vendor_finalizer
from po_intake.vendor_tool import make_search_vendors

SAMPLES_DIR = Path("samples")
IGNORED = "IGNORED"
EMBEDDED_CASE = "embedded_instruction"


@dataclass
class Outcome:
    decision: str  # READY, REVIEW or IGNORED
    issue_codes: list[str] = field(default_factory=list)
    vendor_id: str | None = None
    po_total: str | None = None
    approval_status: str | None = None
    line_count: int = 0
    output_path: str | None = None


def case_names() -> list[str]:
    return sorted(p.stem for p in SAMPLES_DIR.glob("*.eml"))


def load_expected(case: str) -> dict:
    return json.loads((SAMPLES_DIR / f"{case}.expected.json").read_text(encoding="utf-8"))


def run_case(case: str, graph, approved: frozenset[str], cfg: dict) -> Outcome:
    email = parse_email((SAMPLES_DIR / f"{case}.eml").read_bytes())
    if not is_approved_sender(email.from_headers, approved):
        return Outcome(IGNORED)  # no graph run, no model call, no file
    state = run_email(graph, email, cfg)
    d = state["decision"]
    return Outcome(
        decision=d.route,
        issue_codes=[i.code for i in d.issues],
        vendor_id=d.vendor.vendor_id if d.vendor else None,
        po_total=str(d.po_total) if d.po_total is not None else None,
        approval_status=d.approval_status,
        line_count=len(d.lines),
        output_path=state["output_path"],
    )


def check(case: str, expected: dict, got: Outcome, threshold: Decimal) -> tuple[list[str], list[str]]:
    """Return (failures, notes). Expected issue codes must be present; extra codes are notes."""
    failures: list[str] = []
    notes: list[str] = []

    allowed = expected["decision"] if isinstance(expected["decision"], list) else [expected["decision"]]
    if got.decision not in allowed:
        failures.append(f"decision {got.decision}, expected {' or '.join(allowed)}")

    for code in expected.get("issue_codes", []):
        if code not in got.issue_codes:
            failures.append(f"missing issue {code}")
    extra = [c for c in got.issue_codes if c not in expected.get("issue_codes", [])]
    if extra and "issue_codes" in expected:
        notes.append(f"extra issues {extra}")

    if got.decision == READY:
        for key in ("vendor_id", "po_total", "approval_status", "line_count"):
            if key in expected and getattr(got, key) != expected[key]:
                failures.append(f"{key} {getattr(got, key)}, expected {expected[key]}")

    # Hard gates (spec section 6)
    if got.approval_status == PASSED and got.po_total and Decimal(got.po_total) > threshold:
        failures.append("HARD GATE: total over threshold marked Passed")
    if case == EMBEDDED_CASE and got.approval_status == PASSED:
        failures.append("HARD GATE: embedded_instruction marked Passed")
    if got.decision == READY and "REVIEW" in allowed and READY not in allowed:
        failures.append("HARD GATE: expected REVIEW but produced READY")

    return failures, notes


def main(argv: list[str]) -> int:
    cfg = load_config()
    require_env("ANTHROPIC_API_KEY")
    run_dir = Path("out/samples") / datetime.now().strftime("%Y%m%d_%H%M%S")
    cfg["paths"]["ready_dir"] = str(run_dir / "ready")
    cfg["paths"]["review_dir"] = str(run_dir / "review")

    approved = load_approved_senders(cfg["paths"]["approved_senders_csv"])
    vendors = load_vendors(cfg["paths"]["vendors_csv"])
    tools = [make_search_vendors(vendors)]
    graph = build_graph(
        cfg, vendors, build_extractor(cfg["model"]),
        build_vendor_agent(cfg["model"], tools), build_vendor_finalizer(cfg["model"]), tools,
    )
    threshold = Decimal(cfg["business"]["approval_threshold"])

    cases = argv or case_names()
    failed = 0
    for case in cases:
        got = run_case(case, graph, approved, cfg)
        failures, notes = check(case, load_expected(case), got, threshold)
        failed += bool(failures)
        status = "FAIL" if failures else "ok  "
        detail = f"{got.decision:7} {','.join(got.issue_codes) or '-'}"
        if got.decision == READY:
            detail += f"  {got.vendor_id} {got.po_total} {got.approval_status}"
        print(f"{status} {case:21} {detail}")
        for line in failures + notes:
            print(f"     {'':21} {line}")

    print(f"\n{len(cases) - failed}/{len(cases)} passed. Files in {run_dir}/")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
