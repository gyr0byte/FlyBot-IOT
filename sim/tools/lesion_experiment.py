"""Lesion experiment (plan step 5.10, design E4).

    infra\\start-broker.cmd local                          (in another window)
    sim\\.venv\\Scripts\\python sim\\tools\\lesion_experiment.py
    sim\\.venv\\Scripts\\python sim\\tools\\lesion_experiment.py --report-only

- Seeking: the neural brain intact, with the mushroom body lesioned (mb) and with the receptors
  lesioned (rcpt), 50 trials each on scenarios/default.yaml (independent trials: --reset-on-reboot,
  per-trial brain seeds, base seed 1; batch lockstep wait 2 s). Expected: mb off leaves seeking
  intact, rcpt off leaves only wandering.
- Learning: 10 punish-lit animals with the mushroom body lesioned, on the 5.6 learning protocol
  (learning_experiment.py: fresh brain per animal, mirrored arena for odd animals, hunger held at 0,
  automatic sleep off). Expected: no learning (weights unchanged, valence ~0). The intact comparison
  is the punish-lit condition run on the same code and config.
Results: .local/experiments/lesions/ (trial CSVs, seeking.csv, records.csv, report.md).
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flybot_contract import Contract  # noqa: E402
from flybot_sim.__main__ import open_link  # noqa: E402
from flybot_sim.batch import run_trials  # noqa: E402
from flybot_sim.config import REPO, load_scenario  # noqa: E402
from learning_experiment import (RECORD_COLUMNS, Monitor, experiment_brain_config, run_animal,  # noqa: E402
                                 start_brain, stop_brain)

log = logging.getLogger("lesions")
SEEK = [("intact", []), ("mb", ["--lesion", "mb"]), ("rcpt", ["--lesion", "rcpt"])]
SEEK_COLUMNS = ["lesion", "n", "reached", "median_time_s", "collisions", "lockstep_timeouts"]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def run_seeking(out: Path, monitor: Monitor, trials: int) -> list[dict]:
    sc = load_scenario("default")
    rows = []
    for name, extra in SEEK:
        b = start_brain(1, out / f"brain-seek-{name}.log", monitor, None, extra=["--reset-on-reboot"] + extra)
        try:
            tr = run_trials(sc, trials, f"neural-{name}", False, out / f"seek-{name}.csv", open_link)
        finally:
            stop_brain(b)
        ok = [r for r in tr if r["reached"] == "yes"]
        rows.append({"lesion": name, "n": len(tr), "reached": len(ok),
                     "median_time_s": round(statistics.median(float(r["time_to_source_s"]) for r in ok), 1) if ok else "",
                     "collisions": round(statistics.fmean(int(r["collisions"]) for r in tr), 2),
                     "lockstep_timeouts": sum(int(r["lockstep_timeouts"] or 0) for r in tr)})
        log.info("seeking %s: reached %d/%d", name, len(ok), len(tr))
    return rows


def _num(x):
    return None if x in ("", None) else float(x)


def report(seek: list[dict], learn: list[dict]) -> str:
    out = ["# Lesions (step 5.10, design E4)", "", "## Seeking (default scenario, 50 independent trials each)", "",
           "| lesion | reached (95% CI) | median time to source | collisions per trial |", "|---|---|---|---|"]
    for r in seek:
        k, n = int(r["reached"]), int(r["n"])
        lo, hi = wilson(k, n)
        mt = f"{float(r['median_time_s']):.1f} s" if r["median_time_s"] not in ("", None) else "–"
        out.append(f"| {r['lesion']} | {k}/{n} = {k / n:.0%} ({lo:.0%}–{hi:.0%}) | {mt} | {float(r['collisions']):.2f} |")
    out += ["", "## Learning with the mushroom body lesioned (punish lit, 10 animals)", "",
            "| phase | lit / dark / timeout per animal | PI | pref.lt | pref.odr | w KC→ap | w KC→av |",
            "|---|---|---|---|---|---|---|"]
    for ph in ("pre", "train", "post"):
        rs = [r for r in learn if r["phase"] == ph]
        if not rs:
            continue
        m = lambda key: statistics.fmean(int(r[key]) for r in rs)  # noqa: E731
        f = lambda key, fmt="{:+.2f}": (lambda v: fmt.format(statistics.fmean(v)) if v else "–")(  # noqa: E731
            [x for x in (_num(r[key]) for r in rs) if x is not None])
        out.append(f"| {ph} | {m('lit'):.1f} / {m('dark'):.1f} / {m('timeout'):.1f} | {f('pi')} | {f('pref_lt')} | "
                   f"{f('pref_odr')} | {f('w_ap', '{:.3f}')} | {f('w_av', '{:.3f}')} |")
    out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--animals", type=int, default=10)
    ap.add_argument("--out", default=str(REPO / ".local" / "experiments" / "lesions"))
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("flybot_sim").setLevel(logging.WARNING)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    seek_path, rec_path = out / "seeking.csv", out / "records.csv"
    if not args.report_only:
        monitor = Monitor(Contract())
        monitor.start()
        try:
            if not seek_path.exists():
                rows = run_seeking(out, monitor, args.trials)
                with seek_path.open("w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=SEEK_COLUMNS)
                    w.writeheader()
                    w.writerows(rows)
            done = ({int(r["animal"]) for r in csv.DictReader(rec_path.open(encoding="utf-8"))}
                    if rec_path.exists() else set())
            config = str(experiment_brain_config(out))
            base = load_scenario("learning")
            new = not rec_path.exists()
            with rec_path.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=RECORD_COLUMNS)
                if new:
                    w.writeheader()
                for k in range(args.animals):
                    if k in done:
                        continue
                    t0 = time.perf_counter()
                    rows = run_animal(base, "punish_lit", k, out / "learning", monitor, config,
                                      extra=["--lesion", "mb"])
                    for r in rows:
                        w.writerow({c: ("" if r[c] is None else r[c]) for c in RECORD_COLUMNS})
                    f.flush()
                    log.info("mb-lesioned animal %d done in %.0f s", k, time.perf_counter() - t0)
        finally:
            monitor.stop()
    seek = list(csv.DictReader(seek_path.open(encoding="utf-8"))) if seek_path.exists() else []
    learn = list(csv.DictReader(rec_path.open(encoding="utf-8"))) if rec_path.exists() else []
    text = report(seek, learn)
    (out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
