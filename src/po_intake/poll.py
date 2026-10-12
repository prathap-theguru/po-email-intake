"""One pass over the Gmail inbox (spec section 3).

Run with:  uv run python -m po_intake.poll

Unapproved senders are ignored: only their headers are fetched, nothing is
parsed or sent to the model, and the message stays unread. Approved messages
are fetched with BODY.PEEK[] (reading does not mark them read) and marked
\\Seen only after their output file is written.
"""

import imaplib
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone

from po_intake.config import ConfigError, load_config, require_env
from po_intake.emailparse import parse_email
from po_intake.extract import build_extractor
from po_intake.graph import build_graph, run_email
from po_intake.output import msgid8
from po_intake.refdata import is_approved_sender, load_approved_senders, load_vendors
from po_intake.rules import READY
from po_intake.vendor_agent import build_vendor_agent, build_vendor_finalizer
from po_intake.vendor_tool import make_search_vendors


def _fetch(imap: imaplib.IMAP4_SSL, uid: bytes, items: str) -> tuple[bytes, bytes]:
    """Return (response line, message bytes) for one UID FETCH."""
    status, data = imap.uid("FETCH", uid, items)
    if status != "OK" or not data or not isinstance(data[0], tuple):
        raise RuntimeError(f"FETCH {items} failed for UID {uid.decode()}")
    return data[0][0], data[0][1]


def _internal_date(response_line: bytes) -> datetime | None:
    parsed = imaplib.Internaldate2tuple(response_line)
    return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc) if parsed else None


def main() -> int:
    try:
        cfg = load_config()
        require_env("ANTHROPIC_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD")
        # Both lists are loaded before Gmail is contacted; any problem stops the run.
        approved = load_approved_senders(cfg["paths"]["approved_senders_csv"])
        vendors = load_vendors(cfg["paths"]["vendors_csv"])
    except ConfigError as e:
        print(f"Startup error: {e}", file=sys.stderr)
        return 2

    tools = [make_search_vendors(vendors)]
    graph = build_graph(
        cfg, vendors, build_extractor(cfg["model"]),
        build_vendor_agent(cfg["model"], tools), build_vendor_finalizer(cfg["model"]), tools,
    )
    gmail = cfg["gmail"]
    counts = {"ready": 0, "review": 0, "ignored": 0, "failed": 0}

    imap = imaplib.IMAP4_SSL(gmail["host"], gmail["port"])
    try:
        try:
            imap.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
        except imaplib.IMAP4.error:
            print("Gmail login failed: check GMAIL_ADDRESS and GMAIL_APP_PASSWORD, and that IMAP is on.",
                  file=sys.stderr)
            return 2
        imap.select(gmail["folder"])
        status, data = imap.uid("SEARCH", None, "UNSEEN")
        uids = data[0].split() if status == "OK" else []  # ascending UID = oldest first

        # Pass 1: sender check on headers only. Ignored mail can't crowd out approved mail.
        to_process = []
        for uid in uids:
            _, header_bytes = _fetch(imap, uid, "(BODY.PEEK[HEADER.FIELDS (FROM MESSAGE-ID)])")
            headers = parse_email(header_bytes)
            if is_approved_sender(headers.from_headers, approved):
                to_process.append(uid)
            else:
                print(f"{headers.message_id} ignored")
                counts["ignored"] += 1

        # Pass 2: approved messages, up to the per-run limit.
        for uid in to_process[: gmail["max_emails_per_run"]]:
            label = f"UID {uid.decode()}"
            try:
                line, raw = _fetch(imap, uid, "(INTERNALDATE BODY.PEEK[])")
                email = parse_email(raw)
                label = msgid8(email.message_id)
                if email.received_at is None:
                    email = replace(email, received_at=_internal_date(line))
                state = run_email(graph, email, cfg)
                imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")  # only after the file exists
                kind = "ready" if state["decision"].route == READY else "review"
                counts[kind] += 1
                print(f"{label} {kind} {state['output_path']}")
            except Exception as e:  # log, leave unread, carry on with the next message
                counts["failed"] += 1
                print(f"{label} failed ({type(e).__name__}): {e}", file=sys.stderr)

        skipped = max(0, len(to_process) - gmail["max_emails_per_run"])
        if skipped:
            print(f"{skipped} approved message(s) left for the next run (max_emails_per_run)")
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    print(", ".join(f"{k} {v}" for k, v in counts.items()))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
