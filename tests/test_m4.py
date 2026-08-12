"""Hermetic unit tests for Workflow 2 planning helpers (mocked LLM / RAG)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def sample_requirements():
    return {
        "goals": ["Build an agent factory"],
        "functional_requirements": [
            {"id": "FR1", "title": "Upload docs", "description": "Upload BRD"},
            {"id": "FR2", "title": "Plan", "description": "Plan architecture"},
            {"id": "FR3", "title": "Generate", "description": "Generate code"},
            {"id": "FR4", "title": "HITL", "description": "Approvals"},
            {"id": "FR5", "title": "RAG", "description": "Retrieve context"},
            {"id": "FR6", "title": "Patterns", "description": "Select patterns"},
        ],
        "non_functional_requirements": [
            {"id": "NFR1", "title": "Latency", "description": "p95 < 2s"},
        ],
    }


def test_assess_complexity_routes_heavyweight(sample_requirements):
    from app.workflows.w2_planning import assess_complexity

    out = assess_complexity({
        "project_id": "p1",
        "requirements_json": sample_requirements,
    })
    assert out["route"] == "heavyweight"
    assert out["complexity_score"] >= 6


def test_assess_complexity_routes_lightweight():
    from app.workflows.w2_planning import assess_complexity

    out = assess_complexity({
        "project_id": "p1",
        "requirements_json": {
            "functional_requirements": [{"id": "FR1", "title": "a", "description": "b"}],
            "non_functional_requirements": [],
        },
    })
    assert out["route"] == "lightweight"


def test_select_patterns_mocked(sample_requirements):
    from app.workflows import w2_planning as w2

    with patch.object(w2, "_search_kb_rag", return_value=[
        {"source": "[kb:ReAct]", "text": "ReAct pattern", "score": 0.9},
    ]), patch.object(w2, "_call_llm_json", return_value={
        "selected_patterns": [{
            "name": "ReAct",
            "rationale": "tool use",
            "addresses_requirements": ["FR1"],
            "citation": "[kb:ReAct]",
        }],
        "rejection_log": [],
    }):
        out = w2.select_patterns({
            "project_id": "p1",
            "run_id": "r1",
            "requirements_json": sample_requirements,
        })
    assert out["selected_patterns"][0]["name"] == "ReAct"


def test_parallel_research_branches_reducer_shape(sample_requirements):
    from app.workflows import w2_planning as w2

    with patch.object(w2, "_search_doc_rag", return_value=[
        {"source": "[doc:Intro]", "text": "hello", "kind": "doc"},
    ]), patch.object(w2, "_search_kb_rag", return_value=[
        {"source": "[kb:RAG]", "text": "rag", "kind": "kb"},
    ]), patch.object(w2.openai_web_search, "invoke", return_value="web finding"):
        docs = w2.research_docs({"project_id": "p1", "requirements_json": sample_requirements})
        kb = w2.research_kb({"selected_patterns": [{"name": "RAG"}], "requirements_json": sample_requirements})
        web = w2.research_web({"selected_patterns": [{"name": "RAG"}], "requirements_json": sample_requirements})

    assert docs["doc_research"][0]["kind"] == "doc"
    assert kb["kb_research"][0]["kind"] == "kb"
    assert web["web_research"][0]["kind"] == "web"


def test_w2_graph_compiles():
    from app.workflows.w2_planning import build_w2_graph
    graph = build_w2_graph()
    g = graph.get_graph()
    assert g is not None
    mermaid = g.draw_mermaid()
    assert "assess_complexity" in mermaid or "select_patterns" in mermaid.lower() or len(mermaid) > 10
