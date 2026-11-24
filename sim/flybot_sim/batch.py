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

from .config import REPO, Scenario, check_training_starts, load_scenario, phase_starts
from flybot_contract import Contract
from .runner import BATCH_WAIT_MS, run_lockstep, run_realtime, trial_done
from .sim import Simulation

log = logging.getLogger("flybot_sim.batch")

COLUMNS = ["trial", "seed", "scenario", "mode", "controller", "phase", "start_index", "start_x", "start_y",
           "start_heading_deg", "reached", "reached_label", "reward_sent", "time_to_source_s", "path_length_m",
           "collisions", "escapes", "fallback_time_s", "messages_lost", "lockstep_timeouts", "min_source_dist_m", "threats", "rtt_median_ms", "crtt_median_ms",
           "sim_duration_s", "wall_duration_s"]


def run_trials(sc: Scenario, trials: int, controller: str, realtime: bool, out: Path,
               link_factory: Callable[[Contract], object], wait_ms: int = BATCH_WAIT_MS) -> list[dict]:
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
                timeouts = None
                if realtime:
                    run_realtime(sim, trial_done(sc.duration_s))
                else:
                    timeouts = run_lockstep(sim, trial_done(sc.duration_s), wait_ms=wait_ms).timeouts
            finally:
                link.stop()
            r = sim.results()
            sp = sim.start
            row = {
                "trial": i + 1, "seed": seed, "scenario": sc.name, "mode": mode, "controller": controller,
                "phase": "", "start_index": sim.start_index, "start_x": sp.x, "start_y": sp.y,
                "start_heading_deg": sp.heading_deg, "reached": "yes" if r["reached"] else "no",
                "reached_label": r["reached_label"], "reward_sent": r["reward_sent"],
                "time_to_source_s": r["time_to_source_s"], "path_length_m": r["path_length_m"],
                "collisions": r["collisions"], "escapes": r["escapes"],
                "fallback_time_s": r["fallback_time_s"], "messages_lost": r["messages_lost"],
                "lockstep_timeouts": timeouts, "min_source_dist_m": r["min_source_dist_m"], "threats": r["threats"], "rtt_median_ms": r["rtt_median_ms"], "crtt_median_ms": r["crtt_median_ms"],
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


def run_experiment(sc: Scenario, controller: str, out: Path, link_factory: Callable[[Contract], object],
                   on_phase_end: Callable[[str, list[dict]], None] | None = None,
                   wait_ms: int = BATCH_WAIT_MS,
                   catch_up: Callable[[Simulation], None] | None = None) -> list[dict]:
    """Run a scenario's experiment phases in lockstep, one animal (the brain keeps what it learns:
    run the brain WITHOUT --reset-on-reboot). Seeds continue across phases (scenario seed + trial)."""
    if sc.experiment is None:
        raise ValueError(f"scenario {sc.name!r} has no experiment section")
    problems = check_training_starts(sc)
    if problems:
        raise ValueError("training starts are not near their own source: " + "; ".join(problems))
    contract = Contract()
    out.parent.mkdir(parents=True, exist_ok=True)
    rows, n = [], 0
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for ph in sc.experiment.phases:
            starts = phase_starts(sc, ph)
            phase_rows = []
            for i in range(ph.trials):
                n += 1
                seed = sc.seed + n
                link = link_factory(contract)
                sim = Simulation(sc, link, contract, seed=seed, lockstep=True, start=starts[i % len(starts)],
                                 rewards=ph.rewards, reward_delay_s=sc.experiment.reward_delay_s,
                                 hold_after_reward_s=sc.experiment.hold_after_reward_s,
                                 ambient=ph.ambient, odour=ph.odour,
                                 dawn=(ph.duration_s - ph.dawn_s, ph.dawn_ambient) if ph.dawn_s else None)
                duration = ph.duration_s if ph.kind == "rest" else sc.duration_s
                wall0 = time.perf_counter()
                try:
                    timeouts = run_lockstep(sim, trial_done(duration), wait_ms=wait_ms).timeouts
                    if catch_up is not None:
                        catch_up(sim)       # while the robot slept, lockstep did not wait: let the brain catch up
                finally:
                    link.stop()
                r, sp = sim.results(), sim.start
                row = {"trial": n, "seed": seed, "scenario": sc.name, "mode": "lockstep", "controller": controller,
                       "phase": ph.name, "start_index": i % len(starts), "start_x": round(sp.x, 3),
                       "start_y": round(sp.y, 3), "start_heading_deg": round(sp.heading_deg, 1),
                       "reached": "yes" if r["reached"] else "no", "reached_label": r["reached_label"],
                       "reward_sent": r["reward_sent"], "time_to_source_s": r["time_to_source_s"],
                       "path_length_m": r["path_length_m"], "collisions": r["collisions"], "escapes": r["escapes"],
                       "fallback_time_s": r["fallback_time_s"], "messages_lost": r["messages_lost"],
                       "lockstep_timeouts": timeouts, "min_source_dist_m": r["min_source_dist_m"], "threats": r["threats"], "rtt_median_ms": None, "crtt_median_ms": None,
                       "sim_duration_s": round(sim.trial_time_s(), 2),
                       "wall_duration_s": round(time.perf_counter() - wall0, 2)}
                w.writerow({k: ("" if v is None else v) for k, v in row.items()})
                f.flush()
                rows.append(row)
                phase_rows.append(row)
                log.info("%s trial %d/%d: %s%s", ph.name, i + 1, ph.trials, row["reached_label"] or "timeout",
                         f" (reward {row['reward_sent']:+d})" if row["reward_sent"] else "")
            if on_phase_end:
                on_phase_end(ph.name, phase_rows)
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
