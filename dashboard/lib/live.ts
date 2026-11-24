// Live connection to the broker (MQTT over WebSockets as user "dashboard") and the latest state.
// React re-renders at most 4 times a second (version counter, plan step 9.7); charts read the
// rolling buffers directly on their own redraw timer.
import mqtt, { MqttClient } from "mqtt";
import { Json, Outbox, Outgoing, SUBSCRIBE, SensorPoint, nameOf, topic } from "./contract";

export interface MqttConfig { url: string; username: string; password: string; robotId: string }
export interface EventItem { t: number; kind: string; text: string }

const HISTORY_MS = 60_000;

export class Live {
  readonly outbox = new Outbox();
  client: MqttClient | null = null;
  robotId = "01";
  broker: "connecting" | "online" | "offline" = "connecting";
  status: Json | null = null;          // robot status (retained)
  brainStatus: Json | null = null;     // brain/status (retained)
  brainState: Json | null = null;      // brain/state, 5 Hz
  weights: Json | null = null;         // brain/weights (retained)
  mode: string | null = null;          // cmd/mode (retained)
  lastSensors: Json | null = null;
  lastSensorsSeq: number | null = null;
  lastMotor: Json | null = null;
  sensors: SensorPoint[] = [];
  events: EventItem[] = [];
  received = 0;
  version = 0;
  private dirty = false;
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setInterval> | null = null;

  connect(cfg: MqttConfig) {
    this.robotId = cfg.robotId;
    this.client = mqtt.connect(cfg.url, {
      clientId: this.outbox.clientId(cfg.robotId), username: cfg.username, password: cfg.password,
      protocolVersion: 4, clean: true, reconnectPeriod: 2000, connectTimeout: 5000,
    });
    this.client.on("connect", () => {
      this.broker = "online";
      this.client!.subscribe(SUBSCRIBE.map((n) => topic(n, this.robotId)), { qos: 1 });
      this.touch();
    });
    this.client.on("close", () => { this.broker = "offline"; this.touch(); });
    this.client.on("offline", () => { this.broker = "offline"; this.touch(); });
    this.client.on("message", (t, payload, packet) => this.onMessage(t, payload, packet.retain));
    this.timer = setInterval(() => {
      if (this.dirty) {
        this.dirty = false;
        this.version++;
        this.listeners.forEach((l) => l());
      }
    }, 250);
  }

  disconnect() {
    if (this.timer) clearInterval(this.timer);
    this.client?.end(true);
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  getVersion = () => this.version;

  publish(o: Outgoing) {
    if (!this.client || this.broker !== "online") return false;
    this.client.publish(topic(o.topic, this.robotId), JSON.stringify(o.msg), { qos: o.qos, retain: o.retain });
    if (o.topic !== "cmd/manual") this.addEvent("sent", `${o.topic} ${JSON.stringify(stripCommon(o.msg))}`);
    return true;
  }

  private touch() { this.dirty = true; }

  private addEvent(kind: string, text: string) {
    this.events.unshift({ t: Date.now(), kind, text });
    if (this.events.length > 30) this.events.length = 30;
  }

  private onMessage(fullTopic: string, payload: Uint8Array, retained: boolean) {
    const name = nameOf(fullTopic, this.robotId);
    if (!name || payload.length === 0) return;
    let msg: Json;
    try { msg = JSON.parse(new TextDecoder().decode(payload)); } catch { return; }
    this.received++;
    const now = Date.now();
    switch (name) {
      case "sensors": {
        this.lastSensors = msg;
        this.lastSensorsSeq = msg.seq as number;
        this.sensors.push({ t: now, ol: msg.ol as number | null, or: msg.or as number | null, d: msg.d as number | null,
          lt: msg.lt as number | null });
        const cutoff = now - HISTORY_MS;
        while (this.sensors.length && this.sensors[0].t < cutoff) this.sensors.shift();
        break;
      }
      case "status": this.status = msg; if (!retained || msg.st === "offline") this.addEvent("robot", `status ${msg.st}`); break;
      case "brain/status": this.brainStatus = msg; break;
      case "brain/state": this.brainState = msg; break;
      case "brain/weights": this.weights = msg; break;
      case "cmd/mode": this.mode = msg.m as string; if (!retained) this.addEvent("mode", `${msg.m} (${msg.src})`); break;
      case "cmd/motor": this.lastMotor = msg; break;
      case "events": this.addEvent("event", `${msg.e} ${JSON.stringify(stripCommon(msg, ["e"]))}`); break;
      case "reward": this.addEvent("reward", `${msg.r === 1 ? "reward" : "punish"} from ${msg.src}`); break;
      case "cmd/sleep": if (!retained) this.addEvent("sleep", `${msg.s ? "sleep" : "wake"} (${msg.why})`); break;
    }
    this.touch();
  }
}

function stripCommon(m: Json, extra: string[] = []): Json {
  const out: Json = {};
  for (const [k, v] of Object.entries(m)) if (!["v", "seq", "up", "bid", "src", ...extra].includes(k)) out[k] = v;
  return out;
}

export const live: Live | null = typeof window === "undefined" ? null : new Live();
