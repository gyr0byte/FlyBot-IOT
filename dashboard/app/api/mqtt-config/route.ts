// The browser gets the broker settings (including the dashboard MQTT password) only after login.
import { NextResponse } from "next/server";
import { mqttConfig } from "../../../lib/serverEnv";

export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json(mqttConfig(), { headers: { "Cache-Control": "no-store" } });
}
