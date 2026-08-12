# HUS 26.2.1 Python GenAI Track

## AI Agent Factory v2: Document-Driven, Pattern-Aware Code Generator (Backend-Only)

---

### Overview

Build a **backend-only** AI agent factory that turns business and technical documents (BRD / PRD / TRD) into working code through three sequential, autonomous agentic workflows. The system is operated through a REST API for command/control, **Server-Sent Events** for one-way progress streams, and **WebSockets specifically for the human-in-the-loop (HITL) channel**  where the running graph pauses, asks the user a question (clarification or approval), and resumes once the user replies. There is **no chat UI and no team management or RBAC**. A single user uploads documents, curates a knowledge base of agentic design patterns, and triggers three workflows back-to-back: **Requirements Gathering**, **Combined Project & Code Planning** (powered by RAG, web search, and the LLM's own knowledge), and **Code Generation**. The Planning workflow is the centerpiece of the assignment: it must select appropriate agentic design patterns from the user's knowledge base, conduct genuine multi-source research, and produce a code-ready task plan that the code-generation workflow executes.

---

### Use Case: Document Working Code, Fully Autonomous

Your system must:

- **Ingest BRD / PRD / TRD documents** (PDF, DOCX, PPTX, XLSX, MD, TXT) and extract clean structured content
- **Maintain an Agentic Design Patterns Knowledge Base** the user can populate with pattern entries (ReAct, Reflection, Planner-Executor, Multi-Agent Debate, Router, RAG, Tool-Use, Hierarchical, Critic-Refine, Map-Reduce, etc.)
- **Run three sequential workflows** triggered via REST, each gated by an explicit user approval:
  1. **Requirements Gathering**  an agent reads uploaded documents, fills gaps via a **WebSocket-driven HITL clarification loop** (the graph pauses on `interrupt`, the server pushes the question over a per-run WebSocket, the user replies on the same socket, the graph resumes), and produces a canonical Requirements Document
  2. **Combined Project & Code Planning**  a multi-agent LangGraph that selects fitting design patterns from the knowledge base, performs RAG over uploaded docs and the pattern KB, runs web search, synthesizes with LLM knowledge, produces a system architecture, selected-patterns rationale with citations, and an ordered, code-ready task list; **approval / rejection-with-feedback flows over WebSocket**
  3. **Code Generation** a developer agent executes tasks sequentially with pattern-aware prompts, reviewer sub-agents validate output, and the system bundles the final artifact for download; final approval flows over WebSocket
- **Stream progress** via Server-Sent Events for one-way node/tool/cost telemetry
- **Drive human-in-the-loop** via per-run WebSocket sessions the only bidirectional transport in the system
- **Survive crashes** through LangGraph checkpointing runs can be resumed from the last successful step
- **Track cost and traceability** end-to-end: uploaded-doc-section requirement selected pattern task generated code file

---

### Target Persona

#### Builder / Engineer

*Profile:* A single technical user who interacts with the system entirely through the REST API (Postman, curl, or Swagger UI). They upload documents, curate the Pattern KB, trigger workflows, answer agent clarification questions through API calls, approve phase outputs, and download generated code.

*Key Deliverables They Need:*

- Reliable document upload and parsing
- A searchable, embedding-indexed Pattern Knowledge Base they fully control
- A canonical, structured requirements document derived from their uploads
- An auditable architecture document with explicit pattern selections and research citations
- An ordered, code-ready task list they can review and approve before code generation
- A downloadable code bundle traceable back to the original requirements
- Per-run cost, token, and latency observability

There are no other personas. There is no admin, no PO, no architect role distinction, and no team. Authorization is a single user account secured with a JWT bearer token.

---

### Tech Stack

- **FastAPI**  REST API for command/control, **Server-Sent Events** for one-way progress streams, and **WebSockets** for the human-in-the-loop channel only (clarifications, approval/rejection-with-feedback). The WebSocket protocol must be documented in the README (message envelope, types, error semantics) since Swagger does not auto-document it.
- **LangChain + LangGraph** (`langchain`, `langgraph`)  agent and multi-agent orchestration; LangGraph **checkpointing** for resumable runs (use the SQLite checkpointer, `langgraph.checkpoint.sqlite`).
- **OpenAI** (`openai`, `langchain-openai`)  backbone LLM (use GPT-5.5 or GPT-5.4); use structured output (JSON schema / Pydantic) for every machine-consumed agent output. Use OpenAI's **built-in web search tool.**
- **SQLite + SQLAlchemy (async via `aiosqlite`)**  primary storage for documents, runs, requirements, plans, tasks, approvals, and audit log;
- **ChromaDB** vector store for both uploaded-document RAG and the Pattern Knowledge Base. Run locally with two collections: `documents` and `patterns`.
- **In-Memory State Management**  use LangGraph's built-in `MemorySaver` or `InMemoryStore` for run-state coordination, single-active-run-per-project locks, idempotency keys, and SSE fan-out. For the assignment scope, in-memory state is acceptable; document the trade-off (state lost on restart, single-process only) and note that production would use a distributed store.
- **Local filesystem storage**  raw uploads, generated code bundles, and rendered graph PNGs are stored under a configurable project root (e.g., `./data/projects/{project_id}/...`) behind a single `FileStore` abstraction.
- **Document parsing**  PDF, DOCX, PPTX, XLSX, MD, TXT minimum (e.g., `pypdf`, `python-docx`, `python-pptx`, `openpyxl`, `unstructured` optional).
- **Authentication**  **JWT** bearer tokens (HS256 with a configured secret, or RS256 with a local keypair). Endpoints: `POST /auth/login` issues an access token; protected routes require `Authorization: Bearer <token>`. Token claims: `sub` (user id), `exp`, `iat`. No refresh-token rotation required for the assignment scope; document the trade-off.
- **Observability**  **Native token/cost tracing** built into the hook layer: intercept OpenAI responses, extract token useage and compute cost using a configurable pricing table.  No external tracing service (LangSmith, Langfuse, etc.) required.

Explicitly **out of scope**: RBAC, multi-tenancy, team invitations, multi-signatory approvals, comments/threads, broadcast notifications, conversational chat UIs, MCP server registration, any frontend. WebSockets exist solely as the HITL transport  not as a chat channel.

---

### Required Concepts Coverage (Mandatory)

The assignment is graded on demonstrable, idiomatic use of the following concepts. Every concept below must appear at least once in the final implementation, and the README must point to the file/function where it lives.

**Agent Design Patterns**  must be implemented, not just referenced:

- **Plan-and-Execute**  used as the *backbone* of Workflow 2 (Planning) and Workflow 3 (Code Generation): a Planner produces a structured plan, an Executor consumes plan steps one at a time.
- **Reflection / Self-Critique**  used inside Workflow 1 (gap analysis), Workflow 2 (Critic node loop), and Workflow 3 (reviewer sub-agents that send feedback back to the developer agent).
- **Parallelization & Routing**  used in Workflow 2's Researcher and in a Router node that dispatches each requirement to either the lightweight or heavyweight planning sub-graph based on complexity.
- **Orchestrator-Worker**  Workflow 3's developer agent acts as orchestrator dispatching task work to worker invocations; the codegen run loop is the canonical implementation.
- **Evaluator-Optimizer**  Workflow 2's Planner ” Critic loop and Workflow 3's Developer ” Reviewer loop must both follow the Evaluator-Optimizer shape (generator proposes, evaluator scores against a rubric, generator revises until score  threshold or max iterations).

**LangGraph Fundamentals**  must be visible across the codebase:

- **State graphs, nodes, edges, conditional edges**  every workflow is a `StateGraph`; conditional edges drive the Router and Critic loop branching.
- **State schema design**  state per workflow with explicit reducers for accumulator fields (e.g., `messages`, `research_findings`, `tasks`).
- **Checkpointing**  SQLite checkpointer wired into every workflow; resumability tested.
- **Tool calling**  agents that need tools must use `ToolNode` with proper tool-error handling (errors are returned to the model as tool messages, not raised).
- **Graph visualization**  every compiled graph exposes a Mermaid/PNG render an endpoint `GET /workflows/{name}/graph.png` returns the rendered diagram for the README.

**LangGraph Advanced**  required wiring:

- **Human-in-the-Loop**  approval gates and clarification requests are implemented with `interrupt` (or `interrupt_before` / `interrupt_after`) on the relevant node. While interrupted, the run pauses and exposes its prompt over a **per-run WebSocket** at `WS /projects/{project_id}/runs/{run_id}/hitl`. The user replies on the same socket; the server writes the reply into graph state via `Command(resume=...)` (or equivalent state update) and the graph continues from the checkpoint.
- **Approval workflows & feedback injection**  rejection feedback (sent as a typed WebSocket message) is written into graph state and the graph resumes from the rejected node, not from scratch. A REST fallback (`POST /projects/{project_id}/runs/{run_id}/approve` / `POST /projects/{project_id}/runs/{run_id}/reject`) exists for replay/automation but the primary path is the WebSocket.
- **Hooks (pre/post node, logging, custom middleware)**  implement a custom hook layer that wraps every node to (a) emit SSE events, (b) record token/cost to the `usage` table, (c) log structured entries with `run_id` / `node` / `latency_ms`. This is the project's observability backbone.
- **Subgraphs**  Workflow 2's Researcher is a subgraph; Workflow 3's per-task developer-reviewer cycle is a subgraph; both are composed into their parent graphs.
- **Parallel branches**  Researcher fans out to three retrieval branches in parallel and joins via a reducer.
- **Cycles**  the Critic loop and Developer-Reviewer loop are explicit cycles bounded by an iteration counter in state.
- **Dynamic graphs**  Workflow 3 dynamically constructs the per-task subgraph based on the patterns referenced by that task.
- **Multi-agent orchestration patterns**  Workflow 2 demonstrates the Orchestrator-Worker (or Supervisor) pattern across Pattern Selector, Researcher, Architect, Planner, and Critic agents.

---

### Project-Centric Resource Model (Non-Negotiable)

The project is the **root aggregate** of every domain object. Concretely:

- Every URL that touches a document, a workflow run, a task, an artifact, an approval, a research log, or a traceability artifact is nested under `/projects/{project_id}/...`. There are **no flat top-level resource collections** like `/runs`, `/documents`, `/tasks`, or `/artifacts`.
- Every database row for the above resources carries a non-null `project_id` foreign key. Every JWT-protected handler resolves the `project_id` from the path, loads the project, and authorizes the request against the JWT subject before touching child resources. A request to a child resource whose `project_id` does not match the path returns `404` (never `403`, to avoid leaking existence).
- Every ChromaDB document metadata (`documents` and `patterns_in_use` collections) carries `project_id` as a filter field. RAG queries inside a workflow run **must** be filtered by the run's `project_id`; cross-project leakage is treated as a defect.
- Every file written under `./data/projects/{project_id}/...` lives inside the project's directory. The `FileStore` API takes `project_id` as a required argument; there is no global file-store namespace.
- Background tasks, in-memory locks, idempotency keys, SSE channels, and WebSocket sessions are all keyed on `(project_id, run_id)`. Concurrency rules are stated **per project** (one active run per project, one HITL socket per `(project_id, run_id)`).
- The Pattern KB is the **only** global resource (it is a curated reference library shared across projects). Even there, when a pattern is selected for a planning run, the system **snapshots** the pattern entry into a `runs.snapshotted_patterns` JSON column scoped to that run / project, so global edits to the KB never mutate historical project artifacts.

Whenever this document mentions a resource without a project prefix in conversational prose, the project scope is still implied. The URL templates in each milestone are authoritative.

---

### User Flow Overview

Every interaction is an HTTP call.

1. **Bootstrap**

   - User registers / logs in via `POST /auth/login` and stores the returned JWT
   - All subsequent requests carry `Authorization: Bearer <token>`
   - User seeds the Pattern KB (system ships with ~10 canonical patterns; user can add more via `POST /patterns`)
2. **Project Creation**

   - `POST /projects` with name and description  returns `project_id`
   - One project = one end-to-end run lineage; only one workflow may be active per project at a time
3. **Document Upload**

   - `POST /projects/{project_id}/documents` with multipart upload (BRD / PRD / TRD)
   - Server stores blob, kicks off async parse + embed job, returns `document_id` and parse-status URL
   - User polls `GET /projects/{project_id}/documents/{document_id}` until status is `ready`
4. **Workflow 1  Requirements Gathering**

   - `POST /projects/{project_id}/workflows/requirements`  `202 Accepted` with `run_id`
   - Agent runs in the background; emits SSE events on `GET /projects/{project_id}/runs/{run_id}/events`
   - User opens a **WebSocket** to `WS /projects/{project_id}/runs/{run_id}/hitl` (JWT in `Authorization` subprotocol or `?token=` query)
   - When the agent identifies unresolved gaps, it `interrupt`s and pushes a `clarification_request` message over the socket; the user replies with a `clarification_response` message and the graph resumes from the checkpoint
   - On completion the run produces a Requirements Document (Markdown + structured JSON) and pushes an `approval_request`; the user sends `approval_response` (approve or reject-with-feedback) over the socket
   - REST equivalents (`POST /projects/{project_id}/runs/{run_id}/clarifications`, `POST /projects/{project_id}/runs/{run_id}/approve`, `POST /projects/{project_id}/runs/{run_id}/reject`) exist as a fallback / automation surface
5. **Workflow 2  Combined Project & Code Planning**

   - `POST /projects/{project_id}/workflows/planning`  `202 Accepted`
   - Multi-agent graph: Pattern Selector  Researcher (RAG + web + LLM)  Architect  Planner  Critic loop
   - Produces architecture doc, selected-patterns report with citations, and an ordered task list
   - User reviews tasks (`GET /projects/{project_id}/runs/{run_id}/tasks`), edits if needed (`PATCH /projects/{project_id}/runs/{run_id}/tasks/{task_id}`), then approves over the **WebSocket** when the graph hits the `interrupt_before` approval node
6. **Workflow 3  Code Generation**

   - `POST /projects/{project_id}/workflows/codegen`  `202 Accepted`
   - Developer agent executes tasks in order with pattern-aware prompts; reviewer sub-agents validate; retry-with-feedback on failure
   - Final-bundle approval flows over the **WebSocket** at `WS /projects/{project_id}/runs/{run_id}/hitl`
   - User downloads the bundle via `GET /projects/{project_id}/runs/{run_id}/artifacts` (zip)
7. **Observability**

   - `GET /projects/{project_id}/runs/{run_id}` for status, token/cost, current node, last error
   - `GET /projects/{project_id}/runs/{run_id}/events` SSE stream for live progress
   - `GET /projects/{project_id}/traceability` for the end-to-end map

---

### Milestones

---

#### Milestone 1: Foundation & Document Ingestion  "The System Can Read"

*Goal:* Stand up the FastAPI backend, persistence layer, local file storage, and a robust document ingestion pipeline that turns uploads into clean, embedded chunks ready for RAG.

*What Users Should Be Able to Do (via API):*

- **Auth & Project Lifecycle**

  - `POST /auth/register` (optional / dev-only) and `POST /auth/login` issue a JWT; all other endpoints require `Authorization: Bearer <token>`
  - JWT validated on every request via FastAPI dependency; expired or invalid tokens return `401`
  - `POST /projects`, `GET /projects`, `GET /projects/{project_id}`, `PATCH /projects/{project_id}` (rename / archive)
  - Project status: `draft`, `ready`, `running`, `archived`
- **Document Upload & Parsing**

  - `POST /projects/{project_id}/documents` accepts multipart upload; supported MIME types: PDF, DOCX, PPTX, XLSX, MD, TXT
  - Server writes the raw file to `./data/projects/{project_id}/uploads/{document_id}.{ext}` via the `FileStore` abstraction, returns `document_id`, kicks off async parse job
  - Parser extracts text per logical section (heading-aware where possible) and persists chunks with stable section IDs
  - Each chunk is embedded and upserted into the ChromaDB `documents` collection with metadata: `document_id`, `section_id`, `section_title`, `page`, `kind` (BRD/PRD/TRD/other), **`project_id` (used as a mandatory filter on every retrieval call)**
  - `GET /projects/{project_id}/documents/{document_id}` returns parse status, section count, and chunk count
  - `GET /projects/{project_id}/documents/{document_id}/sections` returns the structured outline
  - A request whose `document_id` does not belong to the path's `project_id` returns `404`
- **Infrastructure**

  - Swagger at `/docs` complete and accurate; `Authorize` button wired to JWT bearer scheme
  - Structured JSON error envelope: `{ "error": { "code", "message", "details" } }`
  - All config via environment variables (12-factor); no secrets in code (JWT secret, OpenAI key, ChromaDB persist directory, etc.)
  - Health endpoint `GET /healthz` (SQLite + ChromaDB + filesystem ping)

*Invalid Input Handling:*

- Reject unsupported MIME types with `415`
- Reject files over a configured size limit with `413`
- Idempotent upload: same content hash returns existing `document_id` with `200 OK`

*Implementation Hints:*

- Use a background task runner (FastAPI background tasks or Celery)

---

#### Milestone 2: Agentic Design Patterns Knowledge Base  "The System Knows the Playbook"

*Goal:* Build a first-class Pattern KB the planning workflow will use to choose the right agent architecture for the use case.

*What Users Should Be Able to Do:*

- **Pattern Schema** (each entry):

  - `name` (e.g., "ReAct", "Reflection", "Planner-Executor")
  - `intent`  one-paragraph problem this pattern solves
  - `structure`  agents/components and their interactions
  - `when_to_use`  concrete fit signals
  - `when_not_to_use`  anti-patterns and failure modes
  - `prerequisites`  required tools, memory, evaluators
  - `references`  links / citations
  - `tags` (e.g., `["single-agent","tool-use","retrieval"]`)
- **CRUD & Bulk Endpoints**

  - `POST /patterns` (single), `POST /patterns/bulk` (JSON array or YAML upload)
  - `GET /patterns`, `GET /patterns/{id}`, `PATCH /patterns/{id}`, `DELETE /patterns/{id}`
  - Each entry's `intent + when_to_use + structure` is embedded into the ChromaDB `patterns` collection with metadata (`tags`, `source`, `name`) for filtered retrieval
- **Retrieval API**

  - `POST /patterns/search` body: `{ "query": "...", "tags": [...], "top_k": 8 }`
  - Returns ranked patterns with similarity scores and the matched fields
  - This endpoint is what the Planning workflow will call internally
- **Seed Content**

  - Application bootstraps with a seed of **at least 10 canonical patterns**: ReAct, Reflection, Planner-Executor, Multi-Agent Debate, Router, RAG, Tool-Use, Hierarchical Agents, Critic-Refine (Reflexion), Map-Reduce / Parallel Workers
  - Seeding is idempotent

#### Milestone 3: Workflow 1  Requirements Gathering Agent  "Docs In, Spec Out"

*Goal:* A LangGraph workflow that reads the uploaded BRD/PRD/TRD content, applies the **Reflection / Self-Critique** pattern to find gaps, surfaces them to the user **over a per-run WebSocket** (HITL), and produces a canonical, structured requirements artifact. This milestone is where **LangGraph fundamentals** (state schema, nodes, conditional edges, ToolNode, checkpointing) and **Human-in-the-Loop over WebSocket** (`interrupt` / `interrupt_before`, bidirectional clarification, feedback injection on rejection) must be demonstrated end-to-end.

*What Users Should Be Able to Do:*

- **Trigger a Run**

  - `POST /projects/{project_id}/workflows/requirements` body: `{ "document_ids": [...] }`
  - Returns `202 Accepted` with `run_id` and `Location` header to the run resource
  - Idempotency: if a run is already active for the project, return `409 Conflict`
- **WebSocket-Driven Clarification Loop (HITL)**

  - The user opens `WS /projects/{project_id}/runs/{run_id}/hitl` after triggering the run; JWT is required (passed via subprotocol or query string and validated on `accept`)
  - The server is the *initiator* of HITL prompts  when the graph hits a clarification interrupt, the server pushes a typed `clarification_request` message:
    ```json
    { "type": "clarification_request", "request_id": "...", "questions": [{"id":"q1","question":"...","context":"..."}] }
    ```
  - The user replies with a `clarification_response` on the same socket:
    ```json
    { "type": "clarification_response", "request_id": "...", "answers": [{"id":"q1","answer":"..."}] }
    ```
  - The server validates the response, writes answers into graph state, and resumes the graph via `Command(resume=...)`
  - The agent must cap clarification rounds at a configurable limit (default 3); on cap-exceeded the server pushes `clarification_failed` and the run fails
  - If the WebSocket disconnects mid-interrupt, the run **stays paused at the checkpoint**; on reconnect the server re-pushes the pending `clarification_request` (idempotent by `request_id`)
  - REST fallback: `POST /projects/{project_id}/runs/{run_id}/clarifications` performs the same state injection for automation / replay scenarios
- **Output Artifact**

  - Markdown Requirements Document with sections: Goals, Personas, Functional Requirements, Non-Functional Requirements, Constraints, Out-of-Scope, Open Questions
  - Parallel structured JSON (Pydantic schema) for downstream agent consumption
  - Both stored as files under `./data/projects/{project_id}/runs/{run_id}/` and referenced from the run record
- **Approval Gate (HITL over WebSocket)**

  - On reaching the `approval` node (`interrupt_before`), the server pushes:
    ```json
    { "type": "approval_request", "request_id": "...", "artifact": { "requirements_md_url": "...", "requirements_json_url": "..." } }
    ```
  - User responds with `approval_response`:
    ```json
    { "type": "approval_response", "request_id": "...", "decision": "approve" }
    // or
    { "type": "approval_response", "request_id": "...", "decision": "reject", "feedback": "missing privacy requirements" }
    ```
  - On `approve`: project state advances, Planning is unlocked
  - On `reject`: feedback is written into graph state and the graph resumes from the appropriate prior node (not from scratch)
  - REST fallback: `POST /projects/{project_id}/runs/{run_id}/approve` and `POST /projects/{project_id}/runs/{run_id}/reject` (body `{"feedback":"..."}`)

---

#### Milestone 4: Workflow 2  Combined Project & Code Planning  "Pick the Patterns, Plan the Build"

*Goal:* Build a multi-agent planning workflow that takes the approved requirements from Workflow 1, selects appropriate agentic design patterns, conducts research from multiple sources, designs the system architecture, and produces an ordered, code-ready task list. This is the most complex milestone. The workflow must be fully autonomous between approval gates: the user triggers it via REST, observes progress via SSE, and approves or rejects the output over WebSocket.

*Functional Requirements:*

1. **Pattern Selection**  The system must query the Pattern KB using the approved requirements to identify which agentic design patterns best fit the project. The selection must include a rationale explaining *why* each pattern was chosen and which requirements it addresses. Patterns selected must be minimal  do not select more patterns than the project actually needs.
2. **Multi-Source Research**  The system must gather information from three sources in parallel and synthesize the findings:

   - RAG over the user's uploaded documents (scoped to the project)
   - RAG over the Pattern Knowledge Base
   - Web search
   - The LLM's own knowledge serves as a fourth implicit source during synthesis
   - Every claim in the research output must carry a citation tag indicating its source: `[doc:section]`, `[kb:pattern]`, `[web:url]`, or `[llm]`
3. **Architecture Design**  The system must produce an architecture document (Markdown + structured JSON) covering: system components, data flow, agent topology, tool inventory, deployment considerations, and identified risks. The architecture must reference the selected patterns and incorporate research findings.
4. **Task Planning**  The system must decompose the architecture into an ordered, code-ready task list. Each task must include:

   - Title and description
   - Target files to be created/modified
   - Acceptance criteria
   - Dependencies on other tasks (no forward dependencies allowed)
   - References to the patterns that inform the task's implementation
5. **Quality Validation**  The plan must be validated before presenting it to the user. Validation must check:

   - **Coverage**: every requirement maps to at least one task
   - **Ordering**: no task depends on a task that comes after it
   - **Pattern fidelity**: selected patterns are actually reflected in the task descriptions
   - **Atomicity**: each task is implementable independently
   - If validation fails, the plan must be revised (up to a configurable iteration limit) before surfacing to the user
6. **Complexity-Based Routing**  The system must route simple projects (few requirements, straightforward patterns) through a lighter planning path and complex projects through the full architecture + planning + validation cycle.
7. **Approval Gate**  After the task list passes validation, the system must pause and present the complete plan to the user for approval via WebSocket. The user can:

   - **Approve**: unlocks Code Generation (Workflow 3)
   - **Reject with feedback**: the system revises the plan incorporating the feedback, without restarting from scratch

*What Users Should Be Able to Do:*

- `POST /projects/{project_id}/workflows/planning`  trigger the workflow (`202 Accepted`)
- `GET /projects/{project_id}/runs/{run_id}`  check status, current stage, iteration count, token/cost
- `GET /projects/{project_id}/runs/{run_id}/events`  SSE stream for live progress
- `WS /projects/{project_id}/runs/{run_id}/hitl`  receive `approval_request`, send `approval_response`
- `GET /projects/{project_id}/runs/{run_id}/tasks`  retrieve the final ordered task list
- `PATCH /projects/{project_id}/runs/{run_id}/tasks/{task_id}`  edit a task's description, reorder (with dependency validation), or split a task before approving
- `POST /projects/{project_id}/runs/{run_id}/approve`  REST fallback to approve and unlock code generation

*Required Agent Design Patterns (must be demonstrated in this workflow):*

- **Orchestrator-Worker**: a supervisor coordinates the specialist agents (pattern selector, researcher, architect, planner, critic)
- **Plan-and-Execute**: the planner produces a structured plan; downstream agents consume it step-by-step
- **Parallelization & Routing**: research branches run in parallel; a router dispatches to lightweight vs. heavyweight planning paths
- **Evaluator-Optimizer**: the planner-critic validation cycle follows the generate-evaluate-revise shape

*Output Artifacts:*

- Selected-patterns report with rationale and requirement mapping
- Research log with all findings and their source citations
- Architecture document (Markdown + structured JSON)
- Ordered task list with acceptance criteria and pattern references

*Things to Think About:*

- How do you prevent the pattern selector from over-selecting patterns when fewer would suffice?
- How do you ensure research claims are grounded in actual source material and not hallucinated?
- What does "task atomicity" mean practically? (A developer should be able to complete one task without needing information from a future task.)
- Web-search results are untrusted external content  how do you handle them safely in prompts?
- What happens if the validation loop runs indefinitely? (Bound iterations; fail with a clear error if the threshold is never met.)

#### Milestone 5: Workflow 3  Code Generation  "Agents Write the Code"

*Goal:* A background developer agent executes the approved task list sequentially, producing real code; reviewer sub-agents validate; final output is a downloadable bundle. This milestone is the canonical implementation of the **Orchestrator-Worker** and **Plan-and-Execute** patterns, and the per-task **Developer ” Reviewer** cycle is a second instance of the **Evaluator-Optimizer** pattern.

*Required Graph Shape:*

- Top-level orchestrator graph iterates the approved task list (Plan-and-Execute consumer).
- Per-task work runs inside a **dynamically constructed subgraph** whose composition depends on the task's `pattern_refs` (e.g., a task tagged with `Reflection` builds a developer  critic  developer cycle; a task tagged with `Tool-Use` adds a `ToolNode` for any required tools).
- Reviewer sub-agents (`workflow_reviewer`, `prompt_reviewer`, `security_reviewer`) run in **parallel** on the developer's output and their verdicts are reduced into a single pass/fail with feedback.
- `interrupt_after` the final task pushes an `approval_request` over `WS /projects/{project_id}/runs/{run_id}/hitl`; the user inspects the bundle and sends `approval_response` to mark the project complete (REST fallback: `POST /projects/{project_id}/runs/{run_id}/approve`).
- SQLite checkpointer enables resume of a partially-generated codebase.

*What Users Should Be Able to Do:*

- **Trigger Code Generation**

  - `POST /projects/{project_id}/workflows/codegen`  `202 Accepted`
  - One active codegen run per project (in-memory lock)
- **Sequential Task Execution**

  - Developer agent reads requirements, architecture spec, the current task, and **the pattern entries the task references**
  - Pattern-aware prompting: the prompt template injects the matched pattern's `structure` and `prerequisites` so generated code reflects the chosen pattern
  - Writes files to a structured workspace on the local filesystem at `./data/projects/{project_id}/runs/{run_id}/workspace/...` via the `FileStore` abstraction
  - Marks task `in_progress`  `completed` | `failed`; persists generated file paths and diffs
- **Reviewer Sub-Agents**

  - `workflow_reviewer`  checks generated agent orchestration matches the selected patterns
  - `prompt_reviewer`  checks generated prompts for clarity, role, and injection-resistance
  - `security_reviewer`  checks for OWASP basics (input validation, secrets, error leakage)
  - On reviewer failure, developer iterates with feedback up to a configurable retry cap
- **Bundle & Download**

  - On completion, the system zips the workspace to `./data/projects/{project_id}/runs/{run_id}/bundle.zip` and `GET /projects/{project_id}/runs/{run_id}/artifacts` streams the file via FastAPI `FileResponse`
  - Bundle includes a `MANIFEST.json` mapping task IDs  file paths
- **Approval**

  - `POST /projects/{project_id}/runs/{run_id}/approve` marks the project `completed`

---

#### Milestone 6: Orchestration, Resumability & Streaming  "Production-Grade Runs"

*Goal:* Make every run resumable, observable in real time, and safe under concurrency. This milestone is where **checkpointing**, **interrupt_before / interrupt_after**, **hooks (pre/post node, custom middleware)**, and **graph visualization** are formalized as cross-cutting infrastructure used by all three workflows.

*What Users Should Be Able to Do:*

- **Resumability**

  - All three workflows use **LangGraph checkpointing** with the SQLite saver (`AsyncSqliteSaver`); the checkpoint database is a separate file (e.g., `./data/checkpoints.sqlite`) from the application database
  - Killing the process mid-run and restarting must resume from the last successful node
  - `POST /projects/{project_id}/runs/{run_id}/resume` to manually resume after a transient failure
- **Streaming**

  - `GET /projects/{project_id}/runs/{run_id}/events` SSE stream emits typed events: `node_started`, `node_completed`, `tool_called`, `tokens_used`, `clarification_requested`, `error`, `run_completed`
  - Events are persisted to a `run_events` table so reconnecting clients can replay from `Last-Event-ID`
- **WebSocket HITL Channel (required, single transport for human input)**

  - Endpoint: `WS /projects/{project_id}/runs/{run_id}/hitl`. JWT required at handshake (subprotocol or `?token=` query param); reject with close code `401` on missing/invalid token, `403` on token-vs-run owner mismatch (also `404` if the `run_id` does not belong to the path's `project_id`).
  - **Server-initiated** request/response only  the server pushes prompts when the graph hits an `interrupt`; the client pushes responses. The client must not send unsolicited messages (server replies with `unexpected_message` and closes with `400` on violation).
  - **Message envelope** (JSON): every message has `type`, `request_id` (UUID), and a `payload`. Required `type` values: `clarification_request`, `clarification_response`, `approval_request`, `approval_response`, `clarification_failed`, `run_completed`, `error`, `ping`, `pong`.
  - **Idempotency & resume**: each `*_request` has a stable `request_id` derived from the LangGraph checkpoint id. If the socket disconnects while a request is pending, the server re-sends the *same* `request_id` on reconnect. Duplicate `*_response` messages for the same `request_id` are ignored after the first.
  - **Liveness**: server sends `ping` every 20s; client must reply `pong` within 10s or the server closes (`408`). The underlying run is **not** affected by socket disconnection \u2014 it stays paused at the checkpoint.
  - **Single subscriber**: at most one active socket per `run_id`; a second connection forces the first closed with `409` (the second wins).
  - **REST equivalence**: every WebSocket interaction has a REST counterpart (`POST /projects/{project_id}/runs/{run_id}/clarifications`, `POST /projects/{project_id}/runs/{run_id}/approve`, `POST /projects/{project_id}/runs/{run_id}/reject`) so the system is testable and scriptable without a WebSocket client.
- **Status & Polling**

  - `GET /projects/{project_id}/runs/{run_id}` returns canonical status, current node, token/cost totals, and last error
  - `GET /projects/{project_id}/runs?status=...` for filtered listings within a project (no cross-project listing endpoint exists)
- **Concurrency & Idempotency**

  - At most one active run per project enforced via in-memory lock (`asyncio.Lock` keyed by `project_id`)
  - Workflow-trigger endpoints accept an `Idempotency-Key` header; replays return the original `run_id`
  - Cost/token ceilings per run, configurable; runs that exceed must fail with status `failed_cost_ceiling`
- **Hooks & Middleware (required)**

  - Implement a reusable `node_hook` layer that wraps every LangGraph node
  - `pre_node` hook: log structured entry, start timer, emit SSE `node_started`
  - `post_node` hook: record tokens/cost to `usage` table, emit SSE `node_completed`, persist intermediate state snapshot
  - On exception: emit SSE `error`, mark run state, re-raise so the graph fails cleanly
  - The hook layer is the single observability seam  no ad-hoc logging inside node functions
- **Graph Visualization**

  - Every compiled graph exposes its Mermaid representation via `graph.get_graph().draw_mermaid_png()`
  - `GET /workflows/{name}/graph.png` returns the rendered diagram (cached on disk)
  - README embeds the three workflow diagrams

---

#### Milestone 7: Observability & Traceability (Brownie Points)

*Goal:* Per-run cost transparency and end-to-end traceability from source documents to generated files.

*Capabilities:*

- **Native Token Tracing**  implement a custom `TokenTracer` middleware that wraps every LLM call, extracts OpenAI's `usage` response fields (`prompt_tokens`, `completion_tokens`, `total_tokens`), computes cost from a configurable pricing table (`MODEL_PRICING` env var or config file), and writes each record to the `usage` table with `run_id`, `project_id`, `node`, `tool`, `model`, `tokens_in`, `tokens_out`, `cost_usd`, and `timestamp`. Expose `GET /projects/{project_id}/runs/{run_id}/usage` to query aggregated and per-node breakdowns.
- **Token & Cost Accounting**  per node, per agent, per run, persisted to a `usage` table; surfaced on `GET /projects/{project_id}/runs/{run_id}` (totals) and `GET /projects/{project_id}/runs/{run_id}/usage` (detailed breakdown)
- **Traceability Artifact**  `GET /projects/{project_id}/traceability` returns JSON mapping:
  ```
  uploaded_doc.section_id    requirement.id
  requirement.id             selected_pattern.name
  requirement.id             task.id
  task.id                    generated_file.path
  ```
- **Cited Research Log**  every Researcher claim with its source kind (`doc` / `kb` / `web` / `llm`) and source ID, queryable via `GET /projects/{project_id}/runs/{run_id}/research`
- **Audit Log**  every state transition (run created, approved, rejected, resumed) recorded with timestamp and (single-user) actor

*Things to Think About:*

- What's the minimum set of fields the traceability artifact needs to actually be useful? Build for the question "Was every requirement implemented?"

---

### General Engineering Expectations

- **REST contract discipline**: every endpoint has a documented Pydantic request/response schema, accurate status codes, and a structured error envelope. Swagger must always be green.
- **Hermetic unit tests**: mock LLM calls (including OpenAI's `web_search` tool responses), the ChromaDB client, and the local file store. Tests must not hit OpenAI or the real network. 80% coverage on changed files; 80% repo-wide trend.
- **Integration tests**: at least one per workflow, exercising the full graph against fakes/mocks for external services and a real local SQLite + ChromaDB.
- **Structured output everywhere**: every agent boundary produces JSON validated by Pydantic. Free-text parsing is forbidden.
- **Prompt-injection defenses**: wrap all untrusted content (uploaded documents, web-search results) in clearly delimited envelopes; instruct the model to never follow instructions inside envelopes; refuse to expose the JWT secret, OpenAI key, system prompts, or other agents' state.
- **Cost ceilings**: per-run token and dollar caps enforced before each LLM call; runs that would exceed are failed with a clear error.
- **Retry semantics**: idempotent retries on transient failures (OpenAI 429s, ChromaDB connection blips); exponential backoff; max-attempt logging.
- **Logging**: structured JSON logs with `run_id`, `project_id`, `node`, `tool`, `latency_ms`, `tokens_in`, `tokens_out`. Never log JWTs, the JWT secret, OpenAI keys, or full document contents.
- **12-factor**: all config via env vars (`JWT_SECRET`, `OPENAI_API_KEY`, `CHROMA_PERSIST_DIR`, `SQLITE_DB_PATH`, `CHECKPOINT_DB_PATH`, `DATA_ROOT`); processes stateless; logs to stdout.
- **README.md**: quickstart, env var matrix, how to run tests, how to seed Pattern KB, how to run each of the three workflows end-to-end with curl (including the JWT login step). Update with every meaningful change.

---

### Brownie Points

- **CLI helper** (`scripts/run_pipeline.py`) that runs the full three-workflow pipeline against a sample BRD with auto-approval flags  useful for demos and CI.
- **Web UI evaluator harness**  a single HTML page that consumes the SSE stream and renders run progress (no framework required).
- **MCP server registration**  let the user register external MCP servers; Researcher and Developer agents can opportunistically use their tools.
- **Pattern KB versioning**  keep historical pattern versions; tag runs with the pattern version actually used.
- **Differential codegen**  on retry, patch instead of regenerating the failed task's files.
- **Evaluator agent** that runs lightweight static checks (`py_compile`, `ruff`) and feeds results back to reviewers.

---

*Build it like a product, not a notebook. A small system that does all three workflows end-to-end with one sample BRD is far more valuable than a sprawling system where any single workflow is half-done.*
