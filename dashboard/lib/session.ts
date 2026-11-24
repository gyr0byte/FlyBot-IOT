// Login session: an HttpOnly cookie "<expiry>.<hmac>" signed with DASHBOARD_SESSION_SECRET (HMAC-SHA256,
// Web Crypto, so it works in the proxy and in route handlers).
export const COOKIE = "flybot_session";
export const SESSION_HOURS = 12;

function secret(): string {
  const s = process.env.DASHBOARD_SESSION_SECRET || "";
  const p = process.env.DASHBOARD_PASSWORD || "";
  return s || (p ? `derived:${p}` : "");
}

async function hmac(data: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret()), { name: "HMAC", hash: "SHA-256" },
    false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
  return Array.from(new Uint8Array(sig), (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function makeSession(now = Date.now()): Promise<string> {
  const exp = now + SESSION_HOURS * 3600_000;
  return `${exp}.${await hmac(`flybot:${exp}`)}`;
}

export async function validSession(value: string | undefined, now = Date.now()): Promise<boolean> {
  if (!value || !secret()) return false;
  const [exp, sig] = value.split(".");
  if (!exp || !sig || Number(exp) < now) return false;
  const want = await hmac(`flybot:${exp}`);
  if (want.length !== sig.length) return false;
  let diff = 0;
  for (let i = 0; i < want.length; i++) diff |= want.charCodeAt(i) ^ sig.charCodeAt(i);
  return diff === 0;
}

export function passwordOk(given: string): boolean {
  const want = process.env.DASHBOARD_PASSWORD || "";
  if (!want || given.length !== want.length) return false;
  let diff = 0;
  for (let i = 0; i < want.length; i++) diff |= want.charCodeAt(i) ^ given.charCodeAt(i);
  return diff === 0;
}
