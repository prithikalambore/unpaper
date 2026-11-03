/**
 * Same-origin proxy to the FastAPI backend.
 *
 * The browser only ever talks to this Next.js server, so the backend can stay on a
 * private network and its URL is configured at runtime (BACKEND_URL) rather than
 * baked into the client bundle. Request and response bodies are streamed, never
 * buffered or logged here.
 */
import type { NextRequest } from "next/server";

const BACKEND_URL = (process.env.BACKEND_URL ?? "http://localhost:8000").replace(/\/+$/, "");
const SEGMENT = /^[A-Za-z0-9_-]{1,64}$/;
const FORWARD_REQUEST = ["content-type", "content-length", "accept"];
const FORWARD_RESPONSE = ["content-type", "content-disposition", "cache-control", "retry-after"];
const QUERY_KEYS = ["visit"];

/**
 * The visitor's IP, as set by the reverse proxy in front of this server (Caddy in
 * docker-compose.prod.yml overwrites X-Forwarded-For with the real client address).
 * The backend uses it only for rate limiting and counting unique visitors per day.
 */
function clientIp(request: NextRequest): string | null {
  const forwarded = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  const ip = forwarded || request.headers.get("x-real-ip")?.trim();
  return ip && /^[0-9a-fA-F:.]{2,45}$/.test(ip) ? ip : null;
}

function errorResponse(status: number, code: string, message: string) {
  return Response.json({ error: { code, message } }, { status, headers: { "Cache-Control": "no-store" } });
}

async function proxy(request: NextRequest, ctx: RouteContext<"/api/[...path]">) {
  const { path } = await ctx.params;
  if (!path.length || !path.every((s) => SEGMENT.test(s))) {
    return errorResponse(404, "not_found", "Not found.");
  }

  const headers = new Headers();
  for (const name of FORWARD_REQUEST) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const ip = clientIp(request);
  if (ip) headers.set("x-client-ip", ip);

  const query = new URLSearchParams();
  for (const key of QUERY_KEYS) {
    const value = request.nextUrl.searchParams.get(key);
    if (value && /^[0-9a-z]{1,8}$/i.test(value)) query.set(key, value);
  }
  const qs = query.size ? `?${query}` : "";

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/api/${path.join("/")}${qs}`, {
      method: request.method,
      headers,
      body: request.method === "POST" ? request.body : undefined,
      cache: "no-store",
      redirect: "manual",
      // Required by Node's fetch to stream a request body.
      duplex: "half",
    } as RequestInit & { duplex: "half" });
  } catch {
    return errorResponse(502, "backend_unavailable", "The conversion service is not reachable. Please try again in a moment.");
  }

  const out = new Headers({ "Cache-Control": "no-store" });
  for (const name of FORWARD_RESPONSE) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  const body = upstream.status === 204 || upstream.status === 304 ? null : upstream.body;
  return new Response(body, { status: upstream.status, headers: out });
}

export { proxy as GET, proxy as POST, proxy as DELETE };
