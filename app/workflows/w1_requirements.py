"""Workflow 1 — Requirements Gathering.

LangGraph state graph:
  extract_requirements → gap_analysis → [clarification_gate] → compile_spec → [approval_gate]

Demonstrates:
  - State schema with typed reducers
  - Conditional edges
  - HITL via interrupt (clarification + approval)
  - SQLite checkpointing for resumability
  - Reflection/Self-Critique pattern (gap analysis)
"""
from __future__ import annotations

import json
import logging
import operator
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, cast

from langgraph.graph import StateGraph, START, END  # type: ignore[import-untyped]
from langgraph.types import interrupt, Command  # type: ignore[import-untyped]

from app.config import get_settings
from app.workflows.hooks import wrap_nodes

logger = logging.getLogger(__name__)


# ───────────────────── State Schema ─────────────────────

class W1State(dict):
    """Workflow 1 state, stored as a dict for LangGraph compatibility.

    Keys:
        project_id: str
        doc_texts: list[str]            - raw document texts
        extracted: dict                  - extracted requirements (structured)
        gaps: list[str]                  - identified gaps/questions
        clarification_round: int         - current round counter
        max_rounds: int                  - cap
        clarifications: list[dict]       - user answers accumulated
        rejection_feedback: str          - feedback from rejected approval
        spec_markdown: str               - final Markdown spec
        spec_json: dict                  - final structured JSON spec
        status: str                      - current node status
        error: str | None
    """
    pass


DEFAULT_STATE = {
    "project_id": "",
    "doc_texts": [],
    "extracted": {},
    "gaps": [],
    "clarification_round": 0,
    "max_rounds": 3,
    "clarifications": [],
    "rejection_feedback": "",
    "spec_markdown": "",
    "spec_json": {},
    "status": "pending",
    "error": None,
}


# ───────────────────── LLM helpers ─────────────────────

def _ctx(state: dict | None = None) -> tuple[str, str, str]:
    s = state or {}
    return s.get("run_id", ""), s.get("project_id", ""), s.get("_node", "")


def _call_llm(system: str, user: str, state: dict | None = None) -> str:
    from app.services.token_tracer import traced_chat_completion
    run_id, project_id, node = _ctx(state)
    content, _ = traced_chat_completion(
        system=system, user=user, run_id=run_id, project_id=project_id, node=node,
    )
    return content


def _call_llm_json(system: str, user: str, state: dict | None = None) -> dict:
    from app.services.token_tracer import traced_chat_completion
    run_id, project_id, node = _ctx(state)
    content, _ = traced_chat_completion(
        system=system, user=user, response_format={"type": "json_object"},
        run_id=run_id, project_id=project_id, node=node,
    )
    return json.loads(content or "{}")


# ───────────────────── Graph Nodes ─────────────────────

def extract_requirements(state: W1State) -> dict:
    """Node: Extract structured requirements from document texts."""
    logger.info("[W1] extract_requirements: project=%s", state.get("project_id"))

    doc_texts = state.get("doc_texts", [])
    combined = "\n\n---\n\n".join(doc_texts)
    if not combined.strip():
        return {"error": "No document text available", "status": "failed"}

    prior_clarifications = state.get("clarifications", [])
    clarification_ctx = ""
    if prior_clarifications:
        parts = []
        for c in prior_clarifications:
            for q, a in c.items():
                parts.append(f"Q: {q}\nA: {a}")
        clarification_ctx = "\n\nPrevious clarifications from the user:\n" + "\n".join(parts)

    rejection_fb = state.get("rejection_feedback", "")
    rejection_ctx = ""
    if rejection_fb:
        rejection_ctx = f"\n\nThe previous spec was rejected with this feedback: {rejection_fb}\nAddress this feedback in the new extraction."

    system = (
        "You are a senior requirements analyst. Extract structured requirements "
        "from the provided documents. Output valid JSON with these keys: "
        "goals (list[str]), personas (list[str]), "
        "functional_requirements (list[{{id, title, description, priority}}]), "
        "non_functional_requirements (list[{{id, title, description, category}}]), "
        "constraints (list[str]), out_of_scope (list[str]), assumptions (list[str])."
    )
    user_prompt = f"Documents:\n{combined[:30000]}{clarification_ctx}{rejection_ctx}"

    try:
        extracted = _call_llm_json(system, user_prompt, state)
    except Exception as exc:
        logger.error("[W1] extract_requirements LLM error: %s", exc)
        return {"error": str(exc), "status": "failed"}

    logger.info("[W1] Extracted %d FRs, %d NFRs",
                len(extracted.get("functional_requirements", [])),
                len(extracted.get("non_functional_requirements", [])))
    return {"extracted": extracted, "status": "extracting"}


def gap_analysis(state: W1State) -> dict:
    """Node: Reflection/Self-Critique — find gaps in extracted requirements."""
    logger.info("[W1] gap_analysis: project=%s", state.get("project_id"))

    extracted = state.get("extracted", {})
    if not extracted:
        return {"gaps": [], "status": "no_extraction"}

    system = (
        "You are a requirements quality reviewer. Analyze the extracted requirements "
        "and identify gaps, ambiguities, or missing information that need clarification "
        "from the user. Output valid JSON: {\"gaps\": [\"question1\", \"question2\", ...]}. "
        "Each gap should be a clear, specific question. If the requirements are complete "
        "and unambiguous, return {\"gaps\": []}."
    )
    user_prompt = f"Extracted requirements:\n{json.dumps(extracted, indent=2)}"

    prior_clarifications = state.get("clarifications", [])
    if prior_clarifications:
        user_prompt += "\n\nAlready clarified:\n" + json.dumps(prior_clarifications, indent=2)
        user_prompt += "\nDo NOT repeat questions already answered. Only raise NEW gaps."

    try:
        result = _call_llm_json(system, user_prompt, state)
        gaps = result.get("gaps", [])
    except Exception as exc:
        logger.error("[W1] gap_analysis LLM error: %s", exc)
        gaps = []

    logger.info("[W1] Found %d gaps", len(gaps))
    return {"gaps": gaps, "status": "analyzing"}


def clarification_gate(state: W1State) -> dict:
    """Node: HITL interrupt — pause for user to answer gap questions.

    Uses LangGraph interrupt() to pause execution. The workflow runner
    will detect this and surface the questions over WebSocket or REST.
    """
    gaps = state.get("gaps", [])
    current_round = state.get("clarification_round", 0)
    max_rounds = state.get("max_rounds", 3)

    logger.info("[W1] clarification_gate: %d gaps, round %d/%d", len(gaps), current_round, max_rounds)

    if not gaps or current_round >= max_rounds:
        return {"status": "compiling"}

    human_response = interrupt({
        "type": "clarification",
        "gaps": gaps,
        "round": current_round + 1,
        "max_rounds": max_rounds,
    })

    answers = human_response if isinstance(human_response, dict) else {}
    new_clarifications = state.get("clarifications", [])
    new_clarifications = list(new_clarifications) + [answers]

    logger.info("[W1] Received %d answers in round %d", len(answers), current_round + 1)
    return {
        "clarifications": new_clarifications,
        "clarification_round": current_round + 1,
        "status": "clarified",
    }


def compile_spec(state: W1State) -> dict:
    """Node: Compile final requirements specification (Markdown + JSON)."""
    logger.info("[W1] compile_spec: project=%s", state.get("project_id"))

    extracted = state.get("extracted", {})
    clarifications = state.get("clarifications", [])

    system = (
        "You are a senior requirements analyst. Compile the final requirements specification. "
        "Output valid JSON with two keys:\n"
        "  \"markdown\": A complete Markdown document with sections: "
        "Goals, Personas, Functional Requirements, Non-Functional Requirements, "
        "Constraints, Out-of-Scope, Assumptions, Open Questions.\n"
        "  \"structured\": The same content as structured JSON with keys: "
        "goals, personas, functional_requirements, non_functional_requirements, "
        "constraints, out_of_scope, assumptions, open_questions."
    )
    user_prompt = f"Extracted requirements:\n{json.dumps(extracted, indent=2)}"
    if clarifications:
        user_prompt += f"\n\nClarifications received:\n{json.dumps(clarifications, indent=2)}"

    rejection_fb = state.get("rejection_feedback", "")
    if rejection_fb:
        user_prompt += f"\n\nPrevious rejection feedback to address: {rejection_fb}"

    try:
        result = _call_llm_json(system, user_prompt, state)
        md = result.get("markdown", "")
        structured = result.get("structured", extracted)
    except Exception as exc:
        logger.error("[W1] compile_spec LLM error: %s", exc)
        md = f"# Requirements Specification\n\n{json.dumps(extracted, indent=2)}"
        structured = extracted

    logger.info("[W1] Spec compiled: %d chars markdown", len(md))
    return {
        "spec_markdown": md,
        "spec_json": structured,
        "status": "pending_approval",
    }


def approval_gate(state: W1State) -> dict:
    """Node: HITL interrupt — pause for user approval."""
    logger.info("[W1] approval_gate: project=%s", state.get("project_id"))

    human_response = interrupt({
        "type": "approval",
        "spec_markdown": state.get("spec_markdown", ""),
    })

    approved = human_response.get("approved", False) if isinstance(human_response, dict) else False
    feedback = human_response.get("feedback", "") if isinstance(human_response, dict) else ""

    if approved:
        logger.info("[W1] Spec APPROVED")
        return {"status": "approved"}
    else:
        logger.info("[W1] Spec REJECTED with feedback: %s", feedback[:200])
        return {
            "status": "rejected",
            "rejection_feedback": feedback,
        }


# ───────────────────── Routing logic ─────────────────────

def after_gap_analysis(state: W1State) -> str:
    """Conditional edge: if gaps found and under round cap → clarification, else compile."""
    gaps = state.get("gaps", [])
    current_round = state.get("clarification_round", 0)
    max_rounds = state.get("max_rounds", 3)

    if gaps and current_round < max_rounds:
        return "clarification_gate"
    return "compile_spec"


def after_clarification(state: W1State) -> str:
    """After clarification, re-extract to incorporate answers."""
    return "extract_requirements"


def after_approval(state: W1State) -> str:
    """Conditional edge: approved → end, rejected → re-extract with feedback."""
    if state.get("status") == "approved":
        return END
    return "extract_requirements"


# ───────────────────── Build Graph ─────────────────────

def _build_w1_builder() -> Any:
    builder: Any = StateGraph(dict)

    wrap_nodes(builder, {
        "extract_requirements": extract_requirements,
        "gap_analysis": gap_analysis,
        "clarification_gate": clarification_gate,
        "compile_spec": compile_spec,
        "approval_gate": approval_gate,
    })

    builder.add_edge(START, "extract_requirements")
    builder.add_edge("extract_requirements", "gap_analysis")
    builder.add_conditional_edges("gap_analysis", after_gap_analysis, ["clarification_gate", "compile_spec"])
    builder.add_edge("clarification_gate", "extract_requirements")
    builder.add_edge("compile_spec", "approval_gate")
    builder.add_conditional_edges("approval_gate", after_approval, ["extract_requirements", END])

    return builder


def build_w1_graph() -> Any:
    """Build and return the compiled W1 graph (without checkpointer)."""
    return _build_w1_builder().compile()


def build_w1_graph_with_checkpointer() -> tuple[Any, Any]:
    """Build W1 graph with SQLite checkpointer (AsyncSqliteSaver DB file)."""
    from app.workflows.checkpointer import get_sqlite_checkpointer

    builder: Any = _build_w1_builder()
    checkpointer = get_sqlite_checkpointer()
    graph = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["clarification_gate", "approval_gate"],
    )
    return graph, checkpointer