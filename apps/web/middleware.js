import { NextResponse } from "next/server";

// Optional Basic Auth for the demo deployment: set BASIC_AUTH_USER / BASIC_AUTH_PASS.
// Ingest & webhook endpoints are excluded — they use X-Ingest-Token instead.
// /api/* is proxied to the backend (next.config rewrites); the backend wants X-Admin-Token on every write,
// so it is attached here server-side and the browser never sees it.
export function middleware(req) {
  const p = req.nextUrl.pathname;
  const headers = new Headers(req.headers);
  if (p.startsWith("/api/") && process.env.ADMIN_TOKEN) headers.set("x-admin-token", process.env.ADMIN_TOKEN);
  const next = () => NextResponse.next({ request: { headers } });
  const user = process.env.BASIC_AUTH_USER;
  const pass = process.env.BASIC_AUTH_PASS;
  if (!user || !pass) return next();
  if (p.startsWith("/api/ingest/") || p.startsWith("/api/webhooks/") || p === "/api/health") return next();
  const h = req.headers.get("authorization") || "";
  if (h.startsWith("Basic ")) {
    const [u, ...rest] = atob(h.slice(6)).split(":");
    if (u === user && rest.join(":") === pass) return next();
  }
  return new NextResponse("Authentication required", { status: 401, headers: { "WWW-Authenticate": 'Basic realm="Market Intelligence OS"' } });
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
