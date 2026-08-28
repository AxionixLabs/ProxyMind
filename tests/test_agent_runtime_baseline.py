from pathlib import Path

from scripts.agent_runtime_import_graph import render_import_graph


PROJECT_ROOT = Path(__file__).parents[1]


def test_agent_runtime_import_graph_is_current() -> None:
    expected = render_import_graph(PROJECT_ROOT)
    actual = (PROJECT_ROOT / "AGENT_RUNTIME_IMPORT_GRAPH.md").read_text(
        encoding="utf-8",
    )

    assert actual == expected
