"use client";

import { Loader2, LockKeyhole, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, type User } from "@/lib/api";
import { ROLE_META } from "@/lib/roles";
import { saveSession } from "@/lib/session";
import { cn } from "@/lib/utils";

const DEMO_PASSWORD = "demo1234";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState<string | null>(null);
  const [demoUsers, setDemoUsers] = useState<User[] | null>(null);

  useEffect(() => {
    api
      .demoUsers()
      .then(setDemoUsers)
      .catch(() => setDemoUsers([]));
  }, []);

  async function signIn(user: string, pass: string) {
    setLoading(user);
    try {
      const { access_token, user: me } = await api.login(user, pass);
      saveSession({ token: access_token, user: me });
      router.replace("/chat");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not reach the server");
      setLoading(null);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void signIn(username, password);
  }

  return (
    <main className="mx-auto grid w-full max-w-5xl flex-1 items-center gap-10 px-6 py-12 lg:grid-cols-[1fr_1.1fr]">
      <section className="space-y-6">
        <div className="inline-flex items-center gap-2 rounded-full border bg-background px-3 py-1 text-xs text-muted-foreground">
          <ShieldCheck className="size-3.5 text-emerald-600" /> RBAC · Guardrails · Evals · Cost monitoring
        </div>
        <h1 className="text-4xl font-semibold tracking-tight">FinSolve Secure Assistant</h1>
        <p className="text-muted-foreground">
          An internal chatbot that answers questions from company documents - but only the documents your role is
          allowed to see. Access control is enforced inside the vector database, PII is redacted based on role,
          and prompt-injection and off-topic questions are blocked.
        </p>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <LockKeyhole className="size-4" /> Sign in
            </CardTitle>
            <CardDescription>Use a demo account on the right, or enter credentials.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="username">Username</Label>
                <Input
                  id="username"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="e.g. fiona.finance"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              <Button type="submit" className="w-full" disabled={!username || !password || loading !== null}>
                {loading === username && <Loader2 className="animate-spin" />} Sign in
              </Button>
            </form>
          </CardContent>
        </Card>
      </section>

      <section className="space-y-3">
        <div className="flex items-baseline justify-between">
          <h2 className="font-medium">Try a role</h2>
          <span className="text-xs text-muted-foreground">
            password: <code className="rounded bg-muted px-1">{DEMO_PASSWORD}</code>
          </span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {demoUsers === null &&
            [0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-32 rounded-xl" />)}
          {demoUsers?.map((u) => (
            <button
              key={u.username}
              type="button"
              onClick={() => signIn(u.username, DEMO_PASSWORD)}
              disabled={loading !== null}
              className={cn(
                "group rounded-xl border bg-background p-4 text-left transition hover:-translate-y-0.5 hover:shadow-md",
                "disabled:opacity-60",
              )}
            >
              <div className="flex items-center justify-between">
                <Badge className={ROLE_META[u.role].badge}>{ROLE_META[u.role].label}</Badge>
                {loading === u.username && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
              </div>
              <div className="mt-3 font-medium">{u.name}</div>
              <div className="text-xs text-muted-foreground">{u.title}</div>
              <p className="mt-2 line-clamp-2 text-xs text-muted-foreground">{u.access_summary}</p>
            </button>
          ))}
          {demoUsers?.length === 0 && (
            <p className="text-sm text-muted-foreground">Demo accounts are unavailable (is the backend running?).</p>
          )}
        </div>
      </section>
    </main>
  );
}
