from langgraph.checkpoint.memory import InMemorySaver

from invoice_guard.graph.builder import build_graph


def test_graph_compiles_with_expected_nodes():
    graph = build_graph()
    names = set(graph.get_graph().nodes)
    for node in ["ingest", "ocr", "extract", "validate", "repair", "human_review", "post_erp"]:
        assert node in names


def test_run_pauses_for_human_review():
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "inv-1"}}
    result = graph.invoke({"invoice_id": "inv-1"}, config)
    assert "__interrupt__" in result
