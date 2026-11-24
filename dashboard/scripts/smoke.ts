// Dashboard smoke test, MQTT side (run by scripts/smoke_all.py, or: node scripts/smoke.ts).
// Uses the dashboard's own contract module (lib/contract.ts) and mqtt.js over WebSockets as user
// "dashboard": waits for live data, then publishes every control, and waits for the brain's
// acknowledgement of each brain/control command. Prints one JSON line with the results.
import fs from "node:fs";
import path from "node:path";
import mqtt from "mqtt";
import { Outbox, SUBSCRIBE, nameOf, topic, type Outgoing } from "../lib/contract.ts";

const root = path.resolve(import.meta.dirname, "..", "..");
const env: Record<string, string> = {};
for (const line of fs.readFileSync(path.join(root, ".env"), "utf8").split(/\r?\n/)) {
  const i = line.indexOf("=");
  if (i > 0 && !line.trimStart().startsWith("#")) env[line.slice(0, i).trim()] = line.slice(i + 1).trim();
}
const robotId = env.FLYBOT_ROBOT_ID ?? "01";
const url = `ws://${(env.MQTT_HOST ?? "127.0.0.1").replace("localhost", "127.0.0.1")}:${env.MQTT_WS_PORT ?? "9001"}`;
const ob = new Outbox();
const seen: Record<string, number> = {};
let lastSensorsSeq: number | null = null;
let lastAck: { src: string; seq: number; ok: boolean } | null = null;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const client = mqtt.connect(url, { clientId: ob.clientId(robotId), username: env.MQTT_DASHBOARD_USER,
  password: env.MQTT_DASHBOARD_PASS, protocolVersion: 4, clean: true, connectTimeout: 5000 });

client.on("message", (t, payload) => {
  const name = nameOf(t, robotId);
  if (!name || !payload.length) return;
  seen[name] = (seen[name] ?? 0) + 1;
  const m = JSON.parse(payload.toString());
  if (name === "sensors") lastSensorsSeq = m.seq;
  if (name === "brain/state" && m.ack) lastAck = m.ack;
});

async function waitFor(cond: () => boolean, ms: number): Promise<boolean> {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (cond()) return true; await sleep(50); }
  return cond();
}

async function main() {
  const ok = await waitFor(() => client.connected, 8000);
  if (!ok) throw new Error(`cannot connect to ${url}`);
  await client.subscribeAsync(SUBSCRIBE.map((n) => topic(n, robotId)), { qos: 1 });
  const live = await waitFor(() => (seen.sensors ?? 0) > 20 && (seen["brain/state"] ?? 0) > 3 && (seen.status ?? 0) > 0, 30000);
  const send = async (o: Outgoing) => {
    await client.publishAsync(topic(o.topic, robotId), JSON.stringify(o.msg), { qos: o.qos, retain: o.retain });
    return o;
  };
  const acks: Record<string, boolean | null> = {};
  const control = async (label: string, o: Outgoing) => {
    await send(o);
    const got = await waitFor(() => !!lastAck && lastAck.src === ob.src && lastAck.seq === o.msg.seq, 4000);
    acks[label] = got ? lastAck!.ok : null;
  };
  const sent: string[] = [];
  const plain = async (label: string, o: Outgoing) => { await send(o); sent.push(label); await sleep(150); };

  await plain("mode manual", ob.mode("manual"));
  for (let i = 0; i < 5; i++) await plain("manual drive", ob.manual(0.4, 0.4, lastSensorsSeq));
  await plain("manual stop", ob.manual(0, 0, lastSensorsSeq));
  await plain("mode auto", ob.mode("auto"));
  await plain("reward", ob.reward(1));
  await plain("punish", ob.reward(-1));
  await plain("sleep", ob.sleep(true));
  await plain("wake", ob.sleep(false));
  await control("set hunger", ob.setState({ hun: 0.8 }));
  await control("set fear", ob.setState({ fear: 0.3 }));
  await control("set both", ob.setState({ hun: 0.2, fear: 0.1 }));
  for (const t of ["rcpt", "ln", "mb", "apl", "learn", "hun", "fear"] as const) {
    await control(`lesion ${t} off`, ob.lesion(t, true));
    await control(`lesion ${t} on`, ob.lesion(t, false));
  }
  await control("lesion hab (refused by design)", ob.lesion("hab", true));
  await control("save", ob.save("smoke"));
  await control("load", ob.load("smoke"));
  await control("reset state", ob.reset("state"));
  await control("reset weights", ob.reset("weights"));
  await plain("mode estop", ob.mode("estop"));
  await sleep(500);
  await plain("mode auto again", ob.mode("auto"));
  await sleep(500);
  console.log(JSON.stringify({ src: ob.src, live, seen, sent, acks }));
  await client.endAsync();
}

main().catch((e) => { console.log(JSON.stringify({ error: String(e), src: ob.src, seen })); client.end(true); process.exit(1); });
