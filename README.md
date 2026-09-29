# Secure RAG Assistant

Internal chatbot for **FinSolve Technologies**. It answers questions from company documents, but each user only retrieves what their role is allowed to see.

RBAC is enforced in Qdrant at retrieval time, not in the prompt. Guardrails stop prompt injection, mask PII by role, and refuse out-of-scope questions. Every change is gated by evals (Ragas + a red-team set). Token cost is metered even on Groq's free tier.

![Login with one-click demo roles](docs/screenshots/login.png)

## Why this design

| Choice | Why |
|---|---|
| Filter in the vector store, not the prompt | The model never sees payroll if you are in Finance. A jailbreak cannot leak a chunk that was never retrieved. |
| Hybrid search (dense + BM25) + reranker | Keyword questions like "gross margin 2024" and semantic ones both work. |
| Dual HR path (chunks + DuckDB) | Vector search is poor at "average attendance in Sales". HR/C-level get a guarded `SELECT` tool for aggregates. |
| Groq + FastEmbed | Chat is free-tier Groq (`gpt-oss`). Embeddings run locally because Groq has no embeddings API. |
| Eval gates in CI/CD | A PR that leaks HR data cannot merge. A deploy that fails the full suite rolls back automatically. |
| No NAT, Fargate Spot | Public subnets + locked security groups. Tear down with `make destroy`. |

## Architecture

```mermaid
flowchart LR
    User --> NextJS[Next.js]
    NextJS -->|"JWT + SSE"| API[FastAPI]
    API --> Graph[LangGraph]
    Graph --> Guard[Guardrails]
    Graph --> Qdrant[(Qdrant)]
    Graph --> Groq[Groq]
    Graph --> DuckDB[(HR DuckDB)]
    API --> Usage[(DynamoDB)]
    API --> CW[CloudWatch EMF]
    Graph --> LangSmith[LangSmith]
    CW --> Alarms[SNS email]
```

Request path:

```mermaid
flowchart TD
    inGuard["Input: injection + PII in the question"] --> analyze["Classify: company / HR analytics / smalltalk / out of scope"]
    analyze -->|out of scope| refuse[Refuse]
    analyze --> retrieve["Hybrid retrieve with a hard role filter"]
    analyze --> sql["HR SQL tool: hr and c_level only"]
    retrieve --> gen["Answer with citations"]
    sql --> gen
    gen --> outGuard["Output: role-aware PII redaction"]
    outGuard --> meter["Record tokens and list-price cost"]
```

More detail: [docs/architecture.md](docs/architecture.md).

## Roles

Access is by **document department**. C-level sees everything. Everyone can read `general/` (handbook, policies, FAQs).

| | Finance docs | Marketing docs | HR / payroll | Engineering docs | General |
|---|---|---|---|---|---|
| Finance | yes | | | | yes |
| Marketing | | yes | | | yes |
| HR | | | yes | | yes |
| Engineering | | | | yes | yes |
| C-level | yes | yes | yes | yes | yes |
| Employee | | | | | yes |

Raw employee PII (salary, DOB, email) is visible only to **HR** and **C-level**. Other roles get redacted output even if a chunk slipped through.

![HR analytics with citations](docs/screenshots/chat-hr-analytics.png)

![Guardrail badges on a blocked or refused turn](docs/screenshots/chat-guardrails.png)

## Local quickstart

You need Docker, Python 3.12 (`uv`), Node 22 (`pnpm`), and a free [Groq API key](https://console.groq.com/keys).

```bash
cp .env.example .env          # paste GROQ_API_KEY
make install
make up                       # Qdrant, DynamoDB Local, API, Next.js
make ingest                   # chunk, embed, upsert into Qdrant
open http://localhost:3000
```

Native (API + UI on the host, only the databases in Docker):

```bash
make infra-local
make ingest-local
make dev-backend              # :8000
make dev-frontend             # :3000
```

### Demo users

Password for every account is `demo1234`.

| Username | Role |
|---|---|
| `fiona.finance` | Finance |
| `mark.marketing` | Marketing |
| `hana.hr` | HR |
| `eli.engineering` | Engineering |
| `cara.ceo` | C-level (also sees `/admin/usage`) |
| `evan.employee` | Employee |

Try the same question as two roles. Finance can ask about 2024 gross margin; an employee asking the same thing is denied.

![C-level token and cost dashboard](docs/screenshots/usage-dashboard.png)

## Evaluation

69 cases: 40 golden (allowed questions with expected facts) and 29 red-team (RBAC bypass, injection, PII, out-of-scope).

| Metric | Baseline | Gate |
|---|---|---|
| RBAC leakage | **0** | = 0 |
| PII leak | **0** | = 0 |
| Injection block rate | **1.00** | ≥ 0.85 |
| RBAC denial accuracy | **1.00** | ≥ 0.90 |
| Out-of-scope refusal | **0.83** | ≥ 0.80 |
| Golden answer rate | **0.95** | ≥ 0.85 |
| Faithfulness (Ragas) | **1.00** | ≥ 0.70 |
| Answer relevancy | **0.86** | ≥ 0.60 |
| p95 latency | 10.1 s | ≤ 15 s |
| Avg cost / query (list price) | $0.00018 | ≤ $0.002 |

```bash
make evals-fast    # PR gate: red-team + a small golden/Ragas sample
make evals         # full suite, used after every deploy
```

A failing gate exits non-zero. In GitHub Actions that blocks the PR (`ci.yml`) or rolls the ECS services back (`deploy.yml`). Latest captured run: [docs/evals/baseline-full.json](docs/evals/baseline-full.json).

## AWS deploy

Terraform in `infra/terraform/` creates:

- VPC **without a NAT gateway** (tasks in public subnets, security groups only)
- ALB (`/api/*` → FastAPI so SSE is not proxied twice, `/*` → Next.js)
- ECS Fargate: backend, frontend, Qdrant on EFS, plus an on-demand ingest task
- DynamoDB usage table, SSM secrets, CloudWatch dashboard and alarms, SNS email, AWS Budget
- GitHub OIDC deploy role (no long-lived AWS keys)

Expect roughly **$30–50/month** while it is up. Confirm the SNS subscription email after the first apply. Tear everything down with `make destroy`.

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # alert_email, github_repository
export TF_VAR_groq_api_key=...
export TF_VAR_langsmith_api_key=...            # optional
terraform init && terraform apply
make gh-vars                                   # copies outputs into GitHub Actions variables
```

Then in the GitHub repo:

1. Create a `production` environment.
2. Add secrets `GROQ_API_KEY` and optionally `LANGSMITH_API_KEY` (evals only; the app reads Groq from SSM).
3. Push `main`. `deploy.yml` builds images, deploys, re-indexes if `data/` changed, runs the full eval suite, and rolls back on a failed gate.

## Monitoring and cost

- **LangSmith** traces every graph step, tagged with role and user.
- **CloudWatch EMF** from the API: tokens, list-price USD, latency, guardrail hits, access-denied. Alarms email you on daily LLM cost, token spikes, error rate, p95 latency, and RBAC invariant violations.
- **AWS Budgets** alerts at 80% actual / 100% forecast of the monthly cap (default $60).
- **Per-user daily token quota** (`DAILY_TOKEN_QUOTA`, default 200k) returns HTTP 429 when exceeded.
- C-level UI at `/admin/usage` reads the DynamoDB usage log.

## Stack

| Layer | Choice |
|---|---|
| API | Python 3.12, FastAPI, LangGraph, LangChain |
| LLM | Groq `openai/gpt-oss-120b` (fallback `gpt-oss-20b`) |
| Embeddings | FastEmbed `bge-small-en-v1.5` + BM25 |
| Vectors | Qdrant hybrid search, RRF, payload filter on `allowed_roles` |
| Ingestion | Docling `HybridChunker` on markdown; HR CSV → chunks + DuckDB |
| Guardrails | Llama Prompt Guard, scope classifier, Microsoft Presidio |
| Frontend | Next.js 16, Tailwind, shadcn/ui |
| Eval | Ragas + custom RBAC/PII/injection checks |
| Cloud | AWS ECS Fargate, Terraform, GitHub Actions OIDC |

## Repo layout

```
backend/     FastAPI app, ingestion, graph, guardrails, tests
frontend/    Next.js UI
evals/       Golden + red-team datasets, harness, thresholds.yaml
data/raw/    FinSolve sample corpus (fictional)
infra/       Terraform + ecs-deploy.sh
docs/        Architecture, screenshots, baseline eval JSON
```

## License

This is a portfolio project with fictional company data. Not affiliated with a real FinSolve.
