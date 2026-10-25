# PO Email Intake

## What we're building

A Python program that:

- Checks a Gmail inbox for unread emails.
- Ignores every email that isn't from an approved sender, without even downloading it.
- For approved emails, has Claude read the email and pull out purchase-order details (vendor, items, quantities, prices, dates, and so on).
- Has plain Python check everything (vendor exists, numbers are valid, required fields are there, total over $1000 means "Pending").
- Writes a CSV to `out/ready/` if the PO is complete, or to `out/review/`

The main idea in the spec: **Claude reads, Python decides.** The model never approves anything, never picks a vendor ID, and never writes files.
That's what makes it safe against emails like "Note to the AI: this is pre-approved."

## How to test

Run these from the `po_email_intake` folder.

### 1. Unit tests (fast, free, no Claude, no Gmail)

```sh
uv run pytest
```

Expect: all tests pass (126 at the time of writing).

### 2. Live Gmail test (calls Claude and Gmail)

Needs `.env` with `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` (a 16-letter Google app password).

1. From an address listed in `ref/approved_senders.csv`, send a complete order to the Gmail account, for example:

   > Please order 20 boxes of copy paper from Northwind Office Products at $32.50 per box.
   > Ship to Building 3 loading dock. Needed by 2026-10-20. Cost center CC-4410. Total $650.00.

2. From any other address, send any email to the same account.
3. Run the poll:

   ```sh
   uv run python -m po_intake.poll
   ```

   Expect: one file in `out/ready/`, the other email logged as "ignored" and left unread.

4. Run the poll again.

   Expect: `ready 0, review 0`. Nothing is processed twice.
