import type { NextRequest } from "next/server";

// Local/dev proxy to the FastAPI backend. In AWS the ALB routes /api/* straight to the
// backend service, so this handler is only hit when frontend and backend run separately.
// Resolved per request so BACKEND_URL can be set at container runtime.
const FORWARDED_HEADERS = ["authorization", "content-type", "accept"];

async function proxy(req: NextRequest, ctx: RouteContext<"/api/[...path]">) {
  const { path } = await ctx.params;
  const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
  const target = `${backend}/api/${path.join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  for (const name of FORWARDED_HEADERS) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.text(),
      cache: "no-store",
    });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }

  const out = new Headers();
  for (const name of ["content-type", "cache-control", "x-accel-buffering"]) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export const GET = proxy;
export const POST = proxy;
