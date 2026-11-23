"""Mushroom body in the designed circuit (plan step 5.5): sparse, cue-specific Kenyon cell patterns.

Done when different cue combinations (odour in light, odour in dark, light only) give clearly
different, sparse KC patterns. Measured here: fraction of KCs active in 1 s, overlap (Jaccard)
between cue patterns, and overlap between repeats of the same cue (different noise seeds).
"""
from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pytest

from flybot_brain.network import Network, load_circuit

DESIGNED = Path(__file__).resolve().parents[2] / "brain" / "circuits" / "designed"
CUES = {"odour+light": (0.4, 0.4, 0.8), "odour+dark": (0.4, 0.4, 0.05), "light only": (0.0, 0.0, 0.8)}


@pytest.fixture(scope="module")
def circuit():
    return load_circuit(DESIGNED)


def kc_pattern(circuit, cue, seed, no_apl=False, ms=1000):
    net = Network(circuit, seed=seed)
    if no_apl:
        net.e_w[net.e_proj == net.projection_names.index("apl_kc")] = 0.0
    ol, orr, lt = cue
    net.set_input("odour_left", ol)
    net.set_input("odour_right", orr)
    net.set_input("light", lt)
    net.run(300)
    o, n = net.offset["kc"], circuit.groups["kc"].size
    counts = np.zeros(n)
    for _ in range(ms):
        net.run(1)
        s = net.spikes
        counts[s[(s >= o) & (s < o + n)] - o] += 1
    return counts


def jaccard(a, b):
    a, b = a > 0, b > 0
    return (a & b).sum() / max((a | b).sum(), 1)


@pytest.fixture(scope="module")
def patterns(circuit):
    return {k: [kc_pattern(circuit, cue, seed) for seed in (1, 2)] for k, cue in CUES.items()}


def test_size_and_wiring(circuit):
    assert circuit.groups["kc"].size == 2000 and circuit.groups["apl"].size == 1
    pn_claws = np.bincount(np.concatenate([circuit.projections[p].post_idx for p in ("pl_kc", "pr_kc")]), minlength=2000)
    assert pn_claws.min() >= 4 and pn_claws.max() <= 8          # 4-8 PN claws per KC
    assert "light" in circuit.inputs


@pytest.mark.parametrize("cue", list(CUES))
def test_each_cue_activates_a_sparse_set_of_kcs(patterns, cue):
    active = (patterns[cue][0] > 0).mean()
    assert 0.04 <= active <= 0.12, f"{cue}: {100 * active:.1f}% of KCs active"
    assert patterns[cue][0].mean() < 2.0                          # mean KC rate stays low (Hz)


def test_no_cue_no_kc_activity(circuit):
    assert (kc_pattern(circuit, (0.0, 0.0, 0.05), seed=1) > 0).mean() < 0.01


def test_cue_patterns_are_clearly_different_and_reliable(patterns):
    for k in CUES:
        assert jaccard(*patterns[k]) > 0.5, f"{k}: pattern not reproducible"
    for a, b in itertools.combinations(CUES, 2):
        across = jaccard(patterns[a][0], patterns[b][0])
        within = min(jaccard(*patterns[a]), jaccard(*patterns[b]))
        assert across < within, f"{a} vs {b}: overlap {across:.2f} not below within-cue {within:.2f}"
    assert jaccard(patterns["odour+light"][0], patterns["odour+dark"][0]) < 0.4
    assert jaccard(patterns["odour+dark"][0], patterns["light only"][0]) < 0.4


def test_apl_is_what_keeps_kcs_sparse(circuit):
    strong = (0.8, 0.8, 0.05)
    with_apl = (kc_pattern(circuit, strong, seed=1) > 0).mean()
    without = (kc_pattern(circuit, strong, seed=1, no_apl=True) > 0).mean()
    assert with_apl < 0.12 and without > 0.4
