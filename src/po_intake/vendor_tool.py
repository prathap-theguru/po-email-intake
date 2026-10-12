"""The search_vendors tool Claude can call in the vendor loop (spec section 2).

Read-only: it returns data and changes nothing. Exact matches only (project
change: no fuzzy matching), so a typo finds nothing.
"""

from langchain_core.tools import tool

from po_intake.normalize import normalize_vendor_name
from po_intake.refdata import Vendor

MAX_RESULTS = 5


def make_search_vendors(vendors: list[Vendor]):
    """Build the tool with this run's vendor list."""

    @tool
    def search_vendors(query: str) -> list[dict]:
        """Search vendors.csv. Returns up to 5 entries with vendor_id and vendor_name."""
        wanted = normalize_vendor_name(query)
        if not wanted:
            return []
        return [
            {"vendor_id": v.vendor_id, "vendor_name": v.vendor_name}
            for v in vendors
            if v.normalized == wanted
        ][:MAX_RESULTS]

    return search_vendors
