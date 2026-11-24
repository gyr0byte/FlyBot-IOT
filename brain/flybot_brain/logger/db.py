"""SQLite schema and helpers for the logger (plan step 8.1).

Every row keeps both clocks: `ts` = arrival wall time (Unix seconds, for the dashboard's "last N
minutes") and `up` = the sender's uptime in ms, plus `bid` for robot messages, so fast lockstep runs
can be placed on a robot-time timeline (contract 7.1 (6)).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sensors (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, up INTEGER, bid INTEGER, seq INTEGER,
    ol REAL, orr REAL, d REAL, lt REAL, bat REAL);
CREATE INDEX IF NOT EXISTS sensors_ts ON sensors (ts);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, up INTEGER, bid INTEGER, seq INTEGER,
    source TEXT NOT NULL, type TEXT NOT NULL, details TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);

CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, up INTEGER, seq INTEGER,
    topic TEXT NOT NULL, source TEXT NOT NULL, l REAL, r REAL, ttl INTEGER, rs INTEGER, details TEXT);
CREATE INDEX IF NOT EXISTS commands_ts ON commands (ts);

CREATE TABLE IF NOT EXISTS brain_state (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, up INTEGER, seq INTEGER,
    hun REAL, fear REAL, hab REAL, slp INTEGER, dec TEXT, why TEXT, spk TEXT, les TEXT, pref TEXT, rs INTEGER);
CREATE INDEX IF NOT EXISTS brain_state_ts ON brain_state (ts);

CREATE TABLE IF NOT EXISTS weights (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, up INTEGER, seq INTEGER, n INTEGER, sc REAL, ap TEXT, av TEXT);
CREATE INDEX IF NOT EXISTS weights_ts ON weights (ts);

CREATE TABLE IF NOT EXISTS latency (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, up INTEGER, source TEXT NOT NULL,
    rtt_ms REAL, crtt_ms REAL, tick_ms REAL, lag INTEGER);
CREATE INDEX IF NOT EXISTS latency_ts ON latency (ts);

CREATE TABLE IF NOT EXISTS status (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, up INTEGER, seq INTEGER, source TEXT NOT NULL,
    st TEXT, details TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS status_ts ON status (ts);

CREATE TABLE IF NOT EXISTS trials (
    id INTEGER PRIMARY KEY, start_ts REAL NOT NULL, end_ts REAL, scenario TEXT, start_position TEXT,
    controller TEXT, outcome TEXT, notes TEXT);

CREATE TABLE IF NOT EXISTS reliability (
    topic TEXT NOT NULL, sender TEXT NOT NULL, received INTEGER NOT NULL, invalid INTEGER NOT NULL,
    lost INTEGER NOT NULL, duplicates INTEGER NOT NULL, updated_ts REAL NOT NULL,
    PRIMARY KEY (topic, sender));
"""

TABLES = ["sensors", "events", "commands", "brain_state", "weights", "latency", "status", "trials", "reliability"]
PERMANENT = {"events", "trials", "latency", "reliability", "status", "weights"}   # step 8.3: never pruned


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (and create) the database for writing. WAL mode: the API can read while the logger writes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    con.commit()
    return con


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con
