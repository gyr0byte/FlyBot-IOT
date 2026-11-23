"""FlyBot simulator command line. Run from the repo root:

    sim\\.venv\\Scripts\\python -m flybot_sim live  [--scenario default] [--start 0]
    sim\\.venv\\Scripts\\python -m flybot_sim run   [--scenario default] [--start 0] [--lockstep]
    sim\\.venv\\Scripts\\python -m flybot_sim batch --trials 20 --controller baseline [--realtime]

(`python -m` needs sim\\ on the path: the commands above work from the sim folder, or set
PYTHONPATH=sim from the repo root.) The broker must be running (infra\\start-broker.cmd local).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from .config import MqttSettings, load_scenario
from .contract import Contract
from .link import Link
from .runner import run_lockstep, run_realtime, trial_done
from .sim import Simulation

log = logging.getLogger("flybot_sim")


def open_link(contract: Contract) -> Link:
    link = Link(MqttSettings.from_env("SIM"), contract)
    if not link.start():
        link.stop()
        raise SystemExit("cannot connect to the broker as user 'sim': is it running? "
                         "(infra\\start-broker.cmd local)")
    return link


def cmd_run(args) -> int:
    sc = load_scenario(args.scenario)
    if args.duration:
        sc.duration_s = args.duration
    contract = Contract()
    link = open_link(contract)
    sim = Simulation(sc, link, contract, start_index=args.start, seed=args.seed, lockstep=args.lockstep)
    wall0 = time.perf_counter()
    try:
        if args.lockstep:
            st = run_lockstep(sim, trial_done(sc.duration_s))
            log.info("lockstep: %d waits, %d matched, %d timeouts", st.waits, st.matched, st.timeouts)
        else:
            run_realtime(sim, trial_done(sc.duration_s))
    finally:
        link.stop()
    res = sim.results()
    res["sim_s"] = round(sim.t_ms / 1000, 2)
    res["wall_s"] = round(time.perf_counter() - wall0, 2)
    print(json.dumps(res, indent=2))
    return 0


def cmd_live(args) -> int:
    from .view import run_live
    return run_live(args)


def cmd_batch(args) -> int:
    from .batch import run_batch
    return run_batch(args)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m flybot_sim", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--scenario", default="default", help="name in sim/scenarios or a path to a .yaml")
        p.add_argument("--seed", type=int, help="override the scenario seed")
        p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("live", help="real-time run with the pygame view")
    common(p)
    p.add_argument("--start", type=int, default=0, help="index into the scenario's start positions")
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("run", help="one headless trial, results as JSON")
    common(p)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--lockstep", action="store_true", help="faster than real time (contract 7.1)")
    p.add_argument("--duration", type=float, help="override the scenario duration (s)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("batch", help="N headless trials -> CSV")
    common(p)
    p.add_argument("--trials", type=int, default=10)
    p.add_argument("--controller", default="unknown", help="label written to the CSV (e.g. baseline, neural)")
    p.add_argument("--realtime", action="store_true", help="run in real time (default: lockstep)")
    p.add_argument("--duration", type=float, help="override the scenario duration per trial (s)")
    p.add_argument("--out", help="CSV path (default sim/results/<scenario>-<mode>-<time>.csv)")
    p.set_defaults(func=cmd_batch)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
