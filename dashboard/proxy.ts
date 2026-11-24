// Everything except the login page and its API needs a valid session (simple password login, plan step 9.1).
import { NextRequest, NextResponse } from "next/server";
import { COOKIE, validSession } from "./lib/session";

export async function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (pathname === "/login" || pathname === "/api/login") return NextResponse.next();
  if (await validSession(req.cookies.get(COOKIE)?.value)) return NextResponse.next();
  if (pathname.startsWith("/api/")) return NextResponse.json({ error: "login required" }, { status: 401 });
  // Absolute URL on the host the browser used (the Host header, or X-Forwarded-Host behind a reverse
  // proxy): req.nextUrl / req.url resolve to "localhost" and would lose the session cookie.
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? req.nextUrl.host;
  const proto = req.headers.get("x-forwarded-proto") ?? req.nextUrl.protocol.replace(":", "");
  return NextResponse.redirect(`${proto}://${host}/login`, 307);
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
