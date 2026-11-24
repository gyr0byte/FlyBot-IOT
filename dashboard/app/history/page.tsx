"use client";
// History from the logger's read API (proxied through /api/history/*, behind the login).
import Link from "next/link";
import { useEffect, useState } from "react";

type J = Record<string, any>;

async function get(path: string): Promise<J | J[] | null> {
  try {
    const r = await fetch(`/api/history/${path}`, { cache: "no-store" });
    return r.ok ? r.json() : null;
  } catch {
    return null;
  }
}

export default function History() {
  const [minutes, setMinutes] = useState(60);
  const [data, setData] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    setLoading(true);
    Promise.all([get(`sensors?minutes=${minutes}&max_points=600`), get(`events?minutes=${minutes}&limit=200`),
      get(`latency?minutes=${minutes}`), get("trials?limit=500"), get(`timeline?minutes=${minutes}`), get("reliability"),
      get("health")])
      .then(([sensors, events, latency, trials, timeline, reliability, health]) => {
        setData({ sensors, events, latency, trials, timeline, reliability, health });
        setLoading(false);
      });
  }, [minutes]);
  const now = Date.now() / 1000;
  const t0 = now - minutes * 60;

  return (
    <main className="page">
      <header className="top">
        <h1>FlyBot history</h1>
        <nav><Link href="/">Live</Link>
          <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} aria-label="time window">
            {[15, 60, 240, 1440].map((m) => <option key={m} value={m}>last {m < 60 ? `${m} min` : `${m / 60} h`}</option>)}
          </select></nav>
      </header>
      {!loading && !data.health && <p className="bad-text">Logger API not reachable (start it with
        <code> python -m flybot_brain.logger api</code>).</p>}

      <section className="card">
        <div className="card-title">Odour and light ({data.sensors?.count ?? 0} sensor messages)</div>
        <Lines points={data.sensors?.points ?? []} t0={t0} t1={now} keys={[["ol", "#4fc3f7"], ["or", "#ffb74d"], ["lt", "#81c784"]]} />
      </section>

      <section className="card">
        <div className="card-title">A day in the life: meals, startles and sleep</div>
        <Timeline tl={data.timeline} t0={t0} t1={now} />
      </section>

      <section className="grid lower">
        <div className="card">
          <div className="card-title">Latency (robot round trips)</div>
          <Histogram title="ping → pong" s={data.latency?.rtt_ms} />
          <Histogram title="sensor → command" s={data.latency?.crtt_ms} />
          <p className="muted">Brain tick: median {data.latency?.tick_ms?.median ?? "–"} ms · max lag {data.latency?.lag_max ?? "–"}</p>
        </div>
        <div className="card">
          <div className="card-title">Trials</div>
          {(data.trials?.summary ?? []).length === 0 ? <p className="muted">No trials recorded yet.</p> :
            <table><thead><tr><th>Scenario</th><th>Controller</th><th>Trials</th><th>Success</th></tr></thead>
              <tbody>{data.trials.summary.map((s: J) => (
                <tr key={`${s.scenario}${s.controller}`}><td>{s.scenario}</td><td>{s.controller}</td><td>{s.n}</td>
                  <td>{s.rate !== null ? `${Math.round(s.rate * 100)}%` : "–"}</td></tr>))}</tbody></table>}
        </div>
      </section>

      <section className="grid lower">
        <div className="card events">
          <div className="card-title">Event timeline</div>
          <ul>{(Array.isArray(data.events) ? data.events : []).map((e: J, i: number) => (
            <li key={i}><span className="muted">{new Date(e.ts * 1000).toLocaleTimeString()}</span> <b>{e.type}</b>
              {" "}<span className="muted">{e.source}</span> {JSON.stringify(e.details)}</li>))}</ul>
        </div>
        <div className="card">
          <div className="card-title">Message reliability</div>
          <table><thead><tr><th>Topic</th><th>Sender</th><th>Received</th><th>Lost</th><th>Invalid</th></tr></thead>
            <tbody>{(Array.isArray(data.reliability) ? data.reliability : []).map((r: J) => (
              <tr key={`${r.topic}|${r.sender}`}><td>{r.topic}</td><td>{r.sender}</td><td>{r.received}</td>
                <td>{r.lost}</td><td>{r.invalid}</td></tr>))}</tbody></table>
        </div>
      </section>
    </main>
  );
}

function Lines({ points, t0, t1, keys }: { points: J[]; t0: number; t1: number; keys: [string, string][] }) {
  const W = 1000, H = 200;
  const x = (t: number) => ((t - t0) / (t1 - t0)) * W;
  const y = (v: number) => H - Math.min(1, Math.max(0, v)) * H;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="svg-chart" preserveAspectRatio="none" role="img" aria-label="sensor history">
      {[0.25, 0.5, 0.75].map((g) => <line key={g} x1={0} x2={W} y1={H * g} y2={H * g} stroke="#2a3340" />)}
      {keys.map(([k, c]) => (
        <polyline key={k} fill="none" stroke={c} strokeWidth={1.5} vectorEffect="non-scaling-stroke"
          points={points.filter((p) => p[k] !== null).map((p) => `${x(p.ts).toFixed(1)},${y(p[k]).toFixed(1)}`).join(" ")} />
      ))}
    </svg>
  );
}

function Timeline({ tl, t0, t1 }: { tl?: J; t0: number; t1: number }) {
  const W = 1000;
  const x = (t: number) => Math.max(0, Math.min(W, ((t - t0) / (t1 - t0)) * W));
  if (!tl) return <p className="muted">No data.</p>;
  return (
    <>
      <svg viewBox={`0 0 ${W} 60`} className="svg-timeline" preserveAspectRatio="none" role="img" aria-label="timeline">
        {(tl.sleep ?? []).map((s: J, i: number) => (
          <rect key={`s${i}`} x={x(s.start)} width={Math.max(2, x(s.end ?? t1) - x(s.start))} y={0} height={60} fill="#3949ab55" />))}
        {(tl.meals ?? []).map((t: number, i: number) => <line key={`m${i}`} x1={x(t)} x2={x(t)} y1={5} y2={30} stroke="#81c784" strokeWidth={3} />)}
        {(tl.startles ?? []).map((s: J, i: number) => <line key={`e${i}`} x1={x(s.ts)} x2={x(s.ts)} y1={32} y2={57} stroke="#e57373" strokeWidth={3} />)}
      </svg>
      <p className="muted"><span style={{ color: "#81c784" }}>■</span> meals ({(tl.meals ?? []).length}) ·{" "}
        <span style={{ color: "#e57373" }}>■</span> startles ({(tl.startles ?? []).length}) ·{" "}
        <span style={{ color: "#7986cb" }}>■</span> sleep ({(tl.sleep ?? []).length})</p>
    </>
  );
}

function Histogram({ title, s }: { title: string; s?: J }) {
  const bins: J[] = s?.histogram ?? [];
  const max = Math.max(1, ...bins.map((b) => b.count));
  return (
    <div className="hist">
      <div className="muted">{title}: median {s?.median ?? "–"} ms · p95 {s?.p95 ?? "–"} ms · n {s?.n ?? 0}</div>
      <svg viewBox="0 0 200 50" className="svg-hist" preserveAspectRatio="none" role="img" aria-label={title}>
        {bins.map((b, i) => (
          <rect key={i} x={(i * 200) / bins.length} width={200 / bins.length - 1} y={50 - (b.count / max) * 50}
            height={(b.count / max) * 50} fill="#4fc3f7" />))}
      </svg>
    </div>
  );
}
