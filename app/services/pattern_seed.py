from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.pattern import PatternCreate
from app.services.pattern_service import bulk_import_patterns

logger = logging.getLogger(__name__)

CANONICAL_PATTERNS: list[dict] = [
    {
        "name": "ReAct",
        "category": "single-agent",
        "intent": (
            "Enable an agent to interleave reasoning (Thought) with tool use (Action) "
            "and observation in an iterative loop until the task is resolved. The model "
            "explicitly verbalizes its chain-of-thought before each action."
        ),
        "structure": (
            "Loop: Thought → Action (tool call) → Observation (tool result) → repeat. "
            "The agent maintains a scratchpad of all prior thoughts and observations. "
            "Terminates when the agent emits a final-answer action or hits max iterations."
        ),
        "when_to_use": (
            "Complex, dynamic tasks requiring continuous planning and adaptation. "
            "Tasks that need external data via tools (APIs, databases, search). "
            "When debugging transparency is important — the thinking transcript aids diagnosis."
        ),
        "anti_patterns": (
            "Avoid when latency is critical — each loop adds a full LLM call. "
            "Not suitable for simple, one-shot tasks. "
            "A single bad tool result can cascade through the loop."
        ),
        "prerequisites": (
            "Tool definitions with clear schemas. "
            "A capable reasoning model (GPT-4-class or above). "
            "Max-iteration safeguard to prevent runaway loops."
        ),
        "references": [
            "Yao et al., 'ReAct: Synergizing Reasoning and Acting in Language Models', ICLR 2023",
            "https://arxiv.org/abs/2210.03629",
        ],
        "tags": ["reasoning", "tool-use", "iterative", "single-agent"],
    },
    {
        "name": "Reflection / Self-Critique",
        "category": "single-agent",
        "intent": (
            "Let an agent review its own output, identify errors or gaps, and self-correct "
            "before delivering the final result. Implements an internal critic loop."
        ),
        "structure": (
            "Generator produces draft → Critic evaluates against criteria → "
            "if rejected, feedback is fed back to the generator for revision. "
            "Bounded by an iteration counter or quality threshold."
        ),
        "when_to_use": (
            "Outputs must be highly accurate or meet strict quality constraints. "
            "Code generation (security audit pass), factual reports, compliance documents. "
            "When a second LLM call is cheaper than a human review."
        ),
        "anti_patterns": (
            "Avoid if the model cannot reliably evaluate its own output — garbage-in, garbage-out critic. "
            "Unbounded loops without an iteration cap can burn tokens. "
            "Over-critique can cause the agent to oscillate without converging."
        ),
        "prerequisites": (
            "Clear evaluation rubric or checklist the critic can apply. "
            "Iteration cap (typically 2-4 rounds). "
            "Separate system prompts for generator and critic roles."
        ),
        "references": [
            "Madaan et al., 'Self-Refine: Iterative Refinement with Self-Feedback', NeurIPS 2023",
            "https://arxiv.org/abs/2303.17651",
        ],
        "tags": ["self-correction", "quality", "iterative", "critic", "single-agent"],
    },
    {
        "name": "Planner-Executor (Plan-and-Execute)",
        "category": "multi-agent",
        "intent": (
            "Separate high-level planning from low-level task execution. "
            "A Planner agent decomposes the goal into a step-by-step plan; "
            "an Executor agent carries out each step, optionally reporting back "
            "so the Planner can re-plan."
        ),
        "structure": (
            "Planner generates ordered task list → Executor runs tasks sequentially → "
            "results feed back to Planner for replanning or confirmation. "
            "Plan may be stored as structured JSON for traceability."
        ),
        "when_to_use": (
            "Complex, multi-step goals that benefit from an explicit plan. "
            "When you need auditability of the plan before execution. "
            "Backbone for code-generation and project-planning workflows."
        ),
        "anti_patterns": (
            "Avoid for simple tasks — overhead of planning is not justified. "
            "Plans that are too fine-grained increase latency with no quality gain. "
            "Rigid plans that cannot adapt to execution failures."
        ),
        "prerequisites": (
            "A model strong enough to decompose goals into actionable steps. "
            "Structured output (JSON plan schema). "
            "Mechanism for the Executor to report success/failure per step."
        ),
        "references": [
            "Wang et al., 'Plan-and-Solve Prompting', ACL 2023",
            "LangGraph Plan-and-Execute tutorial",
        ],
        "tags": ["planning", "decomposition", "multi-step", "multi-agent"],
    },
    {
        "name": "Multi-Agent Debate",
        "category": "multi-agent",
        "intent": (
            "Multiple agents argue different perspectives on a problem, "
            "critique each other's proposals, and converge on a higher-quality solution "
            "through structured debate rounds."
        ),
        "structure": (
            "N agents generate independent proposals → each agent critiques the others' proposals → "
            "a Judge/Moderator synthesizes the best elements or calls another round. "
            "Terminates on consensus, quality threshold, or max rounds."
        ),
        "when_to_use": (
            "Ambiguous, open-ended problems benefiting from multiple perspectives. "
            "Architecture decisions, threat modeling, creative brainstorming. "
            "When diverse viewpoints reduce blind spots."
        ),
        "anti_patterns": (
            "Expensive — each round multiplies LLM calls by N agents. "
            "Risk of unproductive loops if agents echo each other. "
            "Not suitable for tasks with objectively correct answers (use tools instead)."
        ),
        "prerequisites": (
            "Distinct persona prompts for each debater. "
            "A moderator/judge with clear convergence criteria. "
            "Round and token budget."
        ),
        "references": [
            "Du et al., 'Improving Factuality and Reasoning in LLMs through Multi-Agent Debate', 2023",
            "https://arxiv.org/abs/2305.14325",
        ],
        "tags": ["debate", "consensus", "multi-perspective", "multi-agent"],
    },
    {
        "name": "Router",
        "category": "multi-agent",
        "intent": (
            "Dynamically route an incoming request to the most appropriate specialist agent "
            "or processing path based on the request's characteristics."
        ),
        "structure": (
            "Router agent (or classifier) analyzes the input → selects a route → "
            "dispatches to the matched specialist agent → returns the specialist's result. "
            "Routes can be model-selected or rule-based."
        ),
        "when_to_use": (
            "Varied input types requiring different handling strategies. "
            "Customer support (billing vs. technical vs. returns). "
            "Complexity routing: simple → lightweight agent, complex → heavyweight pipeline."
        ),
        "anti_patterns": (
            "Avoid if all inputs are uniform — unnecessary overhead. "
            "Over-routing (too many fine-grained routes) adds latency. "
            "Router misclassification can send requests to the wrong agent entirely."
        ),
        "prerequisites": (
            "Clear taxonomy of route categories. "
            "Specialist agents for each route. "
            "Fallback/default route for unclassified inputs."
        ),
        "references": [
            "Semantic Router library",
            "LangChain/LangGraph routing patterns",
        ],
        "tags": ["routing", "classification", "dispatch", "multi-agent"],
    },
    {
        "name": "RAG (Retrieval-Augmented Generation)",
        "category": "single-agent",
        "intent": (
            "Ground the LLM's responses in external, up-to-date knowledge by retrieving "
            "relevant documents from a vector store and injecting them into the prompt context."
        ),
        "structure": (
            "User query → Embed query → Vector similarity search → "
            "Retrieve top-k chunks → Augment prompt with retrieved context → "
            "LLM generates grounded response. Optionally includes a reranker step."
        ),
        "when_to_use": (
            "When the LLM needs domain-specific or frequently updated knowledge. "
            "Document Q&A, knowledge bases, support bots. "
            "Reducing hallucination by providing factual context."
        ),
        "anti_patterns": (
            "Garbage-in: poor chunking or stale embeddings degrade retrieval quality. "
            "Stuffing too many chunks overwhelms the context window. "
            "Not suitable when the answer requires multi-hop reasoning across many documents (consider agentic RAG)."
        ),
        "prerequisites": (
            "Vector store (ChromaDB, Pinecone, etc.) with embedded document chunks. "
            "Embedding model aligned with the retrieval model. "
            "Chunk strategy tuned for the domain."
        ),
        "references": [
            "Lewis et al., 'Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks', NeurIPS 2020",
            "https://arxiv.org/abs/2005.11401",
        ],
        "tags": ["retrieval", "grounding", "knowledge", "vector-search", "single-agent"],
    },
    {
        "name": "Tool-Use",
        "category": "single-agent",
        "intent": (
            "Enable the LLM to invoke external tools (APIs, databases, calculators, code execution) "
            "to perform actions it cannot do through text generation alone."
        ),
        "structure": (
            "LLM receives tool schemas → decides which tool to call with which arguments → "
            "runtime executes the tool → result is fed back to the LLM. "
            "Often combined with ReAct or Plan-and-Execute."
        ),
        "when_to_use": (
            "Tasks requiring real-world side effects (API calls, DB writes). "
            "Precise computation (math, data queries). "
            "Any task where the LLM needs external data or capabilities."
        ),
        "anti_patterns": (
            "Exposing tools with dangerous side effects without confirmation gates. "
            "Too many tools in a single prompt overwhelm the model's selection. "
            "Missing error handling for tool failures."
        ),
        "prerequisites": (
            "Well-defined tool schemas (OpenAI function calling format or LangChain ToolNode). "
            "Sandboxed execution environment for code tools. "
            "Retry and error-handling logic."
        ),
        "references": [
            "Schick et al., 'Toolformer', NeurIPS 2023",
            "OpenAI Function Calling documentation",
        ],
        "tags": ["tools", "function-calling", "api", "execution", "single-agent"],
    },
    {
        "name": "Hierarchical (Orchestrator-Worker / Supervisor)",
        "category": "multi-agent",
        "intent": (
            "Organize agents in a hierarchy where a top-level supervisor/orchestrator "
            "decomposes tasks and delegates to specialist worker agents, "
            "aggregating their results into a cohesive output."
        ),
        "structure": (
            "Supervisor receives complex task → decomposes into sub-tasks → "
            "dispatches each sub-task to a specialist worker → "
            "workers execute and return results → supervisor aggregates and produces final output. "
            "Can be multi-level (supervisor → sub-supervisor → workers)."
        ),
        "when_to_use": (
            "Large, complex tasks requiring diverse expertise. "
            "Project planning with specialist agents (architect, researcher, planner, critic). "
            "When modularity and parallel execution improve throughput."
        ),
        "anti_patterns": (
            "Multi-level hierarchies add significant latency and cost. "
            "Supervisor bottleneck if it must process all intermediate results. "
            "Over-decomposition into too many tiny tasks."
        ),
        "prerequisites": (
            "Clear role definitions for each worker agent. "
            "Structured communication protocol between supervisor and workers. "
            "Aggregation logic in the supervisor."
        ),
        "references": [
            "LangGraph multi-agent supervisor tutorial",
            "AutoGen hierarchical agent patterns",
        ],
        "tags": ["hierarchy", "supervisor", "orchestration", "delegation", "multi-agent"],
    },
    {
        "name": "Critic-Refine (Evaluator-Optimizer)",
        "category": "multi-agent",
        "intent": (
            "A dedicated evaluator agent scores or critiques an artifact, "
            "and an optimizer agent uses that feedback to iteratively improve the artifact "
            "until a quality bar is met."
        ),
        "structure": (
            "Generator produces artifact → Evaluator scores against rubric → "
            "if below threshold, Optimizer receives score + feedback → produces improved artifact → "
            "loop. Terminates when score exceeds threshold or max iterations reached."
        ),
        "when_to_use": (
            "Code review loops (developer-reviewer). "
            "Planning quality gates (planner-critic). "
            "Any artifact that has measurable quality dimensions."
        ),
        "anti_patterns": (
            "Evaluator with vague criteria leads to oscillating scores. "
            "No convergence guarantee if the optimizer can't address the feedback. "
            "Cost multiplies with each iteration."
        ),
        "prerequisites": (
            "Explicit evaluation rubric with numeric or categorical scores. "
            "Clear feedback format the optimizer can act on. "
            "Iteration budget (typically 2-5 rounds)."
        ),
        "references": [
            "Shinn et al., 'Reflexion: Language Agents with Verbal Reinforcement Learning', NeurIPS 2023",
            "https://arxiv.org/abs/2303.11366",
        ],
        "tags": ["evaluation", "optimization", "iterative", "quality-gate", "multi-agent"],
    },
    {
        "name": "Map-Reduce (Parallel Fan-Out / Fan-In)",
        "category": "multi-agent",
        "intent": (
            "Split a large task into independent sub-tasks, process them in parallel (map), "
            "then aggregate results into a final output (reduce)."
        ),
        "structure": (
            "Splitter divides input into N chunks → N worker agents process in parallel → "
            "Reducer collects all outputs → merges/summarizes into final result. "
            "Workers are typically identical agents with different input slices."
        ),
        "when_to_use": (
            "Processing large documents (summarize each section, then merge summaries). "
            "Multi-source research (each worker searches a different source). "
            "Any embarrassingly parallel workload."
        ),
        "anti_patterns": (
            "Tasks with strong inter-dependencies between sub-tasks (sequential by nature). "
            "Reducer overwhelmed by conflicting or redundant outputs. "
            "Fan-out to too many workers wastes tokens on thin slices."
        ),
        "prerequisites": (
            "Input that can be cleanly partitioned. "
            "Reducer logic that can reconcile diverse outputs. "
            "Parallel execution runtime (asyncio, LangGraph Send)."
        ),
        "references": [
            "LangGraph Map-Reduce tutorial",
            "Dean & Ghemawat, 'MapReduce: Simplified Data Processing on Large Clusters', OSDI 2004",
        ],
        "tags": ["parallel", "fan-out", "aggregation", "scalability", "multi-agent"],
    },
    {
        "name": "Human-in-the-Loop (HITL)",
        "category": "workflow",
        "intent": (
            "Integrate explicit human checkpoints into the agent workflow. "
            "The agent pauses at predefined points for human review, approval, "
            "correction, or input before continuing."
        ),
        "structure": (
            "Agent executes steps → reaches HITL checkpoint → pauses execution → "
            "sends state to human via UI/WebSocket → human approves/rejects/edits → "
            "agent resumes with human feedback incorporated into state."
        ),
        "when_to_use": (
            "High-stakes decisions requiring human judgment. "
            "Safety-critical operations, compliance workflows. "
            "Any multi-step pipeline where intermediate outputs need validation."
        ),
        "anti_patterns": (
            "Too many checkpoints slow the pipeline to a crawl. "
            "No timeout/escalation for unresponsive humans. "
            "Feedback that the agent cannot parse or act on."
        ),
        "prerequisites": (
            "Communication channel (WebSocket, queue, UI). "
            "State serialization for pause/resume (LangGraph checkpointing). "
            "Clear approval schema (approve/reject with optional feedback)."
        ),
        "references": [
            "LangGraph human-in-the-loop documentation",
            "https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/",
        ],
        "tags": ["human-review", "approval", "safety", "checkpoint", "workflow"],
    },
    {
        "name": "Sequential Pipeline",
        "category": "multi-agent",
        "intent": (
            "Chain specialized agents in a fixed linear order where the output of one agent "
            "becomes the direct input of the next. The orchestrator follows predefined logic "
            "without consulting an AI model."
        ),
        "structure": (
            "Agent A → Agent B → Agent C → ... → Final Output. "
            "Each agent receives the prior agent's output. "
            "Orchestration is deterministic (no model routing decisions)."
        ),
        "when_to_use": (
            "Highly structured, repeatable processes with a fixed step order. "
            "Data pipelines (extract → clean → transform → load). "
            "Document processing (parse → validate → enrich → store)."
        ),
        "anti_patterns": (
            "Rigid structure makes it hard to skip or reorder steps. "
            "Not suitable for tasks where the next step depends on runtime conditions. "
            "Failure in one step blocks the entire pipeline without branching logic."
        ),
        "prerequisites": (
            "Well-defined input/output contracts between agents. "
            "Error handling and retry logic per step. "
            "Monitoring for pipeline health."
        ),
        "references": [
            "Unix pipeline philosophy",
            "LangGraph sequential agent patterns",
        ],
        "tags": ["sequential", "pipeline", "deterministic", "chain", "multi-agent"],
    },
]


async def seed_canonical_patterns(db: AsyncSession) -> None:
    """Idempotently seed the canonical design patterns into the KB."""
    items = [PatternCreate(**p) for p in CANONICAL_PATTERNS]
    result = await bulk_import_patterns(db, items, actor="system")
    logger.info(
        "Pattern seed: created=%d, skipped=%d, errors=%d",
        result["created"], result["skipped"], len(result["errors"]),
    )