"""The per-email LangGraph graph (spec section 2).

    START -> extract --(no vendor named)--------------------------> validate_and_route
                     --(vendor named)--> vendor_agent <-> vendor_tools
                                         vendor_agent --(done or cap)--> vendor_finalize -> validate_and_route
    validate_and_route -> write_output -> END

Claude runs in extract, vendor_agent and vendor_finalize. validate_and_route
is plain Python and is the only place a route, vendor ID or approval status
is decided.
"""

from decimal import Decimal
from typing import Annotated, TypedDict

from langchain_core.exceptions import OutputParserException
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import ValidationError

from po_intake.emailparse import ParsedEmail
from po_intake.extract import ExtractionFailed, extract
from po_intake.models import ExtractedPO, VendorChoice
from po_intake.output import write_ready, write_review
from po_intake.refdata import Vendor
from po_intake.rules import (
    EXTRACTION_FAILED,
    READY,
    Decision,
    Issue,
    check_body,
    review_without_extraction,
    validate,
)
from po_intake.vendor_agent import finalize_messages, first_messages


class POState(TypedDict, total=False):
    email: ParsedEmail  # input
    extracted: ExtractedPO | None  # from extract; None if no usable extraction
    pre_issue: Issue | None  # NO_BODY_TEXT, BODY_TOO_LONG or EXTRACTION_FAILED
    messages: Annotated[list, add_messages]  # vendor loop conversation; new messages are appended
    agent_calls: int  # how many times vendor_agent has run
    vendor_choice: VendorChoice | None  # from vendor_finalize
    decision: Decision  # from validate_and_route
    output_path: str  # from write_output


def build_graph(cfg: dict, vendors: list[Vendor], extractor, vendor_agent, vendor_finalizer, tools: list):
    """Compile the graph. The three model objects can be real (see samples.py) or test stubs."""
    threshold = Decimal(cfg["business"]["approval_threshold"])
    timezone = cfg["business"]["timezone"]
    max_body_chars = cfg["limits"]["max_body_chars"]
    max_tool_iterations = cfg["limits"]["max_tool_iterations"]
    paths = cfg["paths"]

    def extract_node(state: POState) -> POState:
        body = state["email"].body
        issue = check_body(body, max_body_chars)
        if issue:  # empty or too long: no model call
            return {"extracted": None, "pre_issue": issue}
        try:
            return {"extracted": extract(extractor, body), "pre_issue": None}
        except ExtractionFailed as e:
            return {"extracted": None,
                    "pre_issue": Issue(EXTRACTION_FAILED, f"The model output was invalid twice: {e}")}

    def after_extract(state: POState) -> str:
        extracted = state["extracted"]
        if extracted is None or not (extracted.vendor_name or "").strip():
            return "validate_and_route"  # nothing to look up
        return "vendor_agent"

    def vendor_agent_node(state: POState) -> POState:
        history = state.get("messages") or []
        new = [] if history else first_messages(state["email"].body)
        response = vendor_agent.invoke(history + new)
        return {"messages": new + [response], "agent_calls": state.get("agent_calls", 0) + 1}

    def after_vendor_agent(state: POState) -> str:
        # The cap lives here: tools run at most max_tool_iterations times.
        wants_tools = bool(getattr(state["messages"][-1], "tool_calls", None))
        if wants_tools and state["agent_calls"] <= max_tool_iterations:
            return "vendor_tools"
        return "vendor_finalize"

    def vendor_finalize_node(state: POState) -> POState:
        try:
            choice = vendor_finalizer.invoke(finalize_messages(state["email"].body, state["messages"]))
        except (OutputParserException, ValidationError):
            choice = None  # fall back to the extracted vendor name
        return {"vendor_choice": choice}

    def validate_and_route(state: POState) -> POState:
        if state["pre_issue"]:
            return {"decision": review_without_extraction(state["pre_issue"])}
        choice = state.get("vendor_choice")
        chosen = choice.name_as_written if choice else None
        # The proposed name must appear in the email; otherwise it is ignored.
        if chosen and chosen.casefold() not in state["email"].body.casefold():
            chosen = None
        return {"decision": validate(state["extracted"], vendors, threshold, chosen_vendor_name=chosen)}

    def write_output(state: POState) -> POState:
        # Follows the route validate_and_route already decided; decides nothing itself.
        decision = state["decision"]
        if decision.route == READY:
            path = write_ready(state["email"], decision, paths["ready_dir"],
                               cfg["business"]["buyer_id"], timezone)
        else:
            path = write_review(state["email"], decision, state["extracted"],
                                paths["review_dir"], timezone)
        return {"output_path": str(path)}

    g = StateGraph(POState)
    g.add_node("extract", extract_node)
    g.add_node("vendor_agent", vendor_agent_node)
    g.add_node("vendor_tools", ToolNode(tools))
    g.add_node("vendor_finalize", vendor_finalize_node)
    g.add_node("validate_and_route", validate_and_route)
    g.add_node("write_output", write_output)

    g.add_edge(START, "extract")
    g.add_conditional_edges("extract", after_extract, ["vendor_agent", "validate_and_route"])
    g.add_conditional_edges("vendor_agent", after_vendor_agent, ["vendor_tools", "vendor_finalize"])
    g.add_edge("vendor_tools", "vendor_agent")
    g.add_edge("vendor_finalize", "validate_and_route")
    g.add_edge("validate_and_route", "write_output")
    g.add_edge("write_output", END)
    return g.compile()


def run_email(graph, email: ParsedEmail, cfg: dict) -> POState:
    """Run one email. Raises on API errors and GraphRecursionError: the caller
    leaves the email unread and counts it as failed."""
    return graph.invoke({"email": email}, config={"recursion_limit": cfg["limits"]["recursion_limit"]})
