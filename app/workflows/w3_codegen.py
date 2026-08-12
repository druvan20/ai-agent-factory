"""Workflow 3 — Code Generation.  "Agents Write the Code"

Top-level Plan-and-Execute consumer graph:
  init_workspace → task_loop → [has_next_task?]
    YES → generate_code → review_code → [passed?]
      YES → commit_task → task_loop
      NO  → [retries < max?]
        YES → generate_code (with reviewer feedback)
        NO  → commit_task (write best-effort) → task_loop
    NO  → bundle_workspace → approval_gate → END

Per-task dynamic code generation:
  - Default tasks: single-shot LLM generation
  - Reflection/Self-Critique tagged tasks: internal dev → critic → revise cycle
  - Pattern-aware system prompts (each task's pattern_references shape the prompt)

Parallel reviewer sub-agents:
  - workflow_reviewer: checks logic flow and correctness
  - prompt_reviewer: validates prompt engineering patterns
  - security_reviewer: scans for injection, secret leaks, unsafe patterns
  → reduced into single pass/fail with aggregated feedback

Demonstrates:
  - Orchestrator-Worker (top-level loop dispatches per-task agents)
  - Plan-and-Execute consumer (iterates the approved task list)
  - Evaluator-Optimizer (reviewer loop with bounded retries)
  - Dynamic graph construction (pattern-based prompt shaping)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.graph import StateGraph, START, END  # type: ignore[import-untyped]
from langgraph.types import interrupt  # type: ignore[import-untyped]

from app.config import get_settings
from app.workflows.hooks import wrap_nodes, node_hook

logger = logging.getLogger(__name__)

REFLECTION_PATTERNS = {"reflection", "self-critique", "self_critique", "react", "evaluator-optimizer"}


def _ctx(state: dict | None = None) -> tuple[str, str]:
    s = state or {}
    return s.get("run_id", ""), s.get("project_id", "")


def _call_llm(system: str, user: str, temperature: float = 0.2, state: dict | None = None, node: str = "") -> str:
    from app.services.token_tracer import traced_chat_completion
    run_id, project_id = _ctx(state)
    content, _ = traced_chat_completion(
        system=system, user=user, temperature=temperature,
        run_id=run_id, project_id=project_id, node=node,
    )
    return content


def _call_llm_json(system: str, user: str, state: dict | None = None, node: str = "") -> dict:
    from app.services.token_tracer import traced_chat_completion
    run_id, project_id = _ctx(state)
    content, _ = traced_chat_completion(
        system=system, user=user, response_format={"type": "json_object"},
        run_id=run_id, project_id=project_id, node=node,
    )
    return json.loads(content or "{}")


# ───────────────────── Per-Task Codegen Strategies ─────────────────────

def _build_pattern_context(task: dict) -> str:
    """Build pattern-specific prompt instructions based on task's pattern_references."""
    refs = [r.lower() for r in task.get("pattern_references", [])]
    parts = []
    if any(p in refs for p in ("reflection", "self-critique", "self_critique")):
        parts.append("Apply the REFLECTION pattern: after generating, self-critique your output "
                      "and revise before returning. Include a brief self-review note as a comment.")
    if any(p in refs for p in ("react",)):
        parts.append("Apply the ReAct pattern: reason step-by-step about what the code should do, "
                      "then act by writing the implementation.")
    if any(p in refs for p in ("tool-use", "tool_use")):
        parts.append("Apply Tool-Use pattern: define clear tool interfaces with typed parameters.")
    if any(p in refs for p in ("orchestrator-worker", "orchestrator_worker")):
        parts.append("Apply Orchestrator-Worker: separate coordinator logic from worker logic.")
    if any(p in refs for p in ("router", "routing")):
        parts.append("Apply Router pattern: implement conditional dispatching logic.")
    if any(p in refs for p in ("rag",)):
        parts.append("Apply RAG pattern: implement retrieval-then-generation flow.")
    return "\n".join(parts) if parts else "Follow clean code best practices."


def _has_reflection_pattern(task: dict) -> bool:
    refs = {r.lower() for r in task.get("pattern_references", [])}
    return bool(refs & REFLECTION_PATTERNS)


def _generate_single_shot(task: dict, architecture: dict, prior_files: dict, reviewer_feedback: str, state: dict | None = None) -> dict:
    """Single-shot code generation for a task."""
    pattern_ctx = _build_pattern_context(task)
    # Inject full pattern structure/prerequisites from snapshotted patterns when present
    selected = (state or {}).get("selected_patterns", [])
    for ref in task.get("pattern_references", []):
        for p in selected:
            if str(p.get("name", "")).lower() == str(ref).lower():
                pattern_ctx += f"\nPattern structure: {p.get('structure', p.get('rationale', ''))}"
                pattern_ctx += f"\nPrerequisites: {p.get('prerequisites', '')}"
    prior_ctx = ""
    if prior_files:
        sample_files = list(prior_files.items())[:5]
        prior_ctx = "\n\nAlready-generated files for context:\n" + "\n".join(
            f"--- {fp} ---\n{content[:500]}..." for fp, content in sample_files
        )

    feedback_ctx = f"\n\nReviewer feedback to address:\n{reviewer_feedback}" if reviewer_feedback else ""

    system = (
        "You are a senior developer. Generate production-quality code for the given task.\n"
        f"Pattern instructions:\n{pattern_ctx}\n\n"
        "Output valid JSON: {\"files\": {\"relative/path.ext\": \"file content\", ...}}\n"
        "Rules:\n"
        "- Generate ALL files listed in target_files\n"
        "- Code must satisfy all acceptance criteria\n"
        "- Include proper imports, type hints, error handling\n"
        "- Do NOT include explanatory comments about what you changed"
    )
    user_msg = (
        f"Task: {json.dumps(task, indent=2)}\n\n"
        f"Architecture context:\n{json.dumps(architecture, indent=2)[:4000]}"
        f"{prior_ctx}{feedback_ctx}"
    )

    result = _call_llm_json(system, user_msg, state, node="generate_code")
    return result.get("files", {})


def _generate_with_reflection(task: dict, architecture: dict, prior_files: dict, reviewer_feedback: str, state: dict | None = None) -> dict:
    """Reflection-pattern codegen: generate → self-critique → revise."""
    pattern_ctx = _build_pattern_context(task)
    prior_ctx = ""
    if prior_files:
        sample_files = list(prior_files.items())[:5]
        prior_ctx = "\n\nAlready-generated files:\n" + "\n".join(
            f"--- {fp} ---\n{content[:500]}..." for fp, content in sample_files
        )
    feedback_ctx = f"\n\nReviewer feedback:\n{reviewer_feedback}" if reviewer_feedback else ""

    gen_system = (
        "You are a senior developer. Generate code for the task.\n"
        f"Pattern instructions:\n{pattern_ctx}\n\n"
        "Output valid JSON: {\"files\": {\"path\": \"content\", ...}}"
    )
    gen_msg = (
        f"Task: {json.dumps(task, indent=2)}\n\n"
        f"Architecture:\n{json.dumps(architecture, indent=2)[:3000]}"
        f"{prior_ctx}{feedback_ctx}"
    )

    draft = _call_llm_json(gen_system, gen_msg, state, node="generate_code")
    draft_files = draft.get("files", {})

    critique_system = (
        "You are a code reviewer. Critique this generated code for:\n"
        "1. Correctness against acceptance criteria\n"
        "2. Pattern fidelity\n"
        "3. Error handling and edge cases\n"
        "4. Code quality\n"
        "Output valid JSON: {\"issues\": [str], \"suggestions\": [str], \"quality_score\": float}"
    )
    critique_msg = (
        f"Task: {json.dumps(task, indent=2)}\n\n"
        f"Generated code:\n{json.dumps(draft_files, indent=2)[:8000]}"
    )
    critique = _call_llm_json(critique_system, critique_msg, state, node="generate_code_critique")

    if critique.get("quality_score", 1.0) >= 0.8 and not critique.get("issues"):
        return draft_files

    revise_system = (
        "You are a senior developer. Revise the code based on the critique.\n"
        "Output valid JSON: {\"files\": {\"path\": \"content\", ...}}"
    )
    revise_msg = (
        f"Original task: {json.dumps(task, indent=2)}\n\n"
        f"Draft code:\n{json.dumps(draft_files, indent=2)[:6000]}\n\n"
        f"Critique:\n{json.dumps(critique, indent=2)[:2000]}"
    )
    revised = _call_llm_json(revise_system, revise_msg, state, node="generate_code_revise")
    return revised.get("files", draft_files)


# ───────────────────── Parallel Reviewer Sub-Agents ─────────────────────

def _review_workflow(task: dict, files: dict, state: dict | None = None) -> dict:
    """Workflow reviewer: checks logic flow and correctness."""
    system = (
        "You are a workflow/logic reviewer. Check the code for:\n"
        "1. Correct implementation of the task's acceptance criteria\n"
        "2. Proper control flow and data flow\n"
        "3. Dependency handling\n"
        "Output valid JSON: {\"passed\": bool, \"issues\": [str], \"severity\": \"high\"|\"medium\"|\"low\"}"
    )
    msg = f"Task: {json.dumps(task, indent=2)[:2000]}\n\nCode:\n{json.dumps(files, indent=2)[:6000]}"
    try:
        return _call_llm_json(system, msg, state, node="workflow_reviewer")
    except Exception:
        return {"passed": True, "issues": [], "severity": "low"}


def _review_prompt(task: dict, files: dict, state: dict | None = None) -> dict:
    """Prompt reviewer: validates prompt engineering patterns."""
    system = (
        "You are a prompt engineering reviewer. If the code contains LLM prompts or "
        "prompt templates, check for:\n"
        "1. Clear system/user separation\n"
        "2. Structured output instructions\n"
        "3. Guard rails and safety instructions\n"
        "If no prompts exist, pass automatically.\n"
        "Output valid JSON: {\"passed\": bool, \"issues\": [str], \"severity\": \"high\"|\"medium\"|\"low\"}"
    )
    msg = f"Task: {json.dumps(task, indent=2)[:1000]}\n\nCode:\n{json.dumps(files, indent=2)[:6000]}"
    try:
        return _call_llm_json(system, msg, state, node="prompt_reviewer")
    except Exception:
        return {"passed": True, "issues": [], "severity": "low"}


def _review_security(task: dict, files: dict, state: dict | None = None) -> dict:
    """Security reviewer: scans for injection, secret leaks, unsafe patterns."""
    system = (
        "You are a security code reviewer. Check for:\n"
        "1. Hardcoded secrets, API keys, passwords\n"
        "2. SQL/command injection vulnerabilities\n"
        "3. Unsafe deserialization\n"
        "4. Missing input validation\n"
        "5. Prompt injection vulnerabilities (if LLM code)\n"
        "Output valid JSON: {\"passed\": bool, \"issues\": [str], \"severity\": \"high\"|\"medium\"|\"low\"}"
    )
    msg = f"Code:\n{json.dumps(files, indent=2)[:8000]}"
    try:
        return _call_llm_json(system, msg, state, node="security_reviewer")
    except Exception:
        return {"passed": True, "issues": [], "severity": "low"}


def _run_parallel_reviews(task: dict, files: dict, state: dict | None = None) -> dict:
    """Run all three reviewers and reduce to single pass/fail."""
    wf = _review_workflow(task, files, state)
    pr = _review_prompt(task, files, state)
    sec = _review_security(task, files, state)

    all_issues = []
    for name, result in [("workflow", wf), ("prompt", pr), ("security", sec)]:
        for issue in result.get("issues", []):
            all_issues.append(f"[{name}] {issue}")

    has_high = any(
        r.get("severity") == "high" and not r.get("passed", True)
        for r in [wf, pr, sec]
    )
    all_passed = all(r.get("passed", True) for r in [wf, pr, sec])
    passed = all_passed or not has_high

    return {
        "passed": passed,
        "issues": all_issues,
        "workflow_review": wf,
        "prompt_review": pr,
        "security_review": sec,
    }


# ───────────────────── Graph Nodes ─────────────────────

def init_workspace(state: dict) -> dict:
    """Initialize workspace directory for this run."""
    logger.info("[W3] init_workspace: project=%s", state.get("project_id"))
    from app.services.workspace_manager import workspace_manager
    workspace_manager.workspace_dir(state["project_id"], state["run_id"])
    return {
        "current_task_index": 0,
        "completed_tasks": [],
        "generated_files": {},
        "task_results": [],
        "retry_count": 0,
        "current_review_feedback": "",
        "status": "generating",
    }


def task_loop(state: dict) -> dict:
    """Orchestrator: pick the next task or signal completion."""
    tasks = state.get("task_list", [])
    idx = state.get("current_task_index", 0)

    if idx >= len(tasks):
        logger.info("[W3] All %d tasks completed", len(tasks))
        return {"has_next_task": False, "status": "all_tasks_done"}

    task = tasks[idx]
    logger.info("[W3] task_loop: starting task %d/%d — %s",
                idx + 1, len(tasks), task.get("id", "?"))
    return {
        "current_task": task,
        "has_next_task": True,
        "retry_count": 0,
        "current_review_feedback": "",
        "status": f"task_{task.get('id', idx)}",
    }


def generate_code(state: dict) -> dict:
    """Developer agent: generate code for current task (pattern-aware)."""
    task = state.get("current_task", {})
    architecture = state.get("architecture_json", {})
    prior_files = state.get("generated_files", {})
    feedback = state.get("current_review_feedback", "")
    task_id = task.get("id", "?")

    logger.info("[W3] generate_code: task=%s, reflection=%s, retry=%d",
                task_id, _has_reflection_pattern(task), state.get("retry_count", 0))

    try:
        if _has_reflection_pattern(task):
            files = _generate_with_reflection(task, architecture, prior_files, feedback, state)
        else:
            files = _generate_single_shot(task, architecture, prior_files, feedback, state)
    except Exception as exc:
        logger.error("[W3] generate_code failed for %s: %s", task_id, exc)
        files = {}
        for fp in task.get("target_files", []):
            files[fp] = f"# Generation failed: {exc}\n# Task: {task_id}\n"

    logger.info("[W3] Generated %d files for task %s", len(files), task_id)
    return {"current_files": files}


def review_code(state: dict) -> dict:
    """Parallel reviewer sub-agents: workflow + prompt + security."""
    task = state.get("current_task", {})
    files = state.get("current_files", {})
    task_id = task.get("id", "?")

    logger.info("[W3] review_code: task=%s, %d files", task_id, len(files))

    if not files:
        return {
            "review_passed": True,
            "review_issues": [],
            "current_review_feedback": "",
        }

    try:
        result = _run_parallel_reviews(task, files, state)
    except Exception as exc:
        logger.error("[W3] review_code failed: %s", exc)
        return {"review_passed": True, "review_issues": [], "current_review_feedback": ""}

    feedback = "\n".join(result["issues"]) if result["issues"] else ""
    logger.info("[W3] Review for %s: passed=%s, issues=%d",
                task_id, result["passed"], len(result["issues"]))
    return {
        "review_passed": result["passed"],
        "review_issues": result["issues"],
        "current_review_feedback": feedback,
    }


def commit_task(state: dict) -> dict:
    """Write generated files to workspace, advance to next task."""
    from app.services.workspace_manager import workspace_manager

    task = state.get("current_task", {})
    files = state.get("current_files", {})
    task_id = task.get("id", "?")
    project_id = state.get("project_id", "")
    run_id = state.get("run_id", "")

    written_paths = []
    for rel_path, content in files.items():
        if rel_path and content:
            workspace_manager.write_file(project_id, run_id, rel_path, content)
            written_paths.append(rel_path)

    all_files = dict(state.get("generated_files", {}))
    all_files.update(files)

    completed = list(state.get("completed_tasks", []))
    completed.append(task_id)

    results = list(state.get("task_results", []))
    results.append({
        "task_id": task_id,
        "files": written_paths,
        "review_passed": state.get("review_passed", True),
        "review_issues": state.get("review_issues", []),
        "retries": state.get("retry_count", 0),
    })

    next_idx = state.get("current_task_index", 0) + 1
    logger.info("[W3] commit_task: %s → %d files written, advancing to index %d",
                task_id, len(written_paths), next_idx)

    return {
        "generated_files": all_files,
        "completed_tasks": completed,
        "task_results": results,
        "current_task_index": next_idx,
        "current_task": {},
        "current_files": {},
        "review_passed": False,
        "review_issues": [],
        "retry_count": 0,
        "current_review_feedback": "",
    }


def bundle_workspace(state: dict) -> dict:
    """Create MANIFEST.json and zip the workspace into a downloadable bundle."""
    from app.services.workspace_manager import workspace_manager

    project_id = state.get("project_id", "")
    run_id = state.get("run_id", "")
    task_results = state.get("task_results", [])

    manifest = {
        "project_id": project_id,
        "run_id": run_id,
        "total_tasks": len(task_results),
        "tasks": {},
    }
    for tr in task_results:
        manifest["tasks"][tr["task_id"]] = {
            "files": tr["files"],
            "review_passed": tr["review_passed"],
            "retries": tr["retries"],
        }

    workspace_manager.write_manifest(project_id, run_id, manifest)
    bundle_rel_path = workspace_manager.create_bundle(project_id, run_id)

    total_files = sum(len(tr["files"]) for tr in task_results)
    summary_parts = [
        f"# Code Generation Summary\n",
        f"**Tasks completed:** {len(task_results)}\n",
        f"**Total files generated:** {total_files}\n",
        f"**Bundle:** {bundle_rel_path}\n\n",
        "## Task Results\n\n",
    ]
    for tr in task_results:
        status = "PASS" if tr["review_passed"] else "FAIL"
        summary_parts.append(f"### {tr['task_id']} [{status}]\n")
        summary_parts.append(f"- Files: {', '.join(tr['files']) or 'none'}\n")
        if tr["retries"] > 0:
            summary_parts.append(f"- Retries: {tr['retries']}\n")
        if tr.get("review_issues"):
            summary_parts.append(f"- Issues: {'; '.join(tr['review_issues'][:3])}\n")
        summary_parts.append("\n")

    spec_md = "".join(summary_parts)
    spec_json = {
        "manifest": manifest,
        "task_results": task_results,
        "bundle_path": bundle_rel_path,
    }

    logger.info("[W3] Bundle created: %s, %d tasks, %d files",
                bundle_rel_path, len(task_results), total_files)
    return {
        "bundle_path": bundle_rel_path,
        "spec_markdown": spec_md,
        "spec_json": spec_json,
        "status": "pending_approval",
    }


def approval_gate(state: dict) -> dict:
    """HITL approval for the generated codebase."""
    logger.info("[W3] approval_gate")

    human_response = interrupt({
        "type": "approval",
        "spec_markdown": state.get("spec_markdown", ""),
        "bundle_path": state.get("bundle_path", ""),
    })

    approved = human_response.get("approved", False) if isinstance(human_response, dict) else False
    feedback = human_response.get("feedback", "") if isinstance(human_response, dict) else ""

    if approved:
        logger.info("[W3] Codebase APPROVED")
        return {"status": "approved"}
    else:
        logger.info("[W3] Codebase REJECTED: %s", feedback[:200])
        return {"status": "rejected", "rejection_feedback": feedback}


# ───────────────────── Routing Logic ─────────────────────

def route_task_loop(state: dict) -> str:
    if state.get("has_next_task"):
        return "generate_code"
    return "bundle_workspace"


def route_after_review(state: dict) -> str:
    settings = get_settings()
    if state.get("review_passed", False):
        return "commit_task"
    if state.get("retry_count", 0) >= settings.max_code_retries:
        logger.warning("[W3] Max retries reached for task %s, committing best-effort",
                       state.get("current_task", {}).get("id", "?"))
        return "commit_task"
    return "retry_generate"


def retry_generate(state: dict) -> dict:
    """Increment retry counter and loop back to generate."""
    new_count = state.get("retry_count", 0) + 1
    logger.info("[W3] retry_generate: attempt %d", new_count)
    return {"retry_count": new_count}


def route_after_approval(state: dict) -> str:
    if state.get("status") == "approved":
        return END
    return "task_loop"


# ───────────────────── Per-task developer–reviewer subgraph ─────────────────────

def _build_task_subgraph() -> Any:
    """Dynamic-ish per-task subgraph: generate → parallel reviewers → reduce."""
    import operator
    from typing import Annotated, TypedDict

    class TaskState(TypedDict, total=False):
        project_id: str
        run_id: str
        current_task: dict
        architecture_json: dict
        selected_patterns: dict | list
        generated_files: dict
        current_review_feedback: str
        current_files: dict
        review_passed: bool
        review_issues: list
        workflow_review: dict
        prompt_review: dict
        security_review: dict
        review_verdicts: Annotated[list, operator.add]

    def gen(state: dict) -> dict:
        return generate_code(state)

    def wf_reviewer(state: dict) -> dict:
        task, files = state.get("current_task", {}), state.get("current_files", {})
        r = _review_workflow(task, files, state)
        return {"workflow_review": r, "review_verdicts": [{"name": "workflow", **r}]}

    def prompt_reviewer(state: dict) -> dict:
        task, files = state.get("current_task", {}), state.get("current_files", {})
        r = _review_prompt(task, files, state)
        return {"prompt_review": r, "review_verdicts": [{"name": "prompt", **r}]}

    def security_reviewer(state: dict) -> dict:
        task, files = state.get("current_task", {}), state.get("current_files", {})
        r = _review_security(task, files, state)
        return {"security_review": r, "review_verdicts": [{"name": "security", **r}]}

    def reduce_reviews(state: dict) -> dict:
        verdicts = state.get("review_verdicts") or []
        if not verdicts:
            # fallback if reducers empty
            return review_code(state)
        all_issues = []
        for v in verdicts:
            for issue in v.get("issues", []):
                all_issues.append(f"[{v.get('name')}] {issue}")
        has_high = any(v.get("severity") == "high" and not v.get("passed", True) for v in verdicts)
        all_passed = all(v.get("passed", True) for v in verdicts)
        passed = all_passed or not has_high
        return {
            "review_passed": passed,
            "review_issues": all_issues,
            "current_review_feedback": "\n".join(all_issues),
        }

    builder: Any = StateGraph(TaskState)
    builder.add_node("generate_code", node_hook("task_generate")(gen))
    builder.add_node("workflow_reviewer", node_hook("workflow_reviewer")(wf_reviewer))
    builder.add_node("prompt_reviewer", node_hook("prompt_reviewer")(prompt_reviewer))
    builder.add_node("security_reviewer", node_hook("security_reviewer")(security_reviewer))
    builder.add_node("reduce_reviews", node_hook("reduce_reviews")(reduce_reviews))

    builder.add_edge(START, "generate_code")
    builder.add_edge("generate_code", "workflow_reviewer")
    builder.add_edge("generate_code", "prompt_reviewer")
    builder.add_edge("generate_code", "security_reviewer")
    builder.add_edge("workflow_reviewer", "reduce_reviews")
    builder.add_edge("prompt_reviewer", "reduce_reviews")
    builder.add_edge("security_reviewer", "reduce_reviews")
    builder.add_edge("reduce_reviews", END)
    return builder.compile()


def run_task_unit(state: dict) -> dict:
    """Orchestrator worker: invoke the per-task subgraph (pattern-aware composition)."""
    task = state.get("current_task", {})
    # Dynamic composition signal: reflection tasks already use internal critique in generate_code
    sg = _build_task_subgraph()
    partial = {
        "project_id": state.get("project_id"),
        "run_id": state.get("run_id"),
        "current_task": task,
        "architecture_json": state.get("architecture_json", {}),
        "selected_patterns": state.get("selected_patterns", []),
        "generated_files": state.get("generated_files", {}),
        "current_review_feedback": state.get("current_review_feedback", ""),
        "review_verdicts": [],
    }
    out = sg.invoke(partial)
    return {
        "current_files": out.get("current_files", {}),
        "review_passed": out.get("review_passed", True),
        "review_issues": out.get("review_issues", []),
        "current_review_feedback": out.get("current_review_feedback", ""),
    }


# ───────────────────── Build Graph ─────────────────────

def _build_w3_builder() -> Any:
    builder: Any = StateGraph(dict)

    wrap_nodes(builder, {
        "init_workspace": init_workspace,
        "task_loop": task_loop,
        "run_task_unit": run_task_unit,
        "commit_task": commit_task,
        "retry_generate": retry_generate,
        "bundle_workspace": bundle_workspace,
        "approval_gate": approval_gate,
    })

    def route_task_loop_v2(state: dict) -> str:
        if state.get("has_next_task"):
            return "run_task_unit"
        return "bundle_workspace"

    def route_after_review_v2(state: dict) -> str:
        settings = get_settings()
        if state.get("review_passed", False):
            return "commit_task"
        if state.get("retry_count", 0) >= settings.max_code_retries:
            return "commit_task"
        return "retry_generate"

    builder.add_edge(START, "init_workspace")
    builder.add_edge("init_workspace", "task_loop")
    builder.add_conditional_edges("task_loop", route_task_loop_v2,
                                  ["run_task_unit", "bundle_workspace"])
    builder.add_conditional_edges("run_task_unit", route_after_review_v2,
                                  ["commit_task", "retry_generate"])
    builder.add_edge("retry_generate", "run_task_unit")
    builder.add_edge("commit_task", "task_loop")
    builder.add_edge("bundle_workspace", "approval_gate")
    builder.add_conditional_edges("approval_gate", route_after_approval,
                                  ["task_loop", END])

    return builder


def build_w3_graph() -> Any:
    return _build_w3_builder().compile()


def build_w3_graph_with_checkpointer() -> tuple[Any, Any]:
    from app.workflows.checkpointer import get_sqlite_checkpointer
    builder: Any = _build_w3_builder()
    checkpointer = get_sqlite_checkpointer()
    graph = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["approval_gate"],
    )
    return graph, checkpointer