"""Looming habituation curve and recovery (plan step 5.8, design E2). In-process, no brain.

    sim\\.venv\\Scripts\\python sim\\tools\\habituation_curve.py

The robot stands still in manual mode (reflexes stay on) in the middle of an empty arena and is put
back to the same pose before each stimulus. A stimulus is a looming object launched 0.75 m in
front of it (as with the live view's L key).
- curve: 15 identical stimuli (0.7 m/s, the slowest class) 5 s apart;
- recovery: fresh robots habituated the same way, then rest 10, 30, 60 or 120 s, then one stimulus;
- dishabituation: over 20 seeds, after the 15 slow stimuli one faster stimulus (1.5 or 2.5 m/s):
  how often it gets the full response (dishabituates), and how often an identical slow stimulus
  (4th-15th) falsely does. A fast object gives only ~3 readings before detection, so its speed
  estimate is noisy; the rule dishabituates at >= 1.5 x the recent stimuli's speed.
Writes .local/experiments/habituation/curve.csv and report.md.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flybot_contract import Contract  # noqa: E402
from flybot_sim.config import REPO, StartPos, load_scenario  # noqa: E402
from flybot_sim.link import FakeLink  # noqa: E402
from flybot_sim.sim import PHYSICS_MS, Simulation  # noqa: E402

C = Contract("01")
SLOW = 0.7                       # m/s
ISI_S = 5.0
N_HAB = 15
RESTS_S = (10, 30, 60, 120)
DISHAB_SEEDS = 20
DISHAB_SPEEDS = (1.5, 2.5)


class Rig:
    def __init__(self, seed: int = 1):
        sc = load_scenario("default")
        sc.obstacles, sc.threats, sc.sources = [], [], sc.sources[:1]
        self.sc = sc
        self.link = FakeLink(C)
        self.sim = Simulation(sc, self.link, C, seed=seed, start=StartPos(0.6, 1.0, 0.0))
        self.link.send("cmd/mode", {"v": 2, "seq": 0, "up": 0, "m": "manual", "src": "dash-000001"}, retained=True)
        self.wait(8.0)                                   # boot, odour warm-up, manual mode

    def wait(self, seconds: float) -> None:
        for _ in range(int(seconds * 1000) // PHYSICS_MS):
            self.sim.step()

    def stimulus(self, speed: float, then_s: float = ISI_S) -> dict | None:
        p = self.sim.robot.pose
        p.x, p.y, p.theta = 0.6, 1.0, 0.0
        self.sc.looming.speed_mps = speed
        before = len(self.link.published)
        self.sim.launch_looming()
        self.wait(then_s)
        ev = [m for n, m in self.link.published[before:] if n == "events" and m["e"] == "escape"]
        return ev[0] if ev else None


def resp(ev):
    return None if ev is None else ev["resp"]


def main() -> int:
    out = REPO / ".local" / "experiments" / "habituation"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    rig = Rig()
    curve = []
    for i in range(N_HAB):
        ev = rig.stimulus(SLOW)
        curve.append(ev)
        rows.append({"part": "curve", "stimulus": i + 1, "rest_s": "", "speed_mps": SLOW, "resp": resp(ev),
                     "hab": None if ev is None else ev["hab"], "level_after": round(rig.sim.edge.hab_level(), 3)})
    dishab = {v: 0 for v in DISHAB_SPEEDS}
    restored = {v: [] for v in DISHAB_SPEEDS}
    false_d = slow_n = 0
    for seed in range(DISHAB_SEEDS):
        for v in DISHAB_SPEEDS:
            r = Rig(seed=seed)
            for i in range(N_HAB):
                ev = r.stimulus(SLOW)
                if i >= 3 and ev is not None:
                    slow_n += 1
                    false_d += ev["resp"] == 1.0
            ev = r.stimulus(v)
            hit = ev is not None and ev["resp"] == 1.0
            dishab[v] += hit
            after = r.stimulus(SLOW)
            if hit and after is not None:
                restored[v].append(after["resp"])
            rows.append({"part": "dishabituation", "stimulus": f"seed {seed}", "rest_s": "", "speed_mps": v,
                         "resp": resp(ev), "hab": None if ev is None else ev["hab"], "level_after": resp(after)})
    recovery = {}
    for rest in RESTS_S:
        r = Rig()
        last = None
        for _ in range(N_HAB):
            last = r.stimulus(SLOW)
        r.wait(rest - ISI_S)                             # the last stimulus already waited ISI_S
        ev = r.stimulus(SLOW, then_s=2.0)
        recovery[rest] = (resp(last), resp(ev))
        rows.append({"part": "recovery", "stimulus": "test", "rest_s": rest, "speed_mps": SLOW, "resp": resp(ev),
                     "hab": None if ev is None else ev["hab"], "level_after": ""})

    with (out / "curve.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows({k: ("" if v is None else v) for k, v in r.items()} for r in rows)

    lines = ["# Looming habituation (step 5.8, design E2)", "",
             f"Robot still, stimuli {SLOW} m/s from 0.75 m, {ISI_S:g} s apart. `resp` = escape strength "
             "(1 = full, 0 = detected but no movement); missing = not detected.", "",
             "| stimulus | " + " | ".join(str(i + 1) for i in range(N_HAB)) + " |",
             "|---|" + "---|" * N_HAB,
             "| resp | " + " | ".join("–" if e is None else f"{e['resp']:.2f}" for e in curve) + " |", "",
             "| recovery: rest before the test | last habituated response | response after the rest |",
             "|---|---|---|"]
    for rest, (last, after) in recovery.items():
        lines.append(f"| {rest} s | {last if last is not None else '–'} | {after if after is not None else '–'} |")
    lines += ["", f"Dishabituation over {DISHAB_SEEDS} seeds, after {N_HAB} slow stimuli:", "",
              "| faster stimulus | full response (dishabituated) | next slow stimulus, when dishabituated (mean resp) |",
              "|---|---|---|"]
    for v in DISHAB_SPEEDS:
        m = sum(restored[v]) / len(restored[v]) if restored[v] else float("nan")
        lines.append(f"| {v} m/s | {dishab[v]}/{DISHAB_SEEDS} | {m:.2f} |")
    lines += ["", f"False dishabituation: an identical slow stimulus (4th to 15th) got the full response in "
              f"{false_d}/{slow_n} cases.", ""]
    text = "\n".join(lines)
    (out / "report.md").write_text(text, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
