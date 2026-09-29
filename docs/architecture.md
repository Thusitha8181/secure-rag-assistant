# Architecture

Secure RAG Assistant is an internal chatbot. Retrieval is role-scoped in the vector database, so the language model never sees documents the caller cannot read.

## Runtime

```
Browser  --JWT-->  Next.js  --SSE /api/*-->  FastAPI
                                              |
                                              +-- LangGraph pipeline
                                              |     input guard -> analyze -> retrieve | HR SQL -> generate -> output guard
                                              +-- Qdrant (hybrid search, allowed_roles filter)
                                              +-- DuckDB (HR aggregates, hr/c_level only)
                                              +-- Groq (chat, classifiers, prompt-guard)
                                              +-- DynamoDB (per-request usage)
                                              +-- CloudWatch EMF + LangSmith
```

On AWS a single ALB splits traffic: `/api/*` goes to FastAPI so streamed answers are not buffered by Next.js; everything else hits the frontend service. Locally, Next.js proxies `/api/*` to the backend (`frontend/src/app/api/[...path]/route.ts`).

## RBAC

The policy lives in one file, [`backend/app/rbac/policy.py`](../backend/app/rbac/policy.py). Ingestion stamps every chunk with `department` and `allowed_roles`. Every Qdrant query includes a `must` filter on the caller's role.

That is the security boundary. The prompt also tells the model not to speculate, but a prompt is not access control.

Defence in depth:

1. Qdrant payload filter (primary).
2. Retriever drops any chunk whose department the role cannot read and logs `RBAC invariant violated` (CloudWatch alarm on the first occurrence).
3. Restricted-doc margin: if a forbidden neighbour out-scores the best allowed hit, the API returns `access_denied` rather than a weakly grounded answer from general docs.
4. Presidio redacts employee PII in the answer unless the role is `hr` or `c_level`.

## Pipeline

[`backend/app/graph/pipeline.py`](../backend/app/graph/pipeline.py) is a LangGraph state machine.

1. **Input guard** — regex/heuristic injection checks plus Groq `llama-prompt-guard`. Presidio masks PII the user typed (employee ids, salary, DOB) before any further LLM call.
2. **Analyze** — structured output: `company_question`, `hr_analytics`, `smalltalk`, or `out_of_scope`. Smalltalk is answered briefly; out-of-scope is refused.
3. **Retrieve** — FastEmbed dense + BM25 sparse, Qdrant fusion, optional cross-encoder rerank, RBAC filter. Empty or off-policy results become `no_context` / `access_denied`.
4. **HR analytics** — only `hr` and `c_level`. The model writes a single `SELECT`; DuckDB executes it after a SQL guard that rejects writes and out-of-scope tables.
5. **Generate** — grounded on retrieved text or SQL rows, with source citations.
6. **Output guard** — role-aware Presidio pass on the final answer.
7. **Meter** — tokens × list prices in [`backend/app/llm/pricing.yaml`](../backend/app/llm/pricing.yaml). Free-tier Groq still emits a USD figure so alarms and the C-level dashboard mean something.

Statuses the UI badges: `answered`, `blocked`, `out_of_scope`, `access_denied`, `no_context`, `smalltalk`, `error`.

## Data

Fictional FinSolve corpus in `data/raw/`:

| Path | Department |
|---|---|
| `finance/*.md` | finance |
| `marketing/*.md` | marketing |
| `hr/hr_data.csv` | hr (row-level records + DuckDB table) |
| `engineering/*.md` | engineering |
| `general/*.md` | general (all roles) |

Markdown is parsed with Docling and split with `HybridChunker` so heading paths survive on the chunk. The ingest image is a separate Dockerfile target; CI runs it when `data/` or ingestion code changes.

## Auth

JWT (`HS256`). Demo users in [`backend/app/auth/users.yaml`](../backend/app/auth/users.yaml), one per role, shared password `demo1234`. The login page lists them so a recruiter can switch roles without typing.

## Evaluation and release

[`evals/thresholds.yaml`](../evals/thresholds.yaml) is the contract.

- **PR (`ci.yml`)** — ruff, mypy, pytest (including RBAC leakage tests), frontend lint/build, Terraform fmt. Then docker-compose + ingest + the **fast** suite (all red-team cases + golden cases tagged `fast`).
- **main (`deploy.yml`)** — OIDC into AWS, push images to ECR, register new task definitions, wait for ECS stability, optional ingest task, **full** suite against the live ALB URL. A failed gate updates both services back to the previous task definition ARNs. ECS circuit breakers cover broken health checks independently of evals.

Evals talk to the API as real demo users. They do not import backend policy code, so they test the requirement, not the implementation.

## Cost controls

| Mechanism | What it does |
|---|---|
| Per-user daily token quota | HTTP 429 when the caller exceeds `DAILY_TOKEN_QUOTA` |
| CloudWatch `CostUSD` / `TotalTokens` | Email via SNS (daily LLM cost, 5-minute token spike) |
| AWS Budget | Account monthly cap, 80% actual and 100% forecast |
| Fargate Spot + no NAT | Infra default; documented because it is a cost/reliability tradeoff |
| Pricing table | Groq free tier is $0; we still record list price |

## What was deliberately not built

- **Cognito / SSO** — demo JWT is enough for a recruiter to switch roles in one click.
- **Qdrant Cloud** — self-hosted on EFS so RBAC payload filters and the corpus stay in-account.
- **NAT gateway** — ~$30/month saved; tasks need public IPs to pull images. Security groups still deny inbound except from the ALB.
- **Multi-task Qdrant** — one writer on one EFS volume. A second task would corrupt storage.
