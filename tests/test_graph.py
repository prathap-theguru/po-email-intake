"""Graph wiring tests with stub models. No model, no network."""

from pathlib import Path

import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, ToolMessage

from po_intake.config import load_config
from po_intake.emailparse import parse_email
from po_intake.models import ExtractedLine, ExtractedPO, VendorChoice
from po_intake.graph import build_graph, run_email
from po_intake.refdata import load_vendors
from po_intake.vendor_tool import make_search_vendors
from po_intake.rules import EXTRACTION_FAILED, NO_BODY_TEXT, PASSED, READY, REVIEW, TOTAL_NOT_STATED

SAMPLES = Path("samples")


class StubExtractor:
    """Stands in for ChatAnthropic(...).with_structured_output(...)."""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def complete_small(**overrides):
    fields = dict(
        vendor_name="Northwind Office Products", ship_to="Building 3 loading dock", stated_total="650.00",
        lines=[ExtractedLine(description="copy paper", quantity="20", uom="box", unit_price="32.50",
                             need_by_date="2026-10-20", cost_center="CC-4410")],
        ambiguities=[],
    )
    fields.update(overrides)
    return ExtractedPO(**fields)


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config()
    cfg["paths"]["ready_dir"] = str(tmp_path / "ready")
    cfg["paths"]["review_dir"] = str(tmp_path / "review")
    return cfg


class StubAgent:
    """Stands in for Claude with tools. Asks for search_vendors every time if keep_searching."""

    def __init__(self, keep_searching=False):
        self.keep_searching = keep_searching
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.keep_searching:
            return AIMessage(content="", tool_calls=[
                {"name": "search_vendors", "args": {"query": "Northwind Office Products"}, "id": f"call_{self.calls}"},
            ])
        return AIMessage(content="Northwind Office Products matched.")


def run(cfg, case, extractor, agent=None, finalizer=None):
    vendors = load_vendors(cfg["paths"]["vendors_csv"])
    graph = build_graph(
        cfg, vendors, extractor,
        agent or StubAgent(),
        finalizer or StubExtractor(VendorChoice(name_as_written=None)),
        [make_search_vendors(vendors)],
    )
    return run_email(graph, parse_email((SAMPLES / f"{case}.eml").read_bytes()), cfg)


# --- Vendor loop -------------------------------------------------------------------

def test_loop_stops_at_cap(cfg):
    # The stub never stops asking for tools; the graph must stop it.
    agent = StubAgent(keep_searching=True)
    state = run(cfg, "complete_small", StubExtractor(complete_small()), agent=agent)
    cap = cfg["limits"]["max_tool_iterations"]
    tool_runs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_runs) == cap
    assert agent.calls == cap + 1  # the last answer goes to vendor_finalize, not the tools
    assert state["decision"].route == READY  # finalize still ran, then validate


def test_no_vendor_named_skips_loop(cfg):
    agent = StubAgent()
    state = run(cfg, "vendor_missing", StubExtractor(complete_small(vendor_name=None)), agent=agent)
    assert agent.calls == 0
    assert not state.get("messages")


def test_chosen_name_from_email_is_used(cfg):
    # Extraction got the name wrong, the vendor loop found the right one in the email.
    finalizer = StubExtractor(VendorChoice(name_as_written="Northwind Office Products"))
    state = run(cfg, "complete_small", StubExtractor(complete_small(vendor_name="Northwind")),
                finalizer=finalizer)
    assert state["decision"].vendor.vendor_id == "V1077"


def test_chosen_name_not_in_email_is_ignored(cfg):
    # A name the email never mentions must not pick a vendor.
    finalizer = StubExtractor(VendorChoice(name_as_written="Acme Supply"))
    state = run(cfg, "complete_small", StubExtractor(complete_small()), finalizer=finalizer)
    assert state["decision"].vendor.vendor_id == "V1077"  # fell back to the extracted name


# --- Main path ----------------------------------------------------------------------


def test_ready_path(cfg):
    stub = StubExtractor(complete_small())
    state = run(cfg, "complete_small", stub)
    assert stub.calls == 1
    assert state["decision"].route == READY
    assert state["decision"].approval_status == PASSED
    assert Path(state["output_path"]).parent == Path(cfg["paths"]["ready_dir"])
    assert state["output_path"].endswith("_PO_ready.csv")


def test_review_path(cfg):
    state = run(cfg, "total_missing", StubExtractor(complete_small(stated_total=None)))
    assert state["decision"].route == REVIEW
    assert [i.code for i in state["decision"].issues] == [TOTAL_NOT_STATED]
    assert Path(state["output_path"]).parent == Path(cfg["paths"]["review_dir"])


def test_empty_body_makes_no_model_call(cfg):
    stub = StubExtractor(complete_small())
    state = run(cfg, "empty_body", stub)
    assert stub.calls == 0
    assert [i.code for i in state["decision"].issues] == [NO_BODY_TEXT]
    assert state["output_path"].endswith("_review.csv")


def test_body_too_long_makes_no_model_call(cfg):
    cfg["limits"]["max_body_chars"] = 10
    stub = StubExtractor(complete_small())
    state = run(cfg, "complete_small", stub)
    assert stub.calls == 0
    assert state["decision"].route == REVIEW


def test_invalid_output_twice_is_extraction_failed(cfg):
    stub = StubExtractor(OutputParserException("bad json"))
    state = run(cfg, "complete_small", stub)
    assert stub.calls == 2  # first try + one retry
    assert [i.code for i in state["decision"].issues] == [EXTRACTION_FAILED]
    assert state["output_path"].endswith("_review.csv")


def test_api_error_propagates_and_writes_nothing(cfg, tmp_path):
    with pytest.raises(ConnectionError):
        run(cfg, "complete_small", StubExtractor(ConnectionError("API down")))
    assert not (tmp_path / "ready").exists() and not (tmp_path / "review").exists()
