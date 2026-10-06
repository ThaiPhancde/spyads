// Realtime (SSE) pass-through. Deliberately NOT served by the next.config rewrite proxy: that proxy has a
// small per-host socket pool, so a few open tabs (each holding one endless SSE stream) starved every other
// /api/* call. This handler streams through fetch's own unlimited pool and closes upstream when the tab goes.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const API = process.env.API_URL || "http://127.0.0.1:8000";

export async function GET(req: Request) {
  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/events/stream`, {
      headers: { accept: "text/event-stream" },
      signal: req.signal,
      cache: "no-store",
    });
  } catch {
    return new Response("event stream unavailable", { status: 502 });
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
