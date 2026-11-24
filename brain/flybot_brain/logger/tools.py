"""Retention and export (plan step 8.3)."""
from __future__ import annotations

import csv
import sqlite3
import time
from pathlib import Path

from . import db

PRUNABLE = [t for t in db.TABLES if t not in db.PERMANENT]          # sensors, commands, brain_state


def export_csv(db_path: str | Path, out_dir: str | Path, since_s: float | None = None) -> dict[str, int]:
    """One CSV per table (UTF-8, header row). since_s: only rows from the last since_s seconds
    (tables with a ts column). Returns rows written per table."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    con = db.connect_readonly(db_path)
    written = {}
    cutoff = None if since_s is None else time.time() - since_s
    for table in db.TABLES:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        tcol = "ts" if "ts" in cols else "start_ts" if "start_ts" in cols else None
        sql = f"SELECT * FROM {table}"
        args: tuple = ()
        if cutoff is not None and tcol:
            sql += f" WHERE {tcol} >= ?"
            args = (cutoff,)
        sql += " ORDER BY rowid"
        with (out / f"{table}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            n = 0
            for row in con.execute(sql, args):
                w.writerow(list(row))
                n += 1
        written[table] = n
    con.close()
    return written


def prune(db_path: str | Path, keep_days: float) -> dict[str, int]:
    """Delete raw high-rate rows (sensors, commands, brain_state) older than keep_days. Events,
    trials, latency, status, weights and the reliability counts are kept permanently."""
    con = sqlite3.connect(Path(db_path))
    cutoff = time.time() - keep_days * 86400
    deleted = {}
    for table in PRUNABLE:
        deleted[table] = con.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,)).rowcount
    con.commit()
    con.execute("VACUUM")
    con.close()
    return deleted


def stats(db_path: str | Path) -> dict:
    con = db.connect_readonly(db_path)
    out = {"rows": {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in db.TABLES},
           "reliability": [dict(r) for r in con.execute("SELECT * FROM reliability ORDER BY topic, sender")]}
    con.close()
    return out
