"use client";
import Link from "next/link";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { Controls } from "../components/Controls";
import { Gauge, LineChart, SpikeBars, Stat, WeightsHeatmap } from "../components/widgets";
import { live, type MqttConfig } from "../lib/live";

type J = Record<string, any>;
const STATE_TONE: Record<string, "ok" | "warn" | "bad"> = { cloud: "ok", manual: "warn", escape: "warn", fallback: "warn",
  sleep: "warn", boot: "warn", estop: "bad", offline: "bad" };

export default function Dashboard() {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!live) return;
    fetch("/api/mqtt-config", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`config ${r.status}`))))
      .then((cfg: MqttConfig) => live!.connect(cfg))
      .catch((e) => setError(String(e)));
    return () => live?.disconnect();
  }, []);
  useSyncExternalStore(live?.subscribe ?? (() => () => {}), live?.getVersion ?? (() => 0), () => 0);
  const sensors = useCallback(() => live?.sensors ?? [], []);
  if (!live) return null;
  const st = live.status as J | null, bs = live.brainStatus as J | null, s = live.brainState as J | null;
  const w = live.weights as J | null, sn = live.lastSensors as J | null;
  const robotOnline = !!st && st.st !== "offline";
  const brainOnline = !!bs && bs.st === "online";
  const lag = bs?.lag as number | undefined;

  return (
    <main className="page">
      <header className="top">
        <h1>FlyBot</h1>
        <nav><Link href="/history">History</Link>
          <form method="post" action="/api/logout"><button className="link">Sign out</button></form></nav>
      </header>
      {error && <p className="bad-text">Cannot load broker settings: {error}</p>}

      <section className="grid overview">
        <Stat label="Broker" value={live.broker} tone={live.broker === "online" ? "ok" : "bad"} />
        <Stat label="Robot" value={robotOnline ? "online" : "offline"} tone={robotOnline ? "ok" : "bad"} />
        <Stat label="Brain" value={brainOnline ? `online · ${bs?.ctrl}` : "offline"} tone={brainOnline ? "ok" : "bad"} />
        <Stat label="Robot state" value={st?.st ?? "–"} tone={STATE_TONE[String(st?.st)] ?? undefined} />
        <Stat label="Decision" value={s?.dec ?? "–"} />
        <Stat label="Battery" value={st?.bat !== undefined ? `${Number(st.bat).toFixed(2)} V` : sn?.bat ? `${sn.bat} V` : "–"}
          tone={Number(st?.bat ?? sn?.bat ?? 9) < 6.8 ? "warn" : undefined} />
        <Stat label="Wi-Fi" value={st?.rssi !== undefined ? `${st.rssi} dBm` : "–"} />
        <Stat label="Round trip" value={st?.rtt != null ? `${st.rtt} ms` : "–"} />
        <Stat label="Sensor→command" value={st?.crtt != null ? `${st.crtt} ms` : "–"} />
        <Stat label="Brain lag" value={lag !== undefined ? `${lag} msgs` : "–"} tone={lag !== undefined && lag > 40 ? "bad" : undefined} />
        <Stat label="Brain tick" value={bs?.tick !== undefined ? `${bs.tick} ms` : "–"} />
        <Stat label="Sensors" value={bs?.sens ?? "–"} tone={bs?.sens === "ok" ? "ok" : bs ? "warn" : undefined} />
      </section>

      <section className="card gauges">
        <Gauge label="Hunger" value={s?.hun} color="#ffb74d" />
        <Gauge label="Fear" value={s?.fear} color="#e57373" />
        <Gauge label="Habituation" value={s?.hab ?? st?.hab} color="#9575cd" />
        <div className="why"><span className="muted">Why:</span> {(s?.why ?? []).join(" · ") || "–"}
          {s?.pref && Object.keys(s.pref).length > 0 &&
            <span className="muted"> · learned: {Object.entries(s.pref).map(([k, v]) => `${k} ${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(2)}`).join(", ")}</span>}
        </div>
      </section>

      <section className="grid charts">
        <LineChart title="Odour" getData={sensors} series={ODOUR} yMin={0} yMax={1} />
        <LineChart title="Distance" getData={sensors} series={DIST} yMin={0} yMax={200} unit=" cm" />
        <LineChart title="Light" getData={sensors} series={LIGHT} yMin={0} yMax={1} />
      </section>

      <section className="grid brain">
        <SpikeBars spk={s?.spk} />
        <WeightsHeatmap ap={w?.ap} av={w?.av} n={w?.n} />
      </section>

      <section className="grid lower">
        <Controls live={live} />
        <div className="card events">
          <div className="card-title">Recent events <span className="muted">{live.received} messages</span></div>
          <ul>{live.events.map((e, i) => (
            <li key={`${e.t}-${i}`}><span className="muted">{new Date(e.t).toLocaleTimeString()}</span> <b>{e.kind}</b> {e.text}</li>
          ))}</ul>
        </div>
      </section>
    </main>
  );
}

const ODOUR = [{ key: "ol" as const, label: "left" }, { key: "or" as const, label: "right" }];
const DIST = [{ key: "d" as const, label: "distance" }];
const LIGHT = [{ key: "lt" as const, label: "light" }];
