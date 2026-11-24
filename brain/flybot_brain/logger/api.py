"""Read-only HTTP API over the logger's database, for the dashboard's history page (plan step 8.4).

    .venv\\Scripts\\python -m flybot_brain.logger api --port 8000

Binds to 127.0.0.1 by default; the dashboard's server calls it (behind the dashboard login), the
browser never does. The database is opened read-only. Times are Unix seconds (`ts`, arrival time).
OpenAPI page: http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from fastapi import FastAPI, Query

from . import db


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return round(s[k], 2)


def _stats(values: list[float], bins: int = 20) -> dict:
    values = [v for v in values if v is not None]
    out = {"n": len(values), "median": round(statistics.median(values), 2) if values else None,
           "p95": _pct(values, 95), "max": round(max(values), 2) if values else None, "histogram": []}
    if values:
        lo, hi = min(values), max(values)
        width = (hi - lo) / bins if hi > lo else 1.0
        counts = [0] * bins
        for v in values:
            counts[min(bins - 1, int((v - lo) / width))] += 1
        out["histogram"] = [{"lo": round(lo + i * width, 2), "hi": round(lo + (i + 1) * width, 2), "count": c}
                            for i, c in enumerate(counts)]
    return out


def create_app(db_path: str | Path) -> FastAPI:
    app = FastAPI(title="FlyBot history API", description="Read-only access to the logger database (Phase 8).")

    def con():
        return db.connect_readonly(db_path)

    def since(minutes: float) -> float:
        return time.time() - minutes * 60

    @app.get("/api/health")
    def health():
        c = con()
        try:
            rows = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in db.TABLES}
            last = c.execute("SELECT MAX(ts) FROM sensors").fetchone()[0]
        finally:
            c.close()
        return {"ok": True, "rows": rows, "last_sensors_ts": last}

    @app.get("/api/sensors")
    def sensors(minutes: float = Query(10, gt=0, le=1440), max_points: int = Query(600, ge=10, le=5000)):
        c = con()
        try:
            rows = c.execute("SELECT ts, up, bid, ol, orr, d, lt, bat FROM sensors WHERE ts >= ? ORDER BY ts",
                             (since(minutes),)).fetchall()
        finally:
            c.close()
        step = max(1, len(rows) // max_points)
        return {"count": len(rows), "step": step,
                "points": [{"ts": r["ts"], "up": r["up"], "ol": r["ol"], "or": r["orr"], "d": r["d"], "lt": r["lt"],
                            "bat": r["bat"]} for r in rows[::step]]}

    @app.get("/api/events")
    def events(minutes: float = Query(60, gt=0, le=10080), limit: int = Query(500, ge=1, le=5000)):
        c = con()
        try:
            rows = c.execute("SELECT ts, up, source, type, details FROM events WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
                             (since(minutes), limit)).fetchall()
        finally:
            c.close()
        return [{"ts": r["ts"], "up": r["up"], "source": r["source"], "type": r["type"],
                 "details": json.loads(r["details"])} for r in rows]

    @app.get("/api/latency")
    def latency(minutes: float = Query(60, gt=0, le=10080)):
        c = con()
        try:
            rows = c.execute("SELECT source, rtt_ms, crtt_ms, tick_ms, lag FROM latency WHERE ts >= ?",
                             (since(minutes),)).fetchall()
        finally:
            c.close()
        robot = [r for r in rows if r["source"] == "robot"]
        brain = [r for r in rows if r["source"] == "brain"]
        return {"rtt_ms": _stats([r["rtt_ms"] for r in robot]), "crtt_ms": _stats([r["crtt_ms"] for r in robot]),
                "tick_ms": _stats([r["tick_ms"] for r in brain]),
                "lag_max": max([r["lag"] or 0 for r in brain], default=None)}

    @app.get("/api/trials")
    def trials(limit: int = Query(500, ge=1, le=10000)):
        c = con()
        try:
            rows = [dict(r) for r in c.execute("SELECT * FROM trials ORDER BY start_ts DESC LIMIT ?", (limit,))]
        finally:
            c.close()
        summary: dict[str, dict] = {}
        for r in rows:
            key = f"{r['scenario']}|{r['controller']}"
            s = summary.setdefault(key, {"scenario": r["scenario"], "controller": r["controller"], "n": 0, "success": 0})
            s["n"] += 1
            s["success"] += r["outcome"] in ("reached", "success", "yes")
        for s in summary.values():
            s["rate"] = round(s["success"] / s["n"], 3) if s["n"] else None
        return {"trials": rows, "summary": list(summary.values())}

    @app.get("/api/timeline")
    def timeline(minutes: float = Query(60, gt=0, le=10080)):
        """'Day in the life': meals (start of eating), startles (escape events) and sleep periods."""
        c = con()
        try:
            states = c.execute("SELECT ts, dec, slp FROM brain_state WHERE ts >= ? ORDER BY ts",
                               (since(minutes),)).fetchall()
            escapes = c.execute("SELECT ts, details FROM events WHERE type = 'escape' AND ts >= ? ORDER BY ts",
                                (since(minutes),)).fetchall()
        finally:
            c.close()
        meals, sleeps = [], []
        prev_dec, sleep_start = None, None
        for r in states:
            if r["dec"] == "eat" and prev_dec != "eat":
                meals.append(r["ts"])
            if r["slp"] and sleep_start is None:
                sleep_start = r["ts"]
            elif not r["slp"] and sleep_start is not None:
                sleeps.append({"start": sleep_start, "end": r["ts"]})
                sleep_start = None
            prev_dec = r["dec"]
        if sleep_start is not None:
            sleeps.append({"start": sleep_start, "end": None})
        return {"meals": meals, "sleep": sleeps,
                "startles": [{"ts": r["ts"], **json.loads(r["details"])} for r in escapes]}

    @app.get("/api/reliability")
    def reliability():
        c = con()
        try:
            return [dict(r) for r in c.execute("SELECT * FROM reliability ORDER BY topic, sender")]
        finally:
            c.close()

    return app
