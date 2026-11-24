"""Brian2 rebuild of the designed circuit's receptor -> antennal lobe -> mushroom body path, compared
with FlyBot's own engine (plan step 5.12, optional).

    brain\\.venv\\Scripts\\python analysis\\brian2\\engine_rates.py      (first: the engine's rates)
    analysis\\.venv\\Scripts\\python analysis\\brian2\\brian2_rates.py

The circuit is read from its files (groups.csv, connections.csv, manifest.yaml), independently of
the engine's code. The model is the engine's, written as Brian2 equations with the same update order:
  - each 1 ms step the synaptic current decays, I <- I * exp(-dt / tau_syn), then v is updated by
    Euler, v <- v + dt / tau_m * (-v + I + bias), and v >= v_thresh spikes (reset, refractory t_ref);
  - a spike raises the target's I in the same step it is emitted, by w / exp(-dt / tau_syn), so that
    after the next step's decay it contributes exactly w, as the engine's 1-step delay does;
  - Poisson inputs fire with probability rate x dt per step;
  - refractory: the engine holds a neuron at reset for t_ref_ms steps AFTER the step it spiked in, so
    its effective refractory period is t_ref_ms + 1 ms. Brian2's refractory counts from the spike, so
    it is set to t_ref_ms + 1 ms. This convention was found by this check: with refractory = t_ref_ms
    the fast local neurons fired 10-22% faster in Brian2 (approved: keep the engine, document it).
Pass criterion (fixed in advance): every group's mean rate within 10% of the engine's (groups firing
below 1 Hz are compared in absolute terms: within 0.1 Hz). Output and dopamine neurons are left out:
nothing feeds back from them into this path. Writes .local/experiments/brian2/report.md.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[2]
CIRCUIT = REPO / "brain" / "circuits" / "designed"
OUT = REPO / ".local" / "experiments" / "brian2"

import brian2 as b2  # noqa: E402

b2.prefs.codegen.target = "numpy"           # no compiler, no cache outside D:
b2.prefs.logging.file_log = False

PATH = ["rl", "rr", "pl", "pr", "ln_l", "ln_r", "lgt", "kc", "apl"]
KIND = {"exc": 1.0, "inh": -1.0}


def load():
    m = yaml.safe_load((CIRCUIT / "manifest.yaml").read_text(encoding="utf-8"))
    groups = {r["group"]: r for r in csv.DictReader((CIRCUIT / "groups.csv").open(encoding="utf-8"))}
    scale = {p["name"]: (p["pre"], p["post"], float(p["scale"])) for p in m["projections"]}
    edges: dict[str, list[tuple[int, int, float]]] = {}
    for r in csv.DictReader((CIRCUIT / "connections.csv").open(encoding="utf-8")):
        pre, post, sc = scale[r["projection"]]
        if pre in PATH and post in PATH and r["type"] in KIND:
            edges.setdefault(r["projection"], []).append((int(r["pre"]), int(r["post"]),
                                                          float(r["weight"]) * sc * KIND[r["type"]]))
    return m, groups, scale, edges


def simulate(ol, orr, lt, seed, settle_ms, run_ms):
    m, groups, scale, edges = load()
    b2.start_scope()
    b2.defaultclock.dt = 1 * b2.ms
    b2.seed(seed)
    inputs = {b["group"]: (b["base_hz"], b["gain_hz"], v) for name, b in m["inputs"].items()
              for v in [{"odour_left": ol, "odour_right": orr, "light": lt}.get(name)] if v is not None}
    nodes, monitors = {}, {}
    for g in PATH:
        row = groups[g]
        n = int(row["size"])
        if row["model"] == "poisson":
            base, gain, value = inputs[g]
            nodes[g] = b2.PoissonGroup(n, rates=max(0.0, base + gain * value) * b2.Hz, name=g)
        else:
            tau_m = float(row["tau_m_ms"]) * b2.ms
            decay = math.exp(-1.0 / float(row["tau_syn_ms"]))
            grp = b2.NeuronGroup(
                n, "dv/dt = (-v + I + bias) / tau_m : 1 (unless refractory)\nI : 1\nbias : 1\ntau_m : second",
                threshold=f"v >= {float(row['v_thresh'])}", reset=f"v = {float(row['v_reset'])}",
                refractory=(float(row["t_ref_ms"]) + 1.0) * b2.ms,     # engine convention, see the docstring
                method="euler", name=g)
            grp.bias = float(row["bias"])
            grp.tau_m = tau_m
            grp.run_regularly(f"I = I * {decay!r}", dt=1 * b2.ms, when="start")
            nodes[g] = grp
        monitors[g] = b2.SpikeMonitor(nodes[g], record=(g == "kc"))
    syns = []
    for name, es in edges.items():
        pre, post, _ = scale[name]
        decay = math.exp(-1.0 / float(groups[post]["tau_syn_ms"]))
        s = b2.Synapses(nodes[pre], nodes[post], "w : 1", on_pre="I_post += w", name=f"s_{name}")
        i, j, w = zip(*es)
        s.connect(i=np.array(i), j=np.array(j))
        s.w = np.array(w) / decay
        syns.append(s)
    net = b2.Network(*nodes.values(), *syns)
    net.run(settle_ms * b2.ms)                          # settle without monitors ...
    net.add(*monitors.values())                         # ... then count only the measured window
    net.run(run_ms * b2.ms)
    rates = {g: monitors[g].num_spikes / int(groups[g]["size"]) / (run_ms / 1000) for g in PATH}
    kc_fired = np.unique(np.asarray(monitors["kc"].i[:]))
    return rates, len(kc_fired) / int(groups["kc"]["size"])


def main() -> int:
    eng = json.loads((OUT / "engine.json").read_text(encoding="utf-8"))
    seeds = eng["seeds"]
    lines = ["# Brian2 check (step 5.12)", "",
             f"Designed circuit, receptor -> antennal lobe -> mushroom body. {seeds} seeds x {eng['run_ms'] / 1000:g} s "
             "per case, both simulators. Criterion: group mean within 10% (below 1 Hz: within 0.1 Hz).", ""]
    all_pass = True
    for case, d in eng["cases"].items():
        ol, orr, lt = d["inputs"]
        runs = [simulate(ol, orr, lt, s, 500, eng["run_ms"]) for s in range(seeds)]
        lines += [f"## {case} (odour {ol:g} / {orr:g}, light {lt:g})", "",
                  "| group | engine (Hz) | Brian2 (Hz) | difference | pass |", "|---|---|---|---|---|"]
        for g in PATH:
            e = float(np.mean(d["rates"][g]))
            b = float(np.mean([r[0][g] for r in runs]))
            ok = abs(b - e) <= 0.1 if max(e, b) < 1.0 else abs(b - e) <= 0.1 * e
            all_pass &= ok
            diff = f"{b - e:+.2f} Hz" if max(e, b) < 1.0 else f"{100 * (b - e) / e:+.1f}%"
            lines.append(f"| {g} | {e:.2f} | {b:.2f} | {diff} | {'yes' if ok else '**no**'} |")
        ka_e, ka_b = float(np.mean(d["kc_active"])), float(np.mean([r[1] for r in runs]))
        lines += ["", f"Kenyon cells active at least once: engine {ka_e:.1%}, Brian2 {ka_b:.1%}.", ""]
        print(case, "done")
    lines.append(f"**Overall: {'all groups within the criterion' if all_pass else 'some groups outside the criterion'}.**")
    text = "\n".join(lines) + "\n"
    (OUT / "report.md").write_text(text, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
