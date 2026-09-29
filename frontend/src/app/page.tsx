"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useHydrated, useSession } from "@/lib/session";

export default function Home() {
  const router = useRouter();
  const hydrated = useHydrated();
  const session = useSession();

  useEffect(() => {
    if (hydrated) router.replace(session ? "/chat" : "/login");
  }, [hydrated, session, router]);

  return null;
}
