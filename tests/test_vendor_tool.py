"""Tests for the search_vendors tool. No model, no network."""

from po_intake.refdata import load_vendors
from po_intake.vendor_tool import make_search_vendors

search = make_search_vendors(load_vendors("ref/vendors.csv"))


def test_exact_name_found():
    assert search.invoke({"query": "Acme Supply"}) == [{"vendor_id": "V1042", "vendor_name": "Acme Supply Co."}]


def test_normalized_match():
    assert search.invoke({"query": "NORTHWIND OFFICE PRODUCTS, LLC"})[0]["vendor_id"] == "V1077"


def test_typo_finds_nothing():
    assert search.invoke({"query": "Acme Suply"}) == []


def test_empty_query_finds_nothing():
    assert search.invoke({"query": "   "}) == []


def test_tool_has_name_and_description_for_claude():
    assert search.name == "search_vendors"
    assert "vendors.csv" in search.description
