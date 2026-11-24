"""Learning experiment (plan step 5.6): pre-test, training, post-test, per animal and condition.

    infra\\start-broker.cmd local                          (in another window)
    sim\\.venv\\Scripts\\python sim\\tools\\learning_experiment.py --animals 10
    sim\\.venv\\Scripts\\python sim\\tools\\learning_experiment.py --report-only

For each condition (punish_lit, reward_lit, control, delayed_punish) and animal k, it starts a
fresh brain process (--controller neural --no-persistence --seed k+1, no --reset-on-reboot, so the
animal keeps what it learns across trials), runs the scenario's phases in lockstep, and records,
at the end of each phase, the brain's learned valence per cue (brain/state pref.lt, pref.odr) and
the mean KC->MBON weights (brain/weights), read over a dashboard-user MQTT connection.

Results go to .local/experiments/learning/ (git-ignored): per-animal trial CSVs, brain logs,
records.csv (one row per animal and phase) and report.md. Animals already in records.csv are
skipped, so a run can be resumed or extended (--first-animal).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flybot_contract import Contract  # noqa: E402
from flybot_sim.__main__ import open_link  # noqa: E402
from flybot_sim.batch import run_experiment  # noqa: E402
from flybot_sim.config import REPO, MqttSettings, check_training_starts, load_scenario, phase_starts, source_by_label  # noqa: E402
from flybot_sim.learning import CONDITIONS, TRAIN_PHASE, animal_scenario, report, summarize_phase, weight_means  # noqa: E402

log = logging.getLogger("learning")
RECORD_COLUMNS = ["condition", "animal", "mirrored", "brain_seed", "phase", "n", "lit", "dark", "timeout", "rewards",
                  "pi", "pref_lt", "pref_odr", "w_ap", "w_av", "lockstep_timeouts"]
BRAIN_PY = REPO / "brain" / ".venv" / "Scripts" / "python.exe"


class Monitor:
    """Watches brain/status, brain/state and brain/weights as the dashboard user (read only)."""

    def __init__(self, contract: Contract):
        self.c = contract
        self.lock = threading.Lock()
        self.state: dict | None = None
        self.weights: dict | None = None
        self.online_at = 0.0               # wall time of the last "online" brain/status
        s = MqttSettings.from_env("DASHBOARD")
        self.client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"flybot-{s.robot_id}-learning-monitor",
                                  protocol=mqtt.MQTTv311, clean_session=True)
        self.client.username_pw_set(s.user, s.password)
        self.client.on_connect = lambda cl, *_: [cl.subscribe(contract.topic(n), 1)
                                                 for n in ("brain/status", "brain/state", "brain/weights")]
        self.client.on_message = self._on_message
        self.settings = s

    def start(self) -> None:
        self.client.connect(self.settings.host, self.settings.port, keepalive=10)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()

    def _on_message(self, client, userdata, m):
        name = self.c.name_of(m.topic)
        try:
            msg = json.loads(m.payload)
        except ValueError:
            return
        with self.lock:
            if name == "brain/state":
                self.state = msg
            elif name == "brain/weights":
                self.weights = msg
            elif name == "brain/status" and msg.get("st") == "online" and not m.retain:
                self.online_at = time.monotonic()

    def reset(self) -> None:
        with self.lock:
            self.state = self.weights = None

    def snapshot(self) -> tuple[dict, dict | None]:
        with self.lock:
            return dict((self.state or {}).get("pref") or {}), self.weights


def start_brain(seed: int, logfile: Path, monitor: Monitor, config: str | None,
                extra: list[str] | None = None) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPYCACHEPREFIX": os.environ.get("PYTHONPYCACHEPREFIX", r"D:\Tools\cache\pycache")}
    cmd = [str(BRAIN_PY), "-B", "-m", "flybot_brain", "--controller", "neural", "--no-persistence", "--seed", str(seed)]
    if config:
        cmd += ["--config", str(Path(config).resolve())]
    cmd += extra or []
    t0 = time.monotonic()
    f = logfile.open("w", encoding="utf-8")
    p = subprocess.Popen(cmd, cwd=REPO / "brain", stdout=f, stderr=subprocess.STDOUT, env=env)
    p._flybot_log = f
    while monitor.online_at < t0:
        if p.poll() is not None:
            raise SystemExit(f"brain exited at start-up; see {logfile}")
        if time.monotonic() - t0 > 20:
            stop_brain(p)
            raise SystemExit(f"brain did not come online in 20 s; see {logfile}")
        time.sleep(0.05)
    return p


def stop_brain(p: subprocess.Popen) -> None:
    p.terminate()
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()
    p._flybot_log.close()


def experiment_brain_config(out: Path, circuit: str | None = None) -> Path:
    """brain.yaml with brain/weights published at most every 250 ms (not 2 s) of robot time, so the
    weights read at the end of a phase include the last reward pulse (the 2 s hold covers it)."""
    import yaml
    cfg = yaml.safe_load((REPO / "brain" / "config" / "brain.yaml").read_text(encoding="utf-8"))
    cfg.setdefault("timing", {})["weights_every_ms"] = 250
    if circuit:
        cfg.setdefault("neural", {})["circuit"] = str(Path(circuit).resolve())
    p = out / "brain-experiment.yaml"
    p.write_text("# generated by sim/tools/learning_experiment.py from brain/config/brain.yaml\n"
                 + yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def load_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run_animal(base, condition: str, k: int, out: Path, monitor: Monitor, config: str | None) -> list[dict]:
    sc, mirrored = animal_scenario(base, condition, k)
    problems = check_training_starts(sc)
    if problems:
        raise SystemExit("; ".join(problems))
    lit = source_by_label(sc, "lit")
    train = next(p for p in sc.experiment.phases if p.name == TRAIN_PHASE)
    starts = ", ".join(f"({s.x:.2f}, {s.y:.2f})" for s in phase_starts(sc, train))
    log.info("%s animal %d: lamp %s (lit source at %.2f, %.2f); training starts %s",
             condition, k, "right" if mirrored else "left", lit.x, lit.y, starts)

    folder = out / condition
    folder.mkdir(parents=True, exist_ok=True)
    seed = k + 1
    monitor.reset()
    brain = start_brain(seed, folder / f"brain-a{k:02d}.log", monitor, config)
    records = []

    def on_phase_end(phase: str, rows: list[dict]) -> None:
        time.sleep(0.3)                       # let the last brain/state arrive
        pref, weights = monitor.snapshot()
        s = summarize_phase(rows)
        w_ap, w_av = weight_means(weights)
        records.append({"condition": condition, "animal": k, "mirrored": mirrored, "brain_seed": seed,
                        "phase": phase, "n": s.n, "lit": s.lit, "dark": s.dark, "timeout": s.timeout,
                        "rewards": s.rewards, "pi": None if s.pi is None else round(s.pi, 3),
                        "pref_lt": pref.get("lt"), "pref_odr": pref.get("odr"), "w_ap": w_ap, "w_av": w_av,
                        "lockstep_timeouts": sum(int(r["lockstep_timeouts"] or 0) for r in rows)})
        log.info("  %-5s lit %d dark %d timeout %d  PI %s  pref.lt %s pref.odr %s  w ap %s av %s", phase, s.lit,
                 s.dark, s.timeout, s.pi if s.pi is None else f"{s.pi:+.2f}", pref.get("lt"), pref.get("odr"),
                 w_ap, w_av)

    try:
        run_experiment(sc, "neural", folder / f"trials-a{k:02d}.csv", open_link, on_phase_end)
    finally:
        stop_brain(brain)
    return records


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    ap.add_argument("--animals", type=int, default=10, help="animals per condition")
    ap.add_argument("--first-animal", type=int, default=0)
    ap.add_argument("--scenario", default="learning")
    ap.add_argument("--brain-config", help="brain YAML (default: brain/config/brain.yaml with weights every 250 ms)")
    ap.add_argument("--circuit", help="circuit folder for the brain (default: the config's, brain/circuits/designed)")
    ap.add_argument("--out", default=str(REPO / ".local" / "experiments" / "learning"))
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("flybot_sim").setLevel(logging.WARNING)

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rec_path = out / "records.csv"
    if not args.brain_config:
        args.brain_config = str(experiment_brain_config(out, args.circuit))
    elif args.circuit:
        raise SystemExit("--circuit works with the generated config; set neural.circuit in --brain-config instead")
    records = load_records(rec_path)
    if not args.report_only:
        base = load_scenario(args.scenario)
        done = {(r["condition"], int(r["animal"])) for r in records}
        monitor = Monitor(Contract())
        monitor.start()
        new_file = not rec_path.exists()
        try:
            with rec_path.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=RECORD_COLUMNS)
                if new_file:
                    w.writeheader()
                conditions = args.conditions.split(",")
                for condition in conditions:
                    if condition not in CONDITIONS:
                        raise SystemExit(f"unknown condition {condition!r}; known: {', '.join(CONDITIONS)}")
                # Interleaved: animal k runs in every condition before animal k + 1, so the time of
                # the run (host load, sleep) cannot line up with a condition.
                for k in range(args.first_animal, args.first_animal + args.animals):
                    for condition in conditions:
                        if (condition, k) in done:
                            continue
                        t0 = time.perf_counter()
                        rows = run_animal(base, condition, k, out, monitor, args.brain_config)
                        for r in rows:
                            w.writerow({c: ("" if r[c] is None else r[c]) for c in RECORD_COLUMNS})
                        f.flush()
                        records += [{c: ("" if r[c] is None else str(r[c])) for c in RECORD_COLUMNS} for r in rows]
                        log.info("%s animal %d done in %.0f s", condition, k, time.perf_counter() - t0)
        finally:
            monitor.stop()
    text = report(records)
    (out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
