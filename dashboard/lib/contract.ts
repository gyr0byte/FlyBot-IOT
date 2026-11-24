// FlyBot MQTT contract, dashboard side (docs/message-contract.md). No imports: shared by the
// browser app and the Node smoke test (node scripts/smoke.ts).
//
// Each tab (or smoke-test run) has its own sender name, `dash-` + 6 hex, used in `src` and in the
// client ID `flybot-<id>-dash-xxxxxx`; per-topic `seq` counters start at 0 and `up` is the sender's
// uptime in ms (contract section 2).

export type Json = Record<string, unknown>;

export const ROBOT_ID = "01";

// Topics the dashboard may read (contract section 4: every topic except brain/sleep).
export const SUBSCRIBE = [
  "sensors", "events", "status", "cmd/motor", "cmd/manual", "cmd/mode", "cmd/sleep", "reward",
  "brain/status", "brain/state", "brain/weights", "brain/control", "ping", "pong",
] as const;

// What the dashboard publishes: schema version, QoS and retain flag per topic (contract section 1).
export const PUBLISH = {
  "cmd/mode": { v: 2, qos: 1, retain: true },
  "cmd/manual": { v: 2, qos: 0, retain: false },
  reward: { v: 3, qos: 1, retain: false },
  "brain/control": { v: 4, qos: 1, retain: false },
  "brain/sleep": { v: 2, qos: 1, retain: false },
} as const;

export type PublishTopic = keyof typeof PUBLISH;
export type Mode = "auto" | "manual" | "estop";
export type LesionTarget = "rcpt" | "ln" | "mb" | "apl" | "learn" | "hab" | "hun" | "fear";
export const LESION_TARGETS: LesionTarget[] = ["rcpt", "ln", "mb", "apl", "learn", "hab", "hun", "fear"];

export function newSenderName(random: () => number = Math.random): string {
  let hex = "";
  for (let i = 0; i < 6; i++) hex += Math.floor(random() * 16).toString(16);
  return `dash-${hex}`;
}

export function topic(name: string, robotId: string = ROBOT_ID): string {
  return `flybot/${robotId}/${name}`;
}

export function nameOf(fullTopic: string, robotId: string = ROBOT_ID): string | null {
  const prefix = `flybot/${robotId}/`;
  return fullTopic.startsWith(prefix) ? fullTopic.slice(prefix.length) : null;
}

const clamp = (x: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, x));
const round = (x: number, dp: number) => Math.round(x * 10 ** dp) / 10 ** dp;

export interface Outgoing {
  topic: PublishTopic;
  msg: Json;
  qos: 0 | 1;
  retain: boolean;
}

// Builds contract-valid messages for one sender (one tab).
export class Outbox {
  readonly src: string;
  private seq: Record<string, number> = {};
  private t0: number;
  private now: () => number;

  constructor(src: string = newSenderName(), now: () => number = () => Date.now()) {
    this.src = src;
    this.now = now;
    this.t0 = now();
  }

  clientId(robotId: string = ROBOT_ID): string {
    return `flybot-${robotId}-${this.src}`;
  }

  private build(t: PublishTopic, fields: Json): Outgoing {
    const s = this.seq[t] ?? 0;
    this.seq[t] = (s + 1) % 4294967296;
    const spec = PUBLISH[t];
    const up = Math.max(0, Math.round(this.now() - this.t0));
    return { topic: t, msg: { v: spec.v, seq: s, up, ...fields, src: this.src }, qos: spec.qos, retain: spec.retain };
  }

  mode(m: Mode): Outgoing {
    return this.build("cmd/mode", { m });
  }

  /** Manual drive (10 Hz while held). rs = seq of the newest sensors message this tab has seen. */
  manual(l: number, r: number, rs: number | null, ttl = 300): Outgoing {
    return this.build("cmd/manual", { l: round(clamp(l, -1, 1), 2), r: round(clamp(r, -1, 1), 2),
      ttl: Math.round(clamp(ttl, 50, 1000)), rs: rs ?? 0 });
  }

  reward(r: 1 | -1): Outgoing {
    return this.build("reward", { r });
  }

  sleep(s: boolean): Outgoing {
    return this.build("brain/sleep", { s });
  }

  lesion(tgt: LesionTarget, off: boolean): Outgoing {
    return this.build("brain/control", { c: "lesion", tgt, off });
  }

  setState(state: { hun?: number; fear?: number }): Outgoing {
    const f: Json = { c: "state" };
    if (state.hun !== undefined) f.hun = round(clamp(state.hun, 0, 1), 2);
    if (state.fear !== undefined) f.fear = round(clamp(state.fear, 0, 1), 2);
    if (!("hun" in f) && !("fear" in f)) throw new Error("set_state needs hun or fear");
    return this.build("brain/control", f);
  }

  save(name?: string): Outgoing {
    return this.build("brain/control", name ? { c: "save", name } : { c: "save" });
  }

  load(name?: string): Outgoing {
    return this.build("brain/control", name ? { c: "load", name } : { c: "load" });
  }

  reset(what: "weights" | "state" | "all"): Outgoing {
    return this.build("brain/control", { c: "reset", what });
  }

  param(k: string, val: number | boolean | string): Outgoing {
    return this.build("brain/control", { c: "param", k, val });
  }
}

/** Messages as the dashboard keeps them: the latest of each kind, plus rolling sensor history. */
export interface SensorPoint { t: number; ol: number | null; or: number | null; d: number | null; lt: number | null }
