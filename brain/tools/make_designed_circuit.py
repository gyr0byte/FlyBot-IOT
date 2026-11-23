"""Generate the DESIGNED circuit (plan steps 5.2-5.3) in circuit format v1.

    brain\\.venv\\Scripts\\python brain\\tools\\make_designed_circuit.py [--out brain/circuits/designed] [--seed 1]

Receptors (rate-coded odour, one population per side) -> projection neurons per side, with local
inhibitory neurons that each side's receptors drive and that inhibit the OPPOSITE side's projection
neurons, so the stronger side suppresses the weaker one (contrast enhancement).
Later steps (mushroom body, outputs, dopamine) extend this generator.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flybot_brain.network import write_circuit  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

# Designed parameters. Weights are in "designed strength" units (1 = one standard synapse);
# each projection's scale turns them into synaptic input.
N_RCPT, N_PN, N_LN = 50, 25, 10
RCPT_PER_PN, RCPT_PER_LN = 12, 15
RATE_BASE_HZ, RATE_GAIN_HZ = 2.0, 150.0
SCALE = {"rcpt_pn": 0.60, "rcpt_ln": 0.40, "ln_pn": 0.12}
# Resting bias at half threshold: PNs respond from odour ~0.03 (far field) but are silent in clean air.
LIF_PN = dict(model="lif", tau_m_ms=20, v_thresh=1.0, v_reset=0.0, t_ref_ms=2, tau_syn_ms=5, bias=0.5, noise=0.0)
LIF_LN = dict(LIF_PN, tau_m_ms=10)


def generate(out: Path, seed: int = 1):
    rng = np.random.default_rng(seed)
    groups = []
    for side, s in (("l", "L"), ("r", "R")):
        groups.append({"group": f"r{side}", "size": N_RCPT, "model": "poisson", "side": s,
                       "spk_key": f"r{side}", "lesion_target": "rcpt"})
    for side, s in (("l", "L"), ("r", "R")):
        groups.append({"group": f"p{side}", "size": N_PN, **LIF_PN, "side": s, "spk_key": f"p{side}"})
    for side, s in (("l", "L"), ("r", "R")):
        groups.append({"group": f"ln_{side}", "size": N_LN, **LIF_LN, "side": s, "spk_key": "ln",
                       "lesion_target": "ln"})

    conns = []
    for side in ("l", "r"):
        other = "r" if side == "l" else "l"
        for pn in range(N_PN):                                  # receptors -> same-side PNs
            for rc in sorted(rng.choice(N_RCPT, RCPT_PER_PN, replace=False)):
                conns.append((f"r{side}_p{side}", int(rc), pn, 1.0, "exc"))
        for ln in range(N_LN):                                  # receptors -> same-side LNs
            for rc in sorted(rng.choice(N_RCPT, RCPT_PER_LN, replace=False)):
                conns.append((f"r{side}_ln_{side}", int(rc), ln, 1.0, "exc"))
        for ln in range(N_LN):                                  # LNs -> OPPOSITE-side PNs (all)
            for pn in range(N_PN):
                conns.append((f"ln_{side}_p{other}", ln, pn, 1.0, "inh"))

    projections = []
    for side in ("l", "r"):
        other = "r" if side == "l" else "l"
        projections += [
            {"name": f"r{side}_p{side}", "pre": f"r{side}", "post": f"p{side}", "scale": SCALE["rcpt_pn"]},
            {"name": f"r{side}_ln_{side}", "pre": f"r{side}", "post": f"ln_{side}", "scale": SCALE["rcpt_ln"]},
            {"name": f"ln_{side}_p{other}", "pre": f"ln_{side}", "post": f"p{other}", "scale": SCALE["ln_pn"]},
        ]
    manifest = {
        "name": "designed",
        "description": ("Designed olfactory front end (plan steps 5.2-5.3): rate-coded receptors per side, "
                        "projection neurons per side, cross-lateral inhibition by local neurons."),
        "source": {"kind": "designed"},
        "generator": {"script": "brain/tools/make_designed_circuit.py", "seed": seed},
        "designed_parts": "All of it. Sizes, wiring and weights are design choices, not connectome data.",
        "inputs": {
            "odour_left": {"group": "rl", "encoding": "poisson_rate", "base_hz": RATE_BASE_HZ, "gain_hz": RATE_GAIN_HZ},
            "odour_right": {"group": "rr", "encoding": "poisson_rate", "base_hz": RATE_BASE_HZ, "gain_hz": RATE_GAIN_HZ},
        },
        "outputs": {"steer_left": ["pl"], "steer_right": ["pr"]},
        "projections": projections,
    }
    return write_circuit(out, manifest, groups, conns)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPO / "brain" / "circuits" / "designed"))
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    c = generate(Path(a.out), a.seed)
    print(f"wrote {a.out}: {c.n_neurons} neurons, {c.n_connections} connections, "
          f"{len(c.projections)} projections")
    return 0


if __name__ == "__main__":
    sys.exit(main())
