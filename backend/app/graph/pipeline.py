"""LangGraph request pipeline.

input_guard -> analyze -> (retrieve | hr_analytics) -> generate -> finalize
Any guard can short-circuit straight to finalize with a canned answer and a status.
"""

from __future__ import annotations

import asyncio
import logging
import operator
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.graph import prompts
from app.guardrails.injection import InjectionGuard
from app.guardrails.pii import PIIGuard, StreamingRedactor, get_pii_guard
from app.hr.store import HRStore, UnsafeSQLError, get_hr_store
from app.llm.models import chat_model, classifier_model, generator_model, prompt_guard_model
from app.rbac.policy import HR_ANALYTICS_ROLES, PII_PRIVILEGED_ROLES, Role, can_access
from app.retrieval.retriever import Retriever, get_retriever

log = logging.getLogger(__name__)

Status = Literal[
    "answered", "blocked", "out_of_scope", "access_denied", "no_context", "smalltalk", "error"
]


class QueryAnalysis(BaseModel):
    intent: Literal["company_question", "hr_analytics", "smalltalk", "out_of_scope"]
    standalone_question: str = Field(description="Self-contained rewrite of the latest message")


class SQLQuery(BaseModel):
    sql: str = Field(description="A single DuckDB SELECT statement")


class ChatState(TypedDict, total=False):
    question: str
    history: list[dict[str, str]]
    user: dict[str, str]
    sanitized_question: str
    query: str
    intent: str
    status: Status
    guardrails: Annotated[list[dict[str, Any]], operator.add]
    contexts: list[dict[str, Any]]
    top_similarity: float
    restricted_departments: list[str]
    sql: str
    answer: str
    sources: list[dict[str, Any]]


@dataclass
class PipelineDeps:
    settings: Settings
    retriever: Retriever
    pii: PIIGuard
    injection: InjectionGuard
    analyzer: Runnable
    generator: Runnable
    sql_writer: Runnable
    hr_store: HRStore | None


def _guard(name: str, action: str, detail: Any = None) -> dict[str, Any]:
    return {"name": name, "action": action, "detail": detail}


def _role(state: ChatState) -> Role:
    return Role(state["user"]["role"])


def _history_messages(state: ChatState, max_turns: int) -> list[BaseMessage]:
    msgs: list[BaseMessage] = []
    for turn in (state.get("history") or [])[-max_turns * 2 :]:
        content = str(turn.get("content", ""))[:2000]
        if turn.get("role") == "assistant":
            msgs.append(AIMessage(content=content))
        elif turn.get("role") == "user":
            msgs.append(HumanMessage(content=content))
    return msgs


def _chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


_FULLWIDTH_CITATION = re.compile(r"【(\d{1,2})(?:†[^】]*)?】")


def _normalize_citations(text: str) -> str:
    # gpt-oss models often cite as 【2】 or 【2†source】; normalise to [2].
    return _FULLWIDTH_CITATION.sub(r"[\1]", text)


def _format_context(contexts: list[dict[str, Any]]) -> str:
    blocks = []
    for i, c in enumerate(contexts, 1):
        where = c["source"] + (f" - {c['section']}" if c.get("section") else "")
        blocks.append(f"[{i}] ({where})\n{c['text']}")
    return "\n\n".join(blocks)


def build_graph(deps: PipelineDeps) -> Any:
    s = deps.settings

    async def input_guard(state: ChatState) -> dict[str, Any]:
        question = state["question"].strip()[:4000]
        updates: dict[str, Any] = {"guardrails": []}
        redacted = await asyncio.to_thread(deps.pii.redact_query, question)
        if redacted.redacted:
            updates["guardrails"].append(_guard("pii_in_query", "redacted", redacted.entities))
        updates["sanitized_question"] = redacted.text
        updates["query"] = redacted.text

        verdict = await deps.injection.check(question)
        if verdict.blocked:
            updates["guardrails"].append(
                _guard(
                    "prompt_injection",
                    "blocked",
                    {"reasons": verdict.reasons, "score": verdict.score},
                )
            )
            updates.update(status="blocked", answer=prompts.BLOCKED_ANSWER)
        return updates

    async def analyze(state: ChatState) -> dict[str, Any]:
        role = _role(state)
        history = _history_messages(state, s.max_history_turns)
        transcript = "\n".join(
            f"{'assistant' if isinstance(m, AIMessage) else 'user'}: {m.content}" for m in history
        )
        messages = [
            SystemMessage(content=prompts.ANALYZER_SYSTEM),
            HumanMessage(
                content=f"Conversation so far:\n{transcript or '(none)'}\n\nLatest message:\n{state['sanitized_question']}"
            ),
        ]
        try:
            result: QueryAnalysis = await deps.analyzer.ainvoke(
                messages, config={"run_name": "analyze_query"}
            )
            intent, query = (
                result.intent,
                result.standalone_question.strip() or state["sanitized_question"],
            )
        except Exception as exc:
            log.warning(
                "Query analyzer failed (%s); defaulting to document search", type(exc).__name__
            )
            intent, query = "company_question", state["sanitized_question"]

        updates: dict[str, Any] = {"intent": intent, "query": query, "guardrails": []}
        if intent == "smalltalk":
            updates.update(
                status="smalltalk", answer=prompts.smalltalk_answer(state["user"]["name"], role)
            )
        elif intent == "out_of_scope":
            # Second opinion from the corpus: a strong match means the classifier was wrong.
            probe = await asyncio.to_thread(deps.retriever.search, query, role)
            best = probe.top_similarity
            if best >= s.relevance_threshold + 0.08:
                updates["intent"] = "company_question"
                updates["guardrails"].append(
                    _guard("scope", "overridden_by_similarity", round(best, 3))
                )
            else:
                updates["guardrails"].append(_guard("out_of_scope", "refused", round(best, 3)))
                updates.update(status="out_of_scope", answer=prompts.out_of_scope_answer(role))
        return updates

    async def retrieve(state: ChatState) -> dict[str, Any]:
        role = _role(state)
        res = await asyncio.to_thread(deps.retriever.search, state["query"], role)
        updates: dict[str, Any] = {
            "top_similarity": res.top_similarity,
            "restricted_departments": res.restricted_departments,
            "guardrails": [],
        }
        # Embedding similarity alone misfires (a finance "Q4 revenue" question sits nearer the
        # marketing Q4 report), so the cross-encoder decides when it is available.
        if res.best_rerank_score is not None and res.restricted_rerank_score is not None:
            restricted_lead = res.restricted_rerank_score - res.best_rerank_score
            restricted_wins = restricted_lead >= s.restricted_rerank_margin
        else:
            restricted_wins = res.restricted_similarity - res.top_similarity >= s.restricted_margin
        restricted_wins = restricted_wins and bool(res.restricted_departments)
        if res.top_similarity < s.relevance_threshold or restricted_wins:
            if res.restricted_departments:
                updates["guardrails"].append(_guard("rbac", "denied", res.restricted_departments))
                updates.update(
                    status="access_denied",
                    answer=prompts.access_denied_answer(role, res.restricted_departments),
                )
            else:
                updates.update(status="no_context", answer=prompts.no_context_answer())
            return updates

        contexts = [c.to_dict() for c in res.chunks]
        if role not in PII_PRIVILEGED_ROLES:
            # Defense in depth: mask employee PII before it ever reaches the LLM.
            masked: set[str] = set()
            for c in contexts:
                r = deps.pii.redact(c["text"], role)
                c["text"] = r.text
                masked.update(r.entities)
            if masked:
                updates["guardrails"].append(_guard("pii_context", "redacted", sorted(masked)))
        updates["contexts"] = contexts
        return updates

    async def hr_analytics(state: ChatState) -> dict[str, Any]:
        role = _role(state)
        if role not in HR_ANALYTICS_ROLES or not can_access(role, "hr"):
            return {
                "status": "access_denied",
                "answer": prompts.access_denied_answer(role, ["hr"]),
                "guardrails": [_guard("rbac", "denied", ["hr"])],
            }
        if deps.hr_store is None:
            return await retrieve(state)

        store = deps.hr_store
        messages: list[BaseMessage] = [
            SystemMessage(content=prompts.SQL_SYSTEM.format(schema=store.schema)),
            HumanMessage(content=state["query"]),
        ]
        last_error = ""
        for attempt in range(2):
            try:
                if last_error:
                    messages.append(
                        HumanMessage(content=f"That query failed: {last_error}. Fix it.")
                    )
                out: SQLQuery = await deps.sql_writer.ainvoke(
                    messages, config={"run_name": "write_sql"}
                )
                result = await asyncio.to_thread(store.query, out.sql)
                context = {
                    "id": "hr-sql",
                    "text": f"SQL query:\n{out.sql}\n\nResult ({len(result.rows)} rows):\n{result.as_markdown()}",
                    "source": "hr/hr_data.csv (DuckDB)",
                    "title": "HR analytics query",
                    "section": "",
                    "department": "hr",
                    "doc_type": "sql_result",
                    "score": 1.0,
                }
                return {
                    "sql": out.sql,
                    "contexts": [context],
                    "top_similarity": 1.0,
                    "guardrails": [_guard("sql_guard", "validated", "single read-only SELECT")],
                }
            except UnsafeSQLError as exc:
                last_error = str(exc)
                log.warning("Rejected generated SQL (attempt %d): %s", attempt + 1, exc)
            except Exception as exc:
                last_error = str(exc)[:300]
                log.warning("HR SQL failed (attempt %d): %s", attempt + 1, last_error)
        # Fall back to semantic search over employee records.
        fallback = await retrieve(state)
        fallback.setdefault("guardrails", []).append(
            _guard("sql_guard", "fallback_to_search", last_error)
        )
        return fallback

    async def generate(state: ChatState) -> dict[str, Any]:
        role = _role(state)
        user = state["user"]
        system = prompts.ANSWER_SYSTEM.format(
            company=prompts.COMPANY,
            name=user["name"],
            title=user.get("title", ""),
            role=role.value,
            access=prompts.ROLE_DESCRIPTIONS[role],
            context=_format_context(state.get("contexts") or []),
        )
        messages = [
            SystemMessage(content=system),
            *_history_messages(state, s.max_history_turns),
            HumanMessage(content=state["sanitized_question"]),
        ]
        writer = get_stream_writer()
        redactor = StreamingRedactor(deps.pii, role)
        parts: list[str] = []
        try:
            async for chunk in deps.generator.astream(
                messages, config={"run_name": "generate_answer"}
            ):
                safe = _normalize_citations(redactor.push(_chunk_text(chunk)))
                if safe:
                    parts.append(safe)
                    writer({"type": "token", "text": safe})
            tail = _normalize_citations(redactor.flush())
            if tail:
                parts.append(tail)
                writer({"type": "token", "text": tail})
        except Exception:
            log.exception("Generation failed")
            return {"status": "error", "answer": prompts.ERROR_ANSWER, "guardrails": []}

        guards = []
        if redactor.entities:
            guards.append(_guard("pii_output", "redacted", sorted(redactor.entities)))
        return {"status": "answered", "answer": "".join(parts).strip(), "guardrails": guards}

    async def finalize(state: ChatState) -> dict[str, Any]:
        contexts = state.get("contexts") or []
        if state.get("status") != "answered" or not contexts:
            return {"sources": [], "guardrails": []}
        answer = state.get("answer", "")
        cited = sorted(
            {int(n) for n in re.findall(r"\[(\d{1,2})\]", answer) if 1 <= int(n) <= len(contexts)}
        )
        guards = []
        if cited:
            sources = [{**contexts[i - 1], "ref": i} for i in cited]
        else:
            sources = [{**c, "ref": i} for i, c in enumerate(contexts[:3], 1)]
            guards.append(_guard("grounding", "no_citations", None))
        return {"sources": sources, "guardrails": guards}

    def after_guard(state: ChatState) -> str:
        return "finalize" if state.get("status") else "analyze"

    def after_analyze(state: ChatState) -> str:
        if state.get("status"):
            return "finalize"
        return "hr_analytics" if state.get("intent") == "hr_analytics" else "retrieve"

    def after_retrieval(state: ChatState) -> str:
        return "finalize" if state.get("status") else "generate"

    g = StateGraph(ChatState)
    g.add_node("input_guard", input_guard)
    g.add_node("analyze", analyze)
    g.add_node("retrieve", retrieve)
    g.add_node("hr_analytics", hr_analytics)
    g.add_node("generate", generate)
    g.add_node("finalize", finalize)
    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", after_guard, ["analyze", "finalize"])
    g.add_conditional_edges("analyze", after_analyze, ["retrieve", "hr_analytics", "finalize"])
    g.add_conditional_edges("retrieve", after_retrieval, ["generate", "finalize"])
    g.add_conditional_edges("hr_analytics", after_retrieval, ["generate", "finalize"])
    g.add_edge("generate", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def default_deps(settings: Settings | None = None) -> PipelineDeps:
    s = settings or get_settings()
    classifier = classifier_model(s)
    try:
        hr_store: HRStore | None = get_hr_store()
    except Exception:
        log.exception("HR store unavailable; HR analytics will fall back to search")
        hr_store = None
    return PipelineDeps(
        settings=s,
        retriever=get_retriever(),
        pii=get_pii_guard(),
        injection=InjectionGuard(prompt_guard_model(s), s.prompt_guard_threshold),
        # Groq's forced tool calling is flaky with gpt-oss ("model did not call a tool");
        # constrained JSON-schema decoding is not.
        analyzer=classifier.with_structured_output(QueryAnalysis, method="json_schema"),
        generator=generator_model(s),
        sql_writer=chat_model(s.llm_model, temperature=0.0, settings=s).with_structured_output(
            SQLQuery, method="json_schema"
        ),
        hr_store=hr_store,
    )


@lru_cache
def get_graph() -> Any:
    return build_graph(default_deps())
