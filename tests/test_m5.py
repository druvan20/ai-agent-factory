"""Hermetic unit tests for Workflow 3 codegen helpers (mocked LLM)."""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def sample_task():
    return {
        "id": "T001",
        "title": "Create agent loop",
        "description": "Implement ReAct loop",
        "acceptance_criteria": ["Has run() method"],
        "target_files": ["agent/loop.py"],
        "dependencies": [],
        "pattern_references": ["ReAct"],
        "estimated_complexity": "medium",
        "order": 1,
    }


def test_pattern_context_react(sample_task):
    from app.workflows.w3_codegen import _build_pattern_context
    ctx = _build_pattern_context(sample_task)
    assert "ReAct" in ctx or "react" in ctx.lower()


def test_generate_single_shot_mocked(sample_task):
    from app.workflows import w3_codegen as w3

    with patch.object(w3, "_call_llm_json", return_value={
        "files": {"agent/loop.py": "def run():\n    pass\n"},
    }):
        files = w3._generate_single_shot(sample_task, {}, {}, "", {"run_id": "r", "project_id": "p"})
    assert "agent/loop.py" in files


def test_parallel_reviews_reduce(sample_task):
    from app.workflows import w3_codegen as w3

    with patch.object(w3, "_review_workflow", return_value={"passed": True, "issues": [], "severity": "low"}), \
         patch.object(w3, "_review_prompt", return_value={"passed": True, "issues": [], "severity": "low"}), \
         patch.object(w3, "_review_security", return_value={"passed": False, "issues": ["secret"], "severity": "high"}):
        result = w3._run_parallel_reviews(sample_task, {"a.py": "x = 1"}, {"run_id": "r"})
    assert result["passed"] is False
    assert any("secret" in i for i in result["issues"])


def test_w3_graph_compiles():
    from app.workflows.w3_codegen import build_w3_graph
    graph = build_w3_graph()
    mermaid = graph.get_graph().draw_mermaid()
    assert "task_loop" in mermaid or "run_task_unit" in mermaid or len(mermaid) > 10


def test_task_subgraph_compiles(sample_task):
    from app.workflows.w3_codegen import _build_task_subgraph
    sg = _build_task_subgraph()
    assert sg is not None
