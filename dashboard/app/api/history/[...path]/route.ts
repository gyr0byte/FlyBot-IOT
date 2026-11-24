// Proxy to the logger's read-only API (python -m flybot_brain.logger api), behind the dashboard login.
import { NextRequest, NextResponse } from "next/server";
import { env } from "../../../../lib/serverEnv";

export const dynamic = "force-dynamic";
const ALLOWED = new Set(["health", "sensors", "events", "latency", "trials", "timeline", "reliability"]);

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (path.length !== 1 || !ALLOWED.has(path[0])) return NextResponse.json({ error: "unknown" }, { status: 404 });
  const base = env("LOGGER_API_URL", "http://127.0.0.1:8000");
  try {
    const r = await fetch(`${base}/api/${path[0]}${req.nextUrl.search}`, { cache: "no-store" });
    return new NextResponse(await r.text(), { status: r.status, headers: { "Content-Type": "application/json" } });
  } catch {
    return NextResponse.json({ error: "logger API not reachable", base }, { status: 502 });
  }
}
