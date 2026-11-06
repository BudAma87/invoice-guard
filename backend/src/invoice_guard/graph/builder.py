"""Builds the invoice validation StateGraph."""

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from invoice_guard.graph import nodes as n
from invoice_guard.graph import routing as r
from invoice_guard.graph.state import InvoiceState

LLM_RETRY = RetryPolicy(max_attempts=4, initial_interval=2.0, backoff_factor=2.0, jitter=True)
IO_RETRY = RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0, jitter=True)


def build_graph(checkpointer=None):
    g = StateGraph(InvoiceState)

    g.add_node("ingest", n.ingest, retry_policy=IO_RETRY)
    g.add_node("preprocess", n.preprocess)
    g.add_node("ocr", n.ocr, retry_policy=IO_RETRY)
    g.add_node("supplier_template", n.supplier_template)
    g.add_node("extract", n.extract, retry_policy=LLM_RETRY)
    g.add_node("normalise", n.normalise)
    g.add_node("validate", n.validate)
    g.add_node("repair", n.repair, retry_policy=LLM_RETRY)
    g.add_node("dedupe", n.dedupe)
    g.add_node("decide", n.decide)
    g.add_node("human_review", n.human_review)
    g.add_node("checker", n.checker)
    g.add_node("post_erp", n.post_erp, retry_policy=IO_RETRY)
    g.add_node("close_rejected", n.close_rejected)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "preprocess")
    g.add_edge("preprocess", "ocr")
    g.add_edge("ocr", "supplier_template")
    g.add_conditional_edges("supplier_template", r.after_template, ["extract", "normalise"])
    g.add_edge("extract", "normalise")
    g.add_edge("normalise", "validate")
    g.add_conditional_edges("validate", r.after_validate, ["repair", "dedupe"])
    g.add_edge("repair", "normalise")
    g.add_edge("dedupe", "decide")
    g.add_conditional_edges("decide", r.after_decide, ["post_erp", "human_review"])
    g.add_conditional_edges(
        "human_review", r.after_review, ["checker", "post_erp", "close_rejected"]
    )
    g.add_conditional_edges("checker", r.after_checker, ["post_erp", "human_review"])
    g.add_edge("post_erp", END)
    g.add_edge("close_rejected", END)

    return g.compile(checkpointer=checkpointer)
