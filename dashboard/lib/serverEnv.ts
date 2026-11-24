// Server-only settings. Reads the repository's .env (MQTT_*, FLYBOT_ROBOT_ID), then dashboard/.env.local
// (DASHBOARD_PASSWORD, DASHBOARD_SESSION_SECRET, LOGGER_API_URL; loaded by Next), then the process
// environment, which wins. Never import this from client components.
import fs from "node:fs";
import path from "node:path";

let cache: Record<string, string> | null = null;

function rootEnv(): Record<string, string> {
  if (cache) return cache;
  const out: Record<string, string> = {};
  for (const p of [path.resolve(process.cwd(), "..", ".env"), path.resolve(process.cwd(), ".env")]) {
    try {
      for (const line of fs.readFileSync(/*turbopackIgnore: true*/ p, "utf8").split(/\r?\n/)) {
        const i = line.indexOf("=");
        if (i > 0 && !line.trimStart().startsWith("#")) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
      }
      break;
    } catch {
      /* try the next location */
    }
  }
  cache = out;
  return out;
}

export function env(key: string, fallback = ""): string {
  return process.env[key] ?? rootEnv()[key] ?? fallback;
}

export function mqttConfig() {
  const host = env("MQTT_HOST", "127.0.0.1").replace(/^localhost$/, "127.0.0.1");
  const useTls = env("MQTT_USE_TLS", "false") === "true";
  const port = useTls ? env("MQTT_WSS_PORT", "8884") : env("MQTT_WS_PORT", "9001");
  return {
    url: env("MQTT_WS_URL") || `${useTls ? "wss" : "ws"}://${env("MQTT_PUBLIC_HOST", host)}:${port}`,
    username: env("MQTT_DASHBOARD_USER", "dashboard"),
    password: env("MQTT_DASHBOARD_PASS"),
    robotId: env("FLYBOT_ROBOT_ID", "01"),
  };
}
