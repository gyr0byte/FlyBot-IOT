"use client";
import { useEffect, useRef } from "react";
import type { SensorPoint } from "../lib/contract";

const COLORS = ["#4fc3f7", "#ffb74d", "#81c784", "#e57373"];

/** Rolling line chart on a canvas, redrawn at most 5 times a second (plan step 9.7). */
export function LineChart({ title, getData, series, yMin, yMax, unit = "", windowMs = 60_000 }: {
  title: string;
  getData: () => SensorPoint[];
  series: { key: keyof SensorPoint; label: string }[];
  yMin: number; yMax: number; unit?: string; windowMs?: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const draw = () => {
      const c = ref.current;
      if (!c) return;
      const dpr = window.devicePixelRatio || 1;
      const w = c.clientWidth, h = c.clientHeight;
      if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
      const g = c.getContext("2d")!;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, w, h);
      g.strokeStyle = "#2a3340"; g.lineWidth = 1;
      for (let i = 0; i <= 4; i++) { const y = (h * i) / 4; g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }
      g.fillStyle = "#8a96a8"; g.font = "12px system-ui";
      g.fillText(`${yMax}${unit}`, 4, 12); g.fillText(`${yMin}${unit}`, 4, h - 4);
      const data = getData();
      const now = Date.now();
      series.forEach((s, si) => {
        g.strokeStyle = COLORS[si % COLORS.length]; g.lineWidth = 2; g.beginPath();
        let started = false;
        for (const p of data) {
          const v = p[s.key] as number | null;
          if (v === null || v === undefined) { started = false; continue; }
          const x = w - ((now - p.t) / windowMs) * w;
          const y = h - ((Math.min(yMax, Math.max(yMin, v)) - yMin) / (yMax - yMin)) * h;
          if (!started) { g.moveTo(x, y); started = true; } else g.lineTo(x, y);
        }
        g.stroke();
      });
    };
    draw();
    const id = setInterval(draw, 200);
    return () => clearInterval(id);
  }, [getData, series, yMin, yMax, unit, windowMs]);
  return (
    <div className="card chart">
      <div className="card-title">{title}
        <span className="legend">{series.map((s, i) => <span key={s.label} style={{ color: COLORS[i % COLORS.length] }}>● {s.label}</span>)}</span>
      </div>
      <canvas ref={ref} className="chart-canvas" aria-label={title} />
    </div>
  );
}

/** Learned KC -> approach / avoid weights (0-255 per KC) as two heat maps. */
export function WeightsHeatmap({ ap, av, n }: { ap?: number[]; av?: number[]; n?: number }) {
  const refA = useRef<HTMLCanvasElement>(null);
  const refV = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const paint = (c: HTMLCanvasElement | null, w?: number[]) => {
      if (!c || !w || !w.length) return;
      const cols = Math.ceil(Math.sqrt(w.length * 1.25)), rows = Math.ceil(w.length / cols);
      c.width = cols; c.height = rows;
      const g = c.getContext("2d")!;
      const img = g.createImageData(cols, rows);
      w.forEach((v, i) => {
        const d = 255 - v;                                      // depression: 0 = untouched
        img.data[i * 4] = 30 + d * 0.8; img.data[i * 4 + 1] = 40 + (255 - d) * 0.5; img.data[i * 4 + 2] = 60;
        img.data[i * 4 + 3] = 255;
      });
      g.putImageData(img, 0, 0);
    };
    paint(refA.current, ap);
    paint(refV.current, av);
  }, [ap, av]);
  const mean = (w?: number[]) => (w && w.length ? (w.reduce((a, b) => a + b, 0) / w.length / 255).toFixed(3) : "–");
  return (
    <div className="card">
      <div className="card-title">Learned weights ({n ?? 0} Kenyon cells; red = depressed)</div>
      <div className="heatmaps">
        <figure><canvas ref={refA} className="heat" /><figcaption>KC → approach · mean {mean(ap)}</figcaption></figure>
        <figure><canvas ref={refV} className="heat" /><figcaption>KC → avoid · mean {mean(av)}</figcaption></figure>
      </div>
    </div>
  );
}

const GROUPS: [string, string][] = [["rl", "receptors L"], ["rr", "receptors R"], ["pl", "PN left"], ["pr", "PN right"],
  ["ln", "local inh."], ["kc", "Kenyon cells"], ["apl", "APL"], ["ma", "approach"], ["mv", "avoid"], ["dp", "DAN reward"],
  ["dn", "DAN punish"]];

export function SpikeBars({ spk }: { spk?: Record<string, number> }) {
  const max = Math.max(1, ...Object.values(spk ?? {}));
  return (
    <div className="card">
      <div className="card-title">Spikes per group (last 200 ms)</div>
      {!spk || !Object.keys(spk).length ? <p className="muted">No network activity (baseline controller or no data).</p> :
        <div className="bars">{GROUPS.filter(([k]) => k in spk).map(([k, label]) => (
          <div key={k} className="bar-row"><span className="bar-label">{label}</span>
            <span className="bar" style={{ width: `${(100 * spk[k]) / max}%` }} /><span className="bar-value">{spk[k]}</span></div>
        ))}</div>}
    </div>
  );
}

export function Gauge({ label, value, color }: { label: string; value?: number | null; color: string }) {
  const v = typeof value === "number" ? value : null;
  return (
    <div className="gauge">
      <div className="gauge-label">{label}</div>
      <div className="gauge-track"><div className="gauge-fill" style={{ width: `${(v ?? 0) * 100}%`, background: color }} /></div>
      <div className="gauge-value">{v === null ? "–" : v.toFixed(2)}</div>
    </div>
  );
}

export function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "ok" | "warn" | "bad" }) {
  return <div className={`stat ${tone ?? ""}`}><div className="stat-label">{label}</div><div className="stat-value">{value}</div></div>;
}
