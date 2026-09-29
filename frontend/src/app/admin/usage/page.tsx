"use client";

import { Activity, Coins, RefreshCw, ShieldAlert, Sigma } from "lucide-react";
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from "recharts";
import { toast } from "sonner";
import { AppShell } from "@/components/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError, type UsageSummary } from "@/lib/api";
import { ROLE_META, STATUS_META } from "@/lib/roles";
import type { Session } from "@/lib/session";
import { cn } from "@/lib/utils";

const RANGES = [1, 7, 30];
const usd = (n: number, digits = 4) => `$${n.toFixed(digits)}`;
const int = (n: number) => Math.round(n).toLocaleString();

function Kpi({ icon: Icon, label, value, hint }: { icon: typeof Coins; label: string; value: string; hint?: string }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription className="flex items-center gap-1.5">
          <Icon className="size-4" /> {label}
        </CardDescription>
        <CardTitle className="text-2xl tabular-nums">{value}</CardTitle>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </CardHeader>
    </Card>
  );
}

function Dashboard({ session }: { session: Session }) {
  const [days, setDays] = useState(7);
  const [data, setData] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .usageSummary(session.token, days)
      .then((summary) => {
        if (cancelled) return;
        setData(summary);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof ApiError ? err.message : "Could not load usage";
        setError(msg);
        toast.error(msg);
      });
    return () => {
      cancelled = true;
    };
  }, [session.token, days, reloadKey]);

  if (session.user.role !== "c_level") {
    return (
      <div className="mx-auto max-w-md py-24 text-center text-sm text-muted-foreground">
        The usage dashboard is only available to C-Level users.
      </div>
    );
  }

  const t = data?.totals;
  const totalTokens = t ? t.input_tokens + t.output_tokens : 0;
  const byRole = (data?.by_role ?? []).map((r) => ({
    ...r,
    label: ROLE_META[r.role]?.label ?? r.role,
    tokens: r.input_tokens + r.output_tokens,
  }));

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl space-y-6 p-6">
        <div className="flex flex-wrap items-center gap-3">
          <div>
            <h1 className="text-xl font-semibold">Usage & cost</h1>
            <p className="text-sm text-muted-foreground">
              Token spend per request, priced from the model price list. Daily quota per user:{" "}
              {data ? int(data.daily_token_quota) : "-"} tokens.
            </p>
          </div>
          <div className="ml-auto flex items-center gap-1 rounded-lg border bg-background p-1">
            {RANGES.map((r) => (
              <Button key={r} size="sm" variant={r === days ? "secondary" : "ghost"} onClick={() => setDays(r)}>
                {r === 1 ? "Today" : `${r}d`}
              </Button>
            ))}
            <Button size="icon-sm" variant="ghost" onClick={() => setReloadKey((k) => k + 1)} aria-label="Refresh">
              <RefreshCw />
            </Button>
          </div>
        </div>

        {!data && !error && (
          <div className="grid gap-4 md:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-24" />
            ))}
          </div>
        )}

        {data && t && (
          <>
            <div className="grid gap-4 md:grid-cols-4">
              <Kpi icon={Coins} label="Cost" value={usd(t.cost_usd)} hint={`${days} day window`} />
              <Kpi
                icon={Activity}
                label="Requests"
                value={int(t.requests)}
                hint={t.requests ? `${usd(t.cost_usd / t.requests, 5)} avg / request` : undefined}
              />
              <Kpi
                icon={Sigma}
                label="Tokens"
                value={int(totalTokens)}
                hint={`${int(t.input_tokens)} in · ${int(t.output_tokens)} out`}
              />
              <Kpi
                icon={ShieldAlert}
                label="Guardrail refusals"
                value={int(t.blocked ?? 0)}
                hint={`${int(t.errors ?? 0)} errors`}
              />
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Cost by day (USD)</CardTitle>
                </CardHeader>
                <CardContent className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={data.by_day}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="date" fontSize={11} tickLine={false} />
                      <YAxis fontSize={11} tickLine={false} width={64} tickFormatter={(v: number) => usd(v)} />
                      <ChartTooltip formatter={(v) => usd(Number(v), 5)} />
                      <Bar dataKey="cost_usd" name="Cost" fill="#10b981" radius={[4, 4, 0, 0]} maxBarSize={48} />
                    </BarChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Tokens by role</CardTitle>
                </CardHeader>
                <CardContent className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={byRole}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="label" fontSize={11} tickLine={false} />
                      <YAxis fontSize={11} tickLine={false} width={56} />
                      <ChartTooltip formatter={(v) => int(Number(v))} />
                      <Bar dataKey="input_tokens" name="Input" stackId="t" fill="#6366f1" maxBarSize={48} />
                      <Bar
                        dataKey="output_tokens"
                        name="Output"
                        stackId="t"
                        fill="#a5b4fc"
                        radius={[4, 4, 0, 0]}
                        maxBarSize={48}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Top users</CardTitle>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>User</TableHead>
                        <TableHead className="text-right">Requests</TableHead>
                        <TableHead className="text-right">Tokens</TableHead>
                        <TableHead className="text-right">Cost</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.by_user.map((u) => (
                        <TableRow key={u.username}>
                          <TableCell>
                            <span className="mr-2">{u.username}</span>
                            <Badge className={ROLE_META[u.role]?.badge}>{ROLE_META[u.role]?.label ?? u.role}</Badge>
                          </TableCell>
                          <TableCell className="text-right tabular-nums">{int(u.requests)}</TableCell>
                          <TableCell className="text-right tabular-nums">{int(u.input_tokens + u.output_tokens)}</TableCell>
                          <TableCell className="text-right tabular-nums">{usd(u.cost_usd)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">By model</CardTitle>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Model</TableHead>
                        <TableHead className="text-right">Calls</TableHead>
                        <TableHead className="text-right">Tokens</TableHead>
                        <TableHead className="text-right">Cost</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.by_model.map((m) => (
                        <TableRow key={m.model}>
                          <TableCell className="font-mono text-xs">{m.model}</TableCell>
                          <TableCell className="text-right tabular-nums">{int(m.requests)}</TableCell>
                          <TableCell className="text-right tabular-nums">{int(m.input_tokens + m.output_tokens)}</TableCell>
                          <TableCell className="text-right tabular-nums">{usd(m.cost_usd)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </div>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Recent requests (today)</CardTitle>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Time</TableHead>
                      <TableHead>User</TableHead>
                      <TableHead>Outcome</TableHead>
                      <TableHead>Guardrails</TableHead>
                      <TableHead className="text-right">Latency</TableHead>
                      <TableHead className="text-right">Tokens</TableHead>
                      <TableHead className="text-right">Cost</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.recent.map((r) => {
                      const meta = STATUS_META[r.status];
                      return (
                        <TableRow key={r.request_id}>
                          <TableCell className="tabular-nums">{new Date(r.timestamp).toLocaleTimeString()}</TableCell>
                          <TableCell>{r.username}</TableCell>
                          <TableCell>
                            <Badge variant="outline" className={cn(meta?.tone)}>
                              {meta?.label ?? r.status}
                            </Badge>
                          </TableCell>
                          <TableCell className="max-w-48 truncate text-xs text-muted-foreground">
                            {r.guardrails.join(", ") || "-"}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">{(r.latency_ms / 1000).toFixed(1)}s</TableCell>
                          <TableCell className="text-right tabular-nums">{int(r.input_tokens + r.output_tokens)}</TableCell>
                          <TableCell className="text-right tabular-nums">{usd(r.cost_usd, 5)}</TableCell>
                        </TableRow>
                      );
                    })}
                    {data.recent.length === 0 && (
                      <TableRow>
                        <TableCell colSpan={7} className="text-center text-muted-foreground">
                          No requests yet today.
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}

export default function UsagePage() {
  return <AppShell>{(session) => <Dashboard session={session} />}</AppShell>;
}
