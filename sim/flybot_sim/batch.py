"""Headless batch: N trials of a scenario -> CSV (plan step 3.7).

Each trial is a fresh robot boot (new connection; seq and up restart at 0, which the brain sees
as a reboot, contract 7.1 (3)), with seed = scenario seed + trial number and the start position
cycling through the scenario's list, so any single trial can be reproduced on its own.

Default mode is lockstep (faster than real time). Latency and round-trip columns are filled
only in real-time runs; in lockstep they stay empty (contract 7.1 (6)).
"""
from __future__ import annotations

import csv
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import REPO, Scenario, load_scenario
from .contract import Contract
from .runner import run_lockstep, run_realtime, trial_done
from .sim import Simulation

log = logging.getLogger("flybot_sim.batch")

COLUMNS = ["trial", "seed", "scenario", "mode", "controller", "start_index", "start_x", "start_y",
           "start_heading_deg", "reached", "time_to_source_s", "path_length_m", "collisions", "escapes",
           "fallback_time_s", "messages_lost", "rtt_median_ms", "crtt_median_ms", "sim_duration_s",
           "wall_duration_s"]


def run_trials(sc: Scenario, trials: int, controller: str, realtime: bool, out: Path,
               link_factory: Callable[[Contract], object]) -> list[dict]:
    contract = Contract()
    mode = "realtime" if realtime else "lockstep"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for i in range(trials):
            seed = sc.seed + i
            link = link_factory(contract)
            sim = Simulation(sc, link, contract, start_index=i, seed=seed, lockstep=not realtime)
            wall0 = time.perf_counter()
            try:
                if realtime:
                    run_realtime(sim, trial_done(sc.duration_s))
                else:
                    run_lockstep(sim, trial_done(sc.duration_s))
            finally:
                link.stop()
            r = sim.results()
            sp = sim.start
            row = {
                "trial": i + 1, "seed": seed, "scenario": sc.name, "mode": mode, "controller": controller,
                "start_index": sim.start_index, "start_x": sp.x, "start_y": sp.y,
                "start_heading_deg": sp.heading_deg, "reached": "yes" if r["reached"] else "no",
                "time_to_source_s": r["time_to_source_s"], "path_length_m": r["path_length_m"],
                "collisions": r["collisions"], "escapes": r["escapes"],
                "fallback_time_s": r["fallback_time_s"], "messages_lost": r["messages_lost"],
                "rtt_median_ms": r["rtt_median_ms"], "crtt_median_ms": r["crtt_median_ms"],
                "sim_duration_s": round(sim.trial_time_s(), 2),
                "wall_duration_s": round(time.perf_counter() - wall0, 2),
            }
            w.writerow({k: ("" if v is None else v) for k, v in row.items()})
            f.flush()
            rows.append(row)
            log.info("trial %d/%d seed %d start %d: reached=%s time=%s path=%.2f m wall=%.1f s",
                     i + 1, trials, seed, sim.start_index, row["reached"], row["time_to_source_s"],
                     row["path_length_m"], row["wall_duration_s"])
    return rows


def run_batch(args) -> int:
    from .__main__ import open_link

    sc = load_scenario(args.scenario)
    if args.seed is not None:
        sc.seed = args.seed
    if args.duration:
        sc.duration_s = args.duration
    mode = "realtime" if args.realtime else "lockstep"
    out = Path(args.out) if args.out else (
        REPO / "sim" / "results" / f"{sc.name}-{mode}-{datetime.now():%Y%m%d-%H%M%S}.csv")
    wall0 = time.perf_counter()
    rows = run_trials(sc, args.trials, args.controller, args.realtime, out, open_link)
    wall = time.perf_counter() - wall0
    reached = [r for r in rows if r["reached"] == "yes"]
    sim_s = sum(r["sim_duration_s"] for r in rows)
    print(f"\n{len(reached)}/{len(rows)} reached the source"
          + (f", mean time {sum(r['time_to_source_s'] for r in reached) / len(reached):.1f} s" if reached else ""))
    print(f"{sim_s:.0f} s of trials in {wall:.0f} s wall ({sim_s / wall:.1f}x real time, {mode})")
    print(f"results: {out}")
    return 0
