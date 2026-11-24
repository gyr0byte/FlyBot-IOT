"""Firing rates of FlyBot's own engine on the designed circuit, for the Brian2 comparison (step 5.12).

    brain\\.venv\\Scripts\\python analysis\\brian2\\engine_rates.py

Runs the receptor -> antennal lobe -> mushroom body path (the whole circuit is simulated; output and
dopamine neurons do not feed back into this path) for fixed inputs and writes mean rates per group
and the fraction of active Kenyon cells to .local/experiments/brian2/engine.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "brain"))

from flybot_brain.network import Network, load_circuit  # noqa: E402

GROUPS = ["rl", "rr", "pl", "pr", "ln_l", "ln_r", "lgt", "kc", "apl"]
CASES = {"weak odour, dark": (0.05, 0.02, 0.05), "odour left, dark": (0.3, 0.2, 0.05),
         "strong odour, lamp": (0.6, 0.6, 0.8), "lamp only": (0.0, 0.0, 0.8)}
SEEDS, SETTLE_MS, RUN_MS = 10, 500, 5000


def measure(circuit, ol, orr, lt, seed):
    net = Network(circuit, seed=seed)
    net.set_input("odour_left", ol); net.set_input("odour_right", orr); net.set_input("light", lt)
    net.run(SETTLE_MS)
    total = net.run(RUN_MS)
    rates = {g: float(total[net.group_names.index(g)]) / circuit.groups[g].size / (RUN_MS / 1000) for g in GROUPS}
    return rates


def kc_active_fraction(circuit, ol, orr, lt, seed):
    """Fraction of KCs that fire at least once in RUN_MS (counted per neuron)."""
    net = Network(circuit, seed=seed)
    net.set_input("odour_left", ol); net.set_input("odour_right", orr); net.set_input("light", lt)
    net.run(SETTLE_MS)
    kc = net.offset["kc"]
    fired = np.zeros(2000, dtype=bool)
    for _ in range(RUN_MS):
        net.run(1)
        s = net.spikes
        s = s[(s >= kc) & (s < kc + 2000)] - kc
        fired[s] = True
    return float(fired.mean())


def main() -> int:
    circuit = load_circuit(REPO / "brain" / "circuits" / "designed")
    out = {"seeds": SEEDS, "run_ms": RUN_MS, "cases": {}}
    for name, (ol, orr, lt) in CASES.items():
        per_seed = [measure(circuit, ol, orr, lt, s) for s in range(SEEDS)]
        frac = [kc_active_fraction(circuit, ol, orr, lt, s) for s in range(3)]
        out["cases"][name] = {"inputs": [ol, orr, lt],
                              "rates": {g: [r[g] for r in per_seed] for g in GROUPS},
                              "kc_active": frac}
        print(name, {g: round(float(np.mean([r[g] for r in per_seed])), 2) for g in GROUPS},
              "kc active", round(float(np.mean(frac)), 3))
    path = REPO / ".local" / "experiments" / "brian2" / "engine.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
