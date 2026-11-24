"""Generate the DESIGNED circuit (plan steps 5.2-5.3) in circuit format v1.

    brain\\.venv\\Scripts\\python brain\\tools\\make_designed_circuit.py [--out brain/circuits/designed] [--seed 1]

Antennal lobe (5.2-5.3): receptors (rate-coded odour, one population per side) -> projection
neurons per side, with local inhibitory neurons that each side's receptors drive and that inhibit
the OPPOSITE side's projection neurons, so the stronger side suppresses the weaker one.
Mushroom body (5.5): Kenyon cells with sparse random input from projection neurons (both sides)
and from a light input, and one APL-like neuron giving global feedback inhibition, so that only a
small fraction of KCs respond to any cue combination (sparse, cue-specific patterns).
Outputs and dopamine (5.6): approach and avoid MBON groups receive plastic KC synapses; reward and
punishment DAN groups, driven by the reward topic, depress recently active KC synapses in their own
compartment (modulation.csv): reward DANs gate KC->avoid, punishment DANs gate KC->approach.
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

# Mushroom body (5.5).
N_LIGHT, N_KC = 20, 2000
PN_PER_KC = (4, 8)                 # each KC samples 4-8 random PNs (fly: ~6-7 claws on average)
CLAW_SIGMA = 0.6                   # lognormal spread of claw strengths (mean 1): KCs differ, as in flies
LIGHT_KC_FRACTION, LIGHT_PER_KC = 0.3, 3
LIGHT_BASE_HZ, LIGHT_GAIN_HZ = 1.0, 60.0
SCALE_MB = {"pn_kc": 0.30, "lgt_kc": 0.80, "kc_apl": 0.30, "apl_kc": 2.0}
LIF_KC = dict(LIF_PN, bias=0.0)

# Outputs and dopamine (5.6).
N_MBON, N_DAN, N_DAN_IN = 10, 5, 10
KC_MBON_P = 0.3                    # each KC -> each MBON with this probability
W_KC_MBON = 1.0                    # initial = maximum: learning only depresses
# Saturating eligibility: a KC spike sets its tag to 1 (a coincidence tag, not a spike count), so the
# change per dopamine pulse does not grow with KC firing rate. lr was chosen by a criterion fixed in
# advance, on weights, not behaviour: for KCs active at the pulse, ~5-10% depression after one pairing
# and ~50-70% after ten, without reaching w_min (measured: 6.5%; median 43%, 90th percentile 67%).
# The earlier accumulating trace (+1 per spike, lr 0.0003) wiped active synapses to 0 within a few
# pairings and pinned the valence at -1 for every odour (see brain/README.md, "Learning").
PLASTICITY = {"rule": "three_factor", "lr": 0.001, "tau_elig_ms": 2500.0, "w_min": 0.0, "w_max": 1.0,
              "eligibility": "saturate"}
# The earlier rule, kept reproducible for comparison runs (--rule accumulate; not the committed circuit).
PLASTICITY_ACCUMULATE = {**PLASTICITY, "lr": 0.0003, "eligibility": "accumulate"}
DAN_IN_GAIN_HZ = 200.0             # reward / punish input rate while a pulse is on

# Internal states (5.7): how hunger and fear modulate the circuit (state 0..1). Chosen by criteria
# fixed before any behaviour was measured:
# - hunger raises receptor -> PN gain (hunger signals boost olfactory input in flies): k such that
#   hunger = 1 raises the PN response to a weak odour (0.05) by ~50%; measured +53% at k = 0.15;
# - fear raises avoid-MBON excitability, hunger approach-MBON excitability (mirror image, so they
#   meet on the same valence), as a SUBTHRESHOLD bias. The target was an untrained mid-odour valence
#   of -0.5 at fear = 1. Not reachable: the largest subthreshold bias (0.8) gives -0.32, and the
#   nearer to threshold, the more the output neurons fire on the faint light-driven KC background of
#   the dark arena without odour (hunger = fear = 1, worst of 10 seeds, per neuron: 0.14 Hz per
#   group at 0.4, 0.28 at 0.6, 0.74 at 0.8; 0.06 Hz without states). 0.6 was chosen (approved):
#   valence -0.25 at fear = 1, approach + avoid at most 0.54 Hz without odour, well below the
#   controller's 2 Hz valence gate. tests/test_states.py guards it.
STATE_MODULATION = [
    {"state": "hunger", "kind": "gain", "projection": "rl_pl", "k": 0.15},
    {"state": "hunger", "kind": "gain", "projection": "rr_pr", "k": 0.15},
    {"state": "hunger", "kind": "bias", "group": "ma", "k": 0.6},
    {"state": "fear", "kind": "bias", "group": "mv", "k": 0.6},
]
SCALE_OUT = {"kc_mbon": 1.0, "dan_in": 0.15}      # DANs ~50 Hz during a reward pulse
LIF_APL = dict(LIF_PN, tau_m_ms=10, bias=0.0)


def generate(out: Path, seed: int = 1, plasticity: dict = PLASTICITY):
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
    groups += [
        {"group": "lgt", "size": N_LIGHT, "model": "poisson", "side": "-"},
        {"group": "kc", "size": N_KC, **LIF_KC, "side": "-", "spk_key": "kc", "lesion_target": "mb"},
        {"group": "apl", "size": 1, **LIF_APL, "side": "-", "spk_key": "apl", "lesion_target": "apl"},
        {"group": "ma", "size": N_MBON, **LIF_PN, "bias": 0.0, "side": "-", "spk_key": "ma"},
        {"group": "mv", "size": N_MBON, **LIF_PN, "bias": 0.0, "side": "-", "spk_key": "mv"},
        {"group": "rwd", "size": N_DAN_IN, "model": "poisson", "side": "-"},
        {"group": "pun", "size": N_DAN_IN, "model": "poisson", "side": "-"},
        {"group": "dp", "size": N_DAN, **LIF_PN, "bias": 0.0, "side": "-", "spk_key": "dp"},
        {"group": "dn", "size": N_DAN, **LIF_PN, "bias": 0.0, "side": "-", "spk_key": "dn"},
    ]

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

    # Mushroom body: PN -> KC (random claws from both sides), light -> some KCs, KC <-> APL.
    def claw():  # lognormal strength with mean 1
        return float(rng.lognormal(-CLAW_SIGMA ** 2 / 2, CLAW_SIGMA))
    for kc in range(N_KC):
        n_claws = int(rng.integers(PN_PER_KC[0], PN_PER_KC[1] + 1))
        for pn in sorted(rng.choice(2 * N_PN, n_claws, replace=False)):
            side = "l" if pn < N_PN else "r"
            conns.append((f"p{side}_kc", int(pn % N_PN), kc, claw(), "exc"))
    for kc in sorted(rng.choice(N_KC, int(LIGHT_KC_FRACTION * N_KC), replace=False)):
        for lg in sorted(rng.choice(N_LIGHT, LIGHT_PER_KC, replace=False)):
            conns.append(("lgt_kc", int(lg), int(kc), claw(), "exc"))
    conns += [("kc_apl", kc, 0, 1.0, "exc") for kc in range(N_KC)]
    conns += [("apl_kc", 0, kc, 1.0, "inh") for kc in range(N_KC)]
    # Outputs: plastic KC -> approach / avoid MBONs; reward / punish inputs -> DANs.
    for grp in ("ma", "mv"):
        for kc in range(N_KC):
            for mb in np.flatnonzero(rng.random(N_MBON) < KC_MBON_P):
                conns.append((f"kc_{grp}", kc, int(mb), W_KC_MBON, "exc"))
    for src, dan in (("rwd", "dp"), ("pun", "dn")):
        conns += [(f"{src}_{dan}", i, d, 1.0, "exc") for i in range(N_DAN_IN) for d in range(N_DAN)]
    # Modulation map: reward DANs depress KC->avoid, punishment DANs depress KC->approach.
    modulation = [{"modulator_group": "dp", "modulator_index": d, "target_group": "mv",
                   "target_from": 0, "target_to": N_MBON} for d in range(N_DAN)]
    modulation += [{"modulator_group": "dn", "modulator_index": d, "target_group": "ma",
                    "target_from": 0, "target_to": N_MBON} for d in range(N_DAN)]

    projections = []
    for side in ("l", "r"):
        other = "r" if side == "l" else "l"
        projections += [
            {"name": f"r{side}_p{side}", "pre": f"r{side}", "post": f"p{side}", "scale": SCALE["rcpt_pn"]},
            {"name": f"r{side}_ln_{side}", "pre": f"r{side}", "post": f"ln_{side}", "scale": SCALE["rcpt_ln"]},
            {"name": f"ln_{side}_p{other}", "pre": f"ln_{side}", "post": f"p{other}", "scale": SCALE["ln_pn"]},
        ]
    projections += [
        {"name": "pl_kc", "pre": "pl", "post": "kc", "scale": SCALE_MB["pn_kc"]},
        {"name": "pr_kc", "pre": "pr", "post": "kc", "scale": SCALE_MB["pn_kc"]},
        {"name": "lgt_kc", "pre": "lgt", "post": "kc", "scale": SCALE_MB["lgt_kc"]},
        {"name": "kc_apl", "pre": "kc", "post": "apl", "scale": SCALE_MB["kc_apl"]},
        {"name": "apl_kc", "pre": "apl", "post": "kc", "scale": SCALE_MB["apl_kc"]},
        {"name": "kc_ma", "pre": "kc", "post": "ma", "scale": SCALE_OUT["kc_mbon"], "plastic": True,
         "plasticity": dict(plasticity)},
        {"name": "kc_mv", "pre": "kc", "post": "mv", "scale": SCALE_OUT["kc_mbon"], "plastic": True,
         "plasticity": dict(plasticity)},
        {"name": "rwd_dp", "pre": "rwd", "post": "dp", "scale": SCALE_OUT["dan_in"]},
        {"name": "pun_dn", "pre": "pun", "post": "dn", "scale": SCALE_OUT["dan_in"]},
    ]
    manifest = {
        "name": "designed",
        "description": ("Designed circuit: olfactory front end (steps 5.2-5.3: rate-coded receptors, projection "
                        "neurons per side, cross-lateral inhibition), mushroom body (5.5: Kenyon cells with "
                        "sparse PN and light input, APL feedback inhibition) and outputs (5.6: approach / avoid "
                        "MBONs with plastic KC synapses, reward / punishment DANs), internal states (5.7: "
                        "hunger and fear modulate gains and biases)."),
        "source": {"kind": "designed"},
        "generator": {"script": "brain/tools/make_designed_circuit.py", "seed": seed},
        "designed_parts": "All of it. Sizes, wiring and weights are design choices, not connectome data.",
        "inputs": {
            "odour_left": {"group": "rl", "encoding": "poisson_rate", "base_hz": RATE_BASE_HZ, "gain_hz": RATE_GAIN_HZ},
            "odour_right": {"group": "rr", "encoding": "poisson_rate", "base_hz": RATE_BASE_HZ, "gain_hz": RATE_GAIN_HZ},
            "light": {"group": "lgt", "encoding": "poisson_rate", "base_hz": LIGHT_BASE_HZ, "gain_hz": LIGHT_GAIN_HZ},
            "reward": {"group": "rwd", "encoding": "poisson_rate", "base_hz": 0.0, "gain_hz": DAN_IN_GAIN_HZ},
            "punish": {"group": "pun", "encoding": "poisson_rate", "base_hz": 0.0, "gain_hz": DAN_IN_GAIN_HZ},
        },
        "outputs": {"steer_left": ["pl"], "steer_right": ["pr"], "approach": ["ma"], "avoid": ["mv"]},
        "projections": projections,
        "state_modulation": [dict(sm) for sm in STATE_MODULATION],
    }
    return write_circuit(out, manifest, groups, conns, modulation=modulation)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPO / "brain" / "circuits" / "designed"))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rule", choices=["saturate", "accumulate"], default="saturate",
                    help="KC->MBON eligibility rule (accumulate = the earlier rule, for comparison runs only)")
    a = ap.parse_args()
    c = generate(Path(a.out), a.seed, PLASTICITY if a.rule == "saturate" else PLASTICITY_ACCUMULATE)
    print(f"wrote {a.out}: {c.n_neurons} neurons, {c.n_connections} connections, "
          f"{len(c.projections)} projections")
    return 0


if __name__ == "__main__":
    sys.exit(main())
