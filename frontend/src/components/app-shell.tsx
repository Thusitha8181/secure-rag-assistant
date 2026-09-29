"use client";

import { BarChart3, LogOut, MessageSquare, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Session } from "@/lib/session";
import { clearSession, useHydrated, useSession } from "@/lib/session";
import { ROLE_META } from "@/lib/roles";
import { cn } from "@/lib/utils";

export function useRequireSession(): Session | null {
  const router = useRouter();
  const hydrated = useHydrated();
  const session = useSession();
  useEffect(() => {
    if (hydrated && !session) router.replace("/login");
  }, [hydrated, session, router]);
  return session;
}

export function logout() {
  clearSession();
}

export function AppShell({ children }: { children: (session: Session) => ReactNode }) {
  const session = useRequireSession();
  const pathname = usePathname();
  if (!session) return null;
  const { user } = session;
  const nav = [
    { href: "/chat", label: "Chat", icon: MessageSquare, show: true },
    { href: "/admin/usage", label: "Usage & cost", icon: BarChart3, show: user.role === "c_level" },
  ];

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex h-14 shrink-0 items-center gap-4 border-b bg-background px-4">
        <Link href="/chat" className="flex items-center gap-2 font-semibold">
          <ShieldCheck className="size-5 text-emerald-600" />
          <span className="hidden sm:inline">FinSolve Secure Assistant</span>
        </Link>
        <nav className="flex items-center gap-1">
          {nav
            .filter((n) => n.show)
            .map((n) => (
              <Link
                key={n.href}
                href={n.href}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground",
                  pathname.startsWith(n.href) && "bg-muted text-foreground",
                )}
              >
                <n.icon className="size-4" /> {n.label}
              </Link>
            ))}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <div className="hidden text-right leading-tight md:block">
            <div className="text-sm font-medium">{user.name}</div>
            <div className="text-xs text-muted-foreground">{user.title}</div>
          </div>
          <Badge className={ROLE_META[user.role].badge}>{ROLE_META[user.role].label}</Badge>
          <Button variant="ghost" size="icon-sm" onClick={logout} aria-label="Sign out" title="Sign out">
            <LogOut />
          </Button>
        </div>
      </header>
      <div className="min-h-0 flex-1">{children(session)}</div>
    </div>
  );
}
