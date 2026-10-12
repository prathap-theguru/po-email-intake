"""Pydantic models for what Claude returns (spec section 2).

These describe the *shape* of the model's output only. Business rules
(positive quantity, valid date, vendor match, ...) live in rules.py.

extra="forbid" means an unexpected field is rejected. It also produces
additionalProperties: false in the JSON schema, which Claude's structured
output feature requires.
"""

from pydantic import BaseModel, ConfigDict


class ExtractedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None
    quantity: str | None  # number text as written, no currency symbol
    uom: str | None  # singular, e.g. box, each
    unit_price: str | None  # number text as written, no currency symbol
    need_by_date: str | None  # ISO YYYY-MM-DD, only if the email gives an explicit date
    cost_center: str | None


class ExtractedPO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_name: str | None  # as written in the email
    ship_to: str | None
    stated_total: str | None  # order total only if the email states it; never computed by the model
    lines: list[ExtractedLine]
    ambiguities: list[str]  # anything unclear, conflicting, or outside scope


class VendorChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name_as_written: str | None  # must be a vendor name that appears in the email
