"use client";

import { ArrowUp, Eraser, Lock, ShieldQuestion, Sparkles, Square } from "lucide-react";
import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { AppShell } from "@/components/app-shell";
import { type ChatMessage, MessageView } from "@/components/chat/message";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, type HistoryTurn, streamChat } from "@/lib/api";
import { DEPARTMENT_LABEL, guardrailDemos, ROLE_META, SUGGESTIONS } from "@/lib/roles";
import { clearSession, type Session } from "@/lib/session";

const ALL_DEPARTMENTS = ["finance", "marketing", "hr", "engineering", "general"];

function Sidebar({ session, onAsk }: { session: Session; onAsk: (q: string) => void }) {
  const { user } = session;
  return (
    <aside className="hidden w-72 shrink-0 flex-col gap-6 overflow-y-auto border-r bg-background p-4 lg:flex">
      <div>
        <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Your access</h3>
        <p className="mb-3 text-sm">{user.access_summary}</p>
        <div className="space-y-1.5">
          {ALL_DEPARTMENTS.map((d) => {
            const allowed = user.departments.includes(d);
            return (
              <div key={d} className="flex items-center justify-between text-sm">
                <span className={allowed ? "" : "text-muted-foreground line-through"}>{DEPARTMENT_LABEL[d]}</span>
                {allowed ? (
                  <span className={`size-2 rounded-full ${ROLE_META[user.role].dot}`} />
                ) : (
                  <Lock className="size-3.5 text-muted-foreground" />
                )}
              </div>
            );
          })}
        </div>
      </div>
      <div>
        <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          <Sparkles className="size-3.5" /> Try asking
        </h3>
        <div className="space-y-1.5">
          {SUGGESTIONS[user.role].map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => onAsk(q)}
              className="w-full rounded-lg border px-3 py-2 text-left text-xs hover:bg-muted"
            >
              {q}
            </button>
          ))}
        </div>
      </div>
      <div>
        <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          <ShieldQuestion className="size-3.5" /> Test the guardrails
        </h3>
        <div className="space-y-1.5">
          {guardrailDemos(user.role).map((d) => (
            <button
              key={d.label}
              type="button"
              onClick={() => onAsk(d.question)}
              className="w-full rounded-lg border border-dashed px-3 py-2 text-left text-xs hover:bg-muted"
            >
              <Badge variant="outline" className="mb-1">
                {d.label}
              </Badge>
              <div>{d.question}</div>
            </button>
          ))}
        </div>
      </div>
    </aside>
  );
}

function Chat({ session }: { session: Session }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  function update(id: string, fn: (m: ChatMessage) => ChatMessage) {
    setMessages((prev) => prev.map((m) => (m.id === id ? fn(m) : m)));
  }

  async function ask(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    const history: HistoryTurn[] = messages
      .filter((m) => !m.pending && m.content)
      .map((m) => ({ role: m.role, content: m.content }));
    const userMsg: ChatMessage = { id: crypto.randomUUID(), role: "user", content: q };
    const botId = crypto.randomUUID();
    setMessages((prev) => [...prev, userMsg, { id: botId, role: "assistant", content: "", pending: true }]);
    setInput("");
    setBusy(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamChat(
        session.token,
        q,
        history,
        {
          onToken: (text) => update(botId, (m) => ({ ...m, content: m.content + text })),
          onDone: (result) => update(botId, (m) => ({ ...m, content: result.answer, result, pending: false })),
        },
        controller.signal,
      );
    } catch (err) {
      if (controller.signal.aborted) {
        update(botId, (m) => ({ ...m, pending: false, content: m.content || "_Stopped._" }));
      } else if (err instanceof ApiError && err.status === 401) {
        toast.error("Your session expired. Please sign in again.");
        clearSession();
      } else {
        const msg = err instanceof ApiError ? err.message : "Could not reach the assistant.";
        toast.error(msg);
        setMessages((prev) => prev.filter((m) => m.id !== botId));
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void ask(input);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void ask(input);
    }
  }

  return (
    <div className="flex h-full">
      <Sidebar session={session} onAsk={ask} />
      <main className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
            {messages.length === 0 && (
              <div className="py-16 text-center">
                <h2 className="text-2xl font-semibold">Hi {session.user.name.split(" ")[0]}, what do you need?</h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  Answers come only from documents your <b>{ROLE_META[session.user.role].label}</b> role can access,
                  with sources.
                </p>
                <div className="mt-6 flex flex-wrap justify-center gap-2">
                  {SUGGESTIONS[session.user.role].map((q) => (
                    <button
                      key={q}
                      type="button"
                      onClick={() => ask(q)}
                      className="rounded-full border bg-background px-3 py-1.5 text-xs hover:bg-muted"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m) => (
              <MessageView key={m.id} message={m} />
            ))}
            <div ref={bottomRef} />
          </div>
        </div>
        <div className="border-t bg-background p-3">
          <form onSubmit={onSubmit} className="mx-auto flex max-w-3xl items-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => setMessages([])}
              disabled={busy || messages.length === 0}
              title="Clear conversation"
              aria-label="Clear conversation"
            >
              <Eraser />
            </Button>
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask about company data you have access to..."
              rows={1}
              maxLength={4000}
              className="max-h-40 min-h-10 resize-none"
            />
            {busy ? (
              <Button type="button" size="icon" variant="secondary" onClick={() => abortRef.current?.abort()} aria-label="Stop">
                <Square />
              </Button>
            ) : (
              <Button type="submit" size="icon" disabled={!input.trim()} aria-label="Send">
                <ArrowUp />
              </Button>
            )}
          </form>
          <p className="mx-auto mt-1.5 max-w-3xl text-center text-[11px] text-muted-foreground">
            Access is enforced server-side from your signed token. Sensitive data is redacted based on your role.
          </p>
        </div>
      </main>
    </div>
  );
}

export default function ChatPage() {
  return <AppShell>{(session) => <Chat session={session} />}</AppShell>;
}
