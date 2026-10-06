import { NextResponse } from "next/server";

// Optional Basic Auth for the demo deployment: set BASIC_AUTH_USER / BASIC_AUTH_PASS.
// Ingest & webhook endpoints are excluded — they use X-Ingest-Token instead.
export function middleware(req) {
  const user = process.env.BASIC_AUTH_USER;
  const pass = process.env.BASIC_AUTH_PASS;
  if (!user || !pass) return NextResponse.next();
  const p = req.nextUrl.pathname;
  if (p.startsWith("/api/ingest/") || p.startsWith("/api/webhooks/") || p === "/api/health") return NextResponse.next();
  const h = req.headers.get("authorization") || "";
  if (h.startsWith("Basic ")) {
    const [u, ...rest] = atob(h.slice(6)).split(":");
    if (u === user && rest.join(":") === pass) return NextResponse.next();
  }
  return new NextResponse("Authentication required", { status: 401, headers: { "WWW-Authenticate": 'Basic realm="Market Intelligence OS"' } });
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
