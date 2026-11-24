import { NextRequest, NextResponse } from "next/server";
import { COOKIE, SESSION_HOURS, makeSession, passwordOk } from "../../../lib/session";

// Relative Location headers: the browser stays on whatever host it used (127.0.0.1, a LAN name, the
// cloud domain). Building absolute URLs from req.url sent it to "localhost" and lost the cookie.
function seeOther(location: string): NextResponse {
  return new NextResponse(null, { status: 303, headers: { Location: location } });
}

export async function POST(req: NextRequest) {
  const form = await req.formData().catch(() => null);
  const password = String(form?.get("password") ?? "");
  if (!passwordOk(password)) return seeOther("/login?error=1");
  const res = seeOther("/");
  res.cookies.set(COOKIE, await makeSession(), { httpOnly: true, sameSite: "lax", path: "/",
    maxAge: SESSION_HOURS * 3600, secure: req.headers.get("x-forwarded-proto") === "https" || req.nextUrl.protocol === "https:" });
  return res;
}
