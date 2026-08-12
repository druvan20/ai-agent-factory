# AI Agent Factory

Backend-only document-driven code generator. Upload BRD/PRD/TRD documents, curate an
agentic design-pattern knowledge base, and run three sequential LangGraph workflows:
**Requirements → Planning → Code Generation**. Progress streams over SSE; human-in-the-loop
clarifications and approvals use a per-run **WebSocket**.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI |
| Workflows | LangGraph (`StateGraph`, subgraphs, `interrupt`, `ToolNode`) |
| Checkpointing | SQLite via `langgraph-checkpoint-sqlite` (`AsyncSqliteSaver` + sync `SqliteSaver`) |
| LLM | OpenAI (`gpt-4o` by default) |
| Vector store | ChromaDB (`documents`, `patterns`) |
| ORM | SQLAlchemy async + aiosqlite |
| Auth | JWT (HS256) |
| Streaming | SSE + WebSockets (HITL only) |

---

## Quickstart

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set OPENAI_API_KEY and SECRET_KEY
uvicorn app.main:app --reload --port 8000
```

Swagger UI: http://localhost:8000/docs  
Health: `GET /health` and `GET /healthz`

Docker:

```bash
docker build -t agent-factory .
docker run -p 8080:8080 -e OPENAI_API_KEY=sk-... -e SECRET_KEY=... agent-factory
```

---

## Environment variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | JWT signing secret |
| `OPENAI_API_KEY` | OpenAI API key |
| `OPENAI_MODEL` | Chat model (default `gpt-4o`) |
| `DATABASE_URL` | App SQLite URL |
| `CHECKPOINT_DB_PATH` | LangGraph checkpoint DB file |
| `CHROMA_PERSIST_DIR` | Chroma persistence |
| `UPLOAD_DIR` | Project file root (`./data/projects`) |
| `MODEL_PRICING` | Optional JSON pricing table |
| `MAX_TOKENS_PER_RUN` / `MAX_COST_USD_PER_RUN` | Cost ceilings |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Seeded single user |

---

## End-to-end curl (JWT + three workflows)

```bash
# 1. Login
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=admin&password=admin" | jq -r '.access_token // .data.access_token')

# 2. Create project
PID=$(curl -s -X POST http://localhost:8000/api/v1/projects \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Demo","description":"Sample"}' | jq -r '.data.id')

# 3. Upload a BRD
DOC=$(curl -s -X POST http://localhost:8000/api/v1/projects/$PID/documents \
  -H "Authorization: Bearer $TOKEN" -F "file=@sample_brd.md" | jq -r '.data.id')

# 4. Start Requirements (W1)
RID=$(curl -s -X POST http://localhost:8000/api/v1/projects/$PID/workflows/requirements \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{}' | jq -r '.data.id')

# 5. SSE progress
# curl -N http://localhost:8000/api/v1/projects/$PID/runs/$RID/events

# 6. HITL WebSocket (or REST fallback)
# REST clarify:
# curl -X POST .../runs/$RID/clarifications -H "Authorization: Bearer $TOKEN" \
#   -d '{"answers":{"q1":"..."}}'
# REST approve:
curl -s -X POST http://localhost:8000/api/v1/projects/$PID/runs/$RID/approve \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"approved":true}'

# 7. Planning (W2) then Codegen (W3) similarly using
# POST .../workflows/planning  {"requirements_run_id":"..."}
# POST .../workflows/codegen   {"planning_run_id":"..."}
# GET  .../runs/{codegen_run_id}/artifacts
```

Graph PNGs: `GET /workflows/{requirements|planning|codegen}/graph.png`

---

## WebSocket HITL protocol

Endpoint: `WS /projects/{project_id}/runs/{run_id}/hitl?token=<JWT>`  
(also under `/api/v1/...` and legacy `/ws/runs/{run_id}`)

Envelope:

```json
{ "type": "<string>", "request_id": "<uuid>", "payload": { } }
```

| Type | Direction | Meaning |
|---|---|---|
| `clarification_request` | server→client | Graph paused; answer questions |
| `clarification_response` | client→server | Answers (`payload.answers`) |
| `approval_request` | server→client | Review artifact |
| `approval_response` | client→server | `decision: approve\|reject` + optional `feedback` |
| `clarification_failed` | server→client | Cap exceeded |
| `run_completed` | server→client | Terminal success |
| `error` | server→client | Failure |
| `ping` / `pong` | both | Liveness (server pings every 20s) |
| `unexpected_message` | server→client | Client sent unsolicited type → close `400` |

Rules: one socket per run (second wins with `409`); reconnect re-pushes the same `request_id`; duplicate responses ignored. REST fallbacks: `POST .../clarifications`, `.../approve`, `.../reject`, `.../resume`.

---

## Required concepts → code map

| Concept | Location |
|---|---|
| Plan-and-Execute | [`app/workflows/w2_planning.py`](app/workflows/w2_planning.py) planner; [`app/workflows/w3_codegen.py`](app/workflows/w3_codegen.py) task_loop |
| Reflection / Self-Critique | W1 `gap_analysis`; W2 `critic`; W3 `_generate_with_reflection` |
| Parallelization & Routing | W2 `assess_complexity` + research fan-out (`research_docs` ∥ `research_kb` ∥ `research_web`) |
| Orchestrator-Worker | W3 `run_task_unit` / task subgraph; W2 specialist agents |
| Evaluator-Optimizer | W2 planner↔critic; W3 developer↔reviewers |
| StateGraph / conditional edges | All three workflow modules |
| Checkpointing | [`app/workflows/checkpointer.py`](app/workflows/checkpointer.py) |
| ToolNode + web search | W2 `openai_web_search` + `ToolNode` |
| HITL `interrupt` | W1 clarification/approval; W2/W3 approval gates |
| Hooks (SSE + usage) | [`app/workflows/hooks.py`](app/workflows/hooks.py), [`app/services/token_tracer.py`](app/services/token_tracer.py) |
| Subgraphs | W2 research subgraph; W3 `_build_task_subgraph` |
| Traceability / usage | [`app/routers/traceability.py`](app/routers/traceability.py), `GET .../runs/{id}/usage` |

---

## Pattern KB

Bootstraps ~12 canonical patterns (idempotent) via [`app/services/pattern_seed.py`](app/services/pattern_seed.py).  
CRUD + search: `/api/v1/patterns`, `POST /api/v1/patterns/search`.

---

## Tests

```bash
pip install pytest pytest-asyncio httpx
pytest tests/ -q
```

Tests mock LLM/RAG and do not call OpenAI.

---

## Project layout

```
app/
  main.py              # FastAPI app + lifespan (seeds admin, patterns, checkpointer)
  workflows/           # W1 / W2 / W3 LangGraphs + checkpointer + hooks
  routers/             # REST, SSE, health, traceability
  services/            # orchestration, SSE/WS, ingestion, token tracer
  store/               # FileStore + Chroma
  models/              # SQLAlchemy (incl. usage, run_events)
data/                  # runtime DB, checkpoints, uploads (gitignored)
```

**In-memory trade-off:** SSE fan-out, WS sessions, and per-project locks are process-local (lost on restart). Checkpoints and SQLite app data survive restarts; resume with `POST .../runs/{id}/resume`.
