"use client";
import { useEffect, useRef, useState } from "react";
import { LESION_TARGETS, type LesionTarget, type Outgoing } from "../lib/contract";
import type { Live } from "../lib/live";

const LESION_LABEL: Record<LesionTarget, string> = {
  rcpt: "receptors", ln: "AL inhibition", mb: "mushroom body", apl: "APL", learn: "learning",
  hab: "habituation", hun: "hunger", fear: "fear",
};

export function Controls({ live }: { live: Live }) {
  const send = (o: Outgoing) => live.publish(o);
  const ob = live.outbox;
  const st = live.brainState as { les?: string[]; hun?: number; fear?: number; ack?: { src: string; seq: number; ok: boolean } } | null;
  const lesions = new Set(st?.les ?? []);
  const [hun, setHun] = useState(0.5);
  const [fear, setFear] = useState(0.0);
  const [name, setName] = useState("");
  const ack = st?.ack && st.ack.src === ob.src ? st.ack : null;

  return (
    <div className="card controls">
      <div className="card-title">Controls <span className="muted">as {ob.src}</span></div>

      <section>
        <h3>Mode <span className="muted">now: {live.mode ?? "–"}</span></h3>
        <div className="btn-row">
          <button onClick={() => send(ob.mode("auto"))}>Auto</button>
          <button onClick={() => send(ob.mode("manual"))}>Manual</button>
          <button className="danger" onClick={() => send(ob.mode("estop"))}>E-STOP</button>
        </div>
      </section>

      <section>
        <h3>Manual drive <span className="muted">(mode manual)</span></h3>
        <DrivePad live={live} />
      </section>

      <section>
        <h3>Teach</h3>
        <div className="btn-row">
          <button className="good" onClick={() => send(ob.reward(1))}>Reward</button>
          <button className="bad" onClick={() => send(ob.reward(-1))}>Punish</button>
          <button onClick={() => send(ob.sleep(true))}>Sleep</button>
          <button onClick={() => send(ob.sleep(false))}>Wake</button>
        </div>
      </section>

      <section>
        <h3>Set internal state</h3>
        <label className="slider">Hunger <input type="range" min={0} max={1} step={0.05} value={hun}
          onChange={(e) => setHun(Number(e.target.value))} /> {hun.toFixed(2)}</label>
        <label className="slider">Fear <input type="range" min={0} max={1} step={0.05} value={fear}
          onChange={(e) => setFear(Number(e.target.value))} /> {fear.toFixed(2)}</label>
        <div className="btn-row">
          <button onClick={() => send(ob.setState({ hun }))}>Set hunger</button>
          <button onClick={() => send(ob.setState({ fear }))}>Set fear</button>
          <button onClick={() => send(ob.setState({ hun, fear }))}>Set both</button>
        </div>
      </section>

      <section>
        <h3>Lesions</h3>
        <div className="lesions">{LESION_TARGETS.map((t) => (
          <label key={t} className={`toggle ${lesions.has(t) ? "on" : ""}`}>
            <input type="checkbox" checked={lesions.has(t)} onChange={(e) => send(ob.lesion(t, e.target.checked))} />
            {LESION_LABEL[t]}
          </label>
        ))}</div>
      </section>

      <section>
        <h3>Brain memory</h3>
        <input className="text" placeholder="snapshot name (default latest)" value={name}
          onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, 32))} />
        <div className="btn-row">
          <button onClick={() => send(ob.save(name || undefined))}>Save</button>
          <button onClick={() => send(ob.load(name || undefined))}>Load</button>
          <button onClick={() => confirm("Reset learned weights?") && send(ob.reset("weights"))}>Reset weights</button>
          <button onClick={() => send(ob.reset("state"))}>Reset state</button>
        </div>
      </section>
      <p className="muted">Last answer to this tab: {ack ? `#${ack.seq} ${ack.ok ? "ok" : "refused"}` : "–"}</p>
    </div>
  );
}

/** Press and drag: forward/back and turn. Sends cmd/manual at 10 Hz while held, stops on release. */
function DrivePad({ live }: { live: Live }) {
  const pad = useRef<HTMLDivElement>(null);
  const vec = useRef<{ x: number; y: number } | null>(null);
  const [shown, setShown] = useState<{ x: number; y: number } | null>(null);
  useEffect(() => {
    const id = setInterval(() => {
      const v = vec.current;
      if (!v) return;
      const fwd = -v.y, turn = v.x;
      live.publish(live.outbox.manual(fwd + turn * 0.6, fwd - turn * 0.6, live.lastSensorsSeq));
    }, 100);
    return () => clearInterval(id);
  }, [live]);
  const update = (e: React.PointerEvent) => {
    const r = pad.current!.getBoundingClientRect();
    const x = Math.max(-1, Math.min(1, ((e.clientX - r.left) / r.width) * 2 - 1));
    const y = Math.max(-1, Math.min(1, ((e.clientY - r.top) / r.height) * 2 - 1));
    vec.current = { x, y };
    setShown({ x, y });
  };
  const stop = () => {
    vec.current = null;
    setShown(null);
    live.publish(live.outbox.manual(0, 0, live.lastSensorsSeq));
  };
  return (
    <div ref={pad} className="pad" onPointerDown={(e) => { (e.target as Element).setPointerCapture(e.pointerId); update(e); }}
      onPointerMove={(e) => vec.current && update(e)} onPointerUp={stop} onPointerCancel={stop} role="slider"
      aria-label="drive pad">
      <div className="pad-cross" />
      {shown && <div className="pad-dot" style={{ left: `${(shown.x + 1) * 50}%`, top: `${(shown.y + 1) * 50}%` }} />}
    </div>
  );
}
