"use client";

import { useSyncExternalStore } from "react";
import type { User } from "./api";

export interface Session {
  token: string;
  user: User;
}

const KEY = "sra_session";
const listeners = new Set<() => void>();
let cachedRaw: string | null | undefined;
let cachedSession: Session | null = null;

function read(): Session | null {
  const raw = window.localStorage.getItem(KEY);
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    try {
      cachedSession = raw ? (JSON.parse(raw) as Session) : null;
    } catch {
      cachedSession = null;
    }
  }
  return cachedSession;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => e.key === KEY && listener();
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function saveSession(session: Session) {
  window.localStorage.setItem(KEY, JSON.stringify(session));
  listeners.forEach((l) => l());
}

export function clearSession() {
  window.localStorage.removeItem(KEY);
  listeners.forEach((l) => l());
}

export function useSession(): Session | null {
  return useSyncExternalStore(subscribe, read, () => null);
}

const noopSubscribe = () => () => {};

/** False during SSR / hydration, true once running in the browser. */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );
}
