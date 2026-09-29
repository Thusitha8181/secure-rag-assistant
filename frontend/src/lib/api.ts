export type Role = "finance" | "marketing" | "hr" | "engineering" | "c_level" | "employee";

export interface User {
  username: string;
  name: string;
  title: string;
  role: Role;
  departments: string[];
  access_summary: string;
}

export interface Source {
  id: string;
  ref: number;
  text: string;
  source: string;
  title: string;
  section: string;
  department: string;
  doc_type: string;
  score: number;
}

export interface Guardrail {
  name: string;
  action: string;
  detail: unknown;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  by_model: Record<string, { input_tokens: number; output_tokens: number; cost_usd: number }>;
}

export type ChatStatus =
  | "answered"
  | "blocked"
  | "out_of_scope"
  | "access_denied"
  | "no_context"
  | "smalltalk"
  | "error";

export interface ChatResult {
  request_id: string;
  status: ChatStatus;
  answer: string;
  sources: Source[];
  guardrails: Guardrail[];
  usage: Usage;
  latency_ms: number;
  intent?: string | null;
  sql?: string | null;
}

export interface HistoryTurn {
  role: "user" | "assistant";
  content: string;
}

export interface UsageRow {
  requests: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  blocked?: number;
  errors?: number;
}

export interface UsageSummary {
  days: number;
  totals: UsageRow;
  by_day: (UsageRow & { date: string })[];
  by_user: (UsageRow & { username: string; role: Role })[];
  by_role: (UsageRow & { role: Role })[];
  by_model: (UsageRow & { model: string })[];
  recent: {
    request_id: string;
    timestamp: string;
    username: string;
    role: Role;
    status: ChatStatus;
    latency_ms: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    models: string[];
    guardrails: string[];
  }[];
  daily_token_quota: number;
}

export interface MyUsage {
  today: UsageRow & { total_tokens: number };
  daily_token_quota: number;
  remaining_tokens: number | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit & { token?: string | null } = {}): Promise<T> {
  const { token, headers, ...rest } = init;
  const res = await fetch(path, {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; user: User }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  demoUsers: () => request<User[]>("/api/auth/demo-users"),
  me: (token: string) => request<User>("/api/auth/me", { token }),
  myUsage: (token: string) => request<MyUsage>("/api/usage/me", { token }),
  usageSummary: (token: string, days: number) =>
    request<UsageSummary>(`/api/usage/summary?days=${days}`, { token }),
};

export interface StreamHandlers {
  onToken: (text: string) => void;
  onDone: (result: ChatResult) => void;
}

/** POST /api/chat/stream and parse the Server-Sent Events it returns. */
export async function streamChat(
  token: string,
  question: string,
  history: HistoryTurn[],
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ question, history }),
    signal,
  });
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : "Request failed");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (event === "token") handlers.onToken(payload.text);
      else if (event === "done") handlers.onDone(payload as ChatResult);
    }
  }
}
