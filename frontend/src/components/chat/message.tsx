"use client";

import { ChevronDown, Database, FileText, ShieldAlert, ShieldCheck, User as UserIcon } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { ChatResult, Guardrail, Source } from "@/lib/api";
import { DEPARTMENT_LABEL, GUARDRAIL_LABEL, STATUS_META } from "@/lib/roles";
import { cn } from "@/lib/utils";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
  result?: ChatResult;
}

function withCitationLinks(text: string): string {
  return text.replace(/\[(\d{1,2})\](?!\()/g, "[$1](#cite-$1)");
}

function guardrailDetail(g: Guardrail): string {
  if (g.detail == null) return g.action;
  if (Array.isArray(g.detail)) return `${g.action}: ${g.detail.join(", ")}`;
  if (typeof g.detail === "object") {
    const reasons = (g.detail as { reasons?: string[] }).reasons;
    if (reasons) return `${g.action}: ${reasons.join(", ")}`;
    return `${g.action}: ${JSON.stringify(g.detail)}`;
  }
  return `${g.action}: ${String(g.detail)}`;
}

function GuardrailBadges({ guardrails }: { guardrails: Guardrail[] }) {
  if (!guardrails.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {guardrails.map((g, i) => {
        const blocking = ["blocked", "refused", "denied"].includes(g.action);
        return (
          <Tooltip key={`${g.name}-${i}`}>
            <TooltipTrigger
              render={
                <Badge
                  variant="outline"
                  className={cn(
                    "cursor-default gap-1",
                    blocking ? "border-red-300 text-red-700 dark:text-red-300" : "border-emerald-300 text-emerald-700 dark:text-emerald-300",
                  )}
                />
              }
            >
              {blocking ? <ShieldAlert /> : <ShieldCheck />}
              {GUARDRAIL_LABEL[g.name] ?? g.name}
            </TooltipTrigger>
            <TooltipContent>{guardrailDetail(g)}</TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}

function SourceCard({ source }: { source: Source }) {
  const [open, setOpen] = useState(false);
  const isSql = source.doc_type === "sql_result";
  return (
    <div id={`cite-${source.ref}`} className="rounded-lg border bg-background text-xs">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span className="flex size-5 shrink-0 items-center justify-center rounded bg-muted font-mono text-[10px]">
          {source.ref}
        </span>
        {isSql ? <Database className="size-3.5 shrink-0" /> : <FileText className="size-3.5 shrink-0" />}
        <span className="truncate font-medium">{source.source}</span>
        {source.section && (
          <span className="hidden truncate text-muted-foreground md:inline">
            {source.section.split(" > ").slice(-1)[0]}
          </span>
        )}
        <Badge variant="secondary" className="ml-auto shrink-0">
          {DEPARTMENT_LABEL[source.department] ?? source.department}
        </Badge>
        <ChevronDown className={cn("size-3.5 shrink-0 transition", open && "rotate-180")} />
      </button>
      {open && (
        <pre className="max-h-64 overflow-auto border-t px-3 py-2 font-mono text-[11px] whitespace-pre-wrap text-muted-foreground">
          {source.text}
        </pre>
      )}
    </div>
  );
}

export function MessageView({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end gap-3">
        <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground">
          {message.content}
        </div>
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted">
          <UserIcon className="size-4" />
        </div>
      </div>
    );
  }

  const result = message.result;
  const status = result ? STATUS_META[result.status] : null;
  return (
    <div className="flex gap-3">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-white">
        <ShieldCheck className="size-4" />
      </div>
      <div className="min-w-0 max-w-[85%] flex-1 space-y-3">
        <div className={cn("rounded-2xl rounded-tl-sm border bg-background px-4 py-3 text-sm", status?.tone)}>
          {status && <div className="mb-1 text-xs font-semibold uppercase tracking-wide">{status.label}</div>}
          {message.content ? (
            <div className="prose prose-sm max-w-none dark:prose-invert prose-p:my-1.5 prose-ul:my-1.5 prose-table:text-xs">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  a: ({ href, children }) =>
                    href?.startsWith("#cite-") ? (
                      <a
                        href={href}
                        className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-muted px-1 align-super font-mono text-[10px] no-underline"
                      >
                        {children}
                      </a>
                    ) : (
                      <a href={href} target="_blank" rel="noreferrer">
                        {children}
                      </a>
                    ),
                }}
              >
                {withCitationLinks(message.content)}
              </ReactMarkdown>
            </div>
          ) : (
            <div className="flex gap-1 py-1">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60"
                  style={{ animationDelay: `${i * 120}ms` }}
                />
              ))}
            </div>
          )}
        </div>

        {result && (
          <>
            <GuardrailBadges guardrails={result.guardrails} />
            {result.sql && (
              <details className="rounded-lg border bg-background text-xs">
                <summary className="cursor-pointer px-3 py-2 font-medium">Generated SQL (read-only)</summary>
                <pre className="overflow-auto border-t px-3 py-2 font-mono text-[11px]">{result.sql}</pre>
              </details>
            )}
            {result.sources.length > 0 && (
              <div className="space-y-1.5">
                {result.sources.map((s) => (
                  <SourceCard key={`${s.id}-${s.ref}`} source={s} />
                ))}
              </div>
            )}
            <div className="flex flex-wrap gap-x-3 text-[11px] text-muted-foreground">
              <span>{(result.latency_ms / 1000).toFixed(1)}s</span>
              <span>{result.usage.total_tokens.toLocaleString()} tokens</span>
              <span>${result.usage.cost_usd.toFixed(5)}</span>
              <span className="font-mono">#{result.request_id}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
