import { NextRequest, NextResponse } from "next/server";
import { COOKIE, SESSION_HOURS, makeSession, passwordOk } from "../../../lib/session";

export async function POST(req: NextRequest) {
  const form = await req.formData().catch(() => null);
  const password = String(form?.get("password") ?? "");
  if (!passwordOk(password)) {
    return NextResponse.redirect(new URL("/login?error=1", req.url), 303);
  }
  const res = NextResponse.redirect(new URL("/", req.url), 303);
  res.cookies.set(COOKIE, await makeSession(), { httpOnly: true, sameSite: "lax", path: "/",
    maxAge: SESSION_HOURS * 3600, secure: req.nextUrl.protocol === "https:" });
  return res;
}
