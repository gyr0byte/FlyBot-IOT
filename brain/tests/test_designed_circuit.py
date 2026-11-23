"""The designed circuit: receptors (plan step 5.2) and antennal lobe (plan step 5.3)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from flybot_brain.network import Network, load_circuit

REPO = Path(__file__).resolve().parents[2]
DESIGNED = REPO / "brain" / "circuits" / "designed"
sys.path.insert(0, str(REPO / "brain" / "tools"))
import make_designed_circuit  # noqa: E402


@pytest.fixture(scope="module")
def circuit():
    return load_circuit(DESIGNED)


def rates(circuit, ol, orr, ms=3000, seed=1, no_ln=False):
    net = Network(circuit, seed=seed)
    if no_ln:                                         # remove cross-lateral inhibition
        for k, name in enumerate(net.projection_names):
            if name.startswith("ln_"):
                net.e_w[net.e_proj == k] = 0.0
    net.set_input("odour_left", ol)
    net.set_input("odour_right", orr)
    net.run(300)
    g = net.group_counts(net.run(ms))
    return {k: g[k] / circuit.groups[k].size / (ms / 1000) for k in g}


def test_committed_circuit_matches_its_generator(tmp_path):
    make_designed_circuit.generate(tmp_path / "d", seed=1)
    for f in ("manifest.yaml", "groups.csv", "connections.csv"):
        assert (tmp_path / "d" / f).read_text() == (DESIGNED / f).read_text(), f"{f} is stale: rerun the generator"


def test_shape(circuit):
    assert circuit.groups["pl"].size == 25 and circuit.groups["rl"].size == 50 and circuit.groups["ln_l"].size == 10
    assert circuit.manifest["source"]["kind"] == "designed"
    assert {"odour_left", "odour_right", "light"} <= set(circuit.inputs)
    assert circuit.outputs["steer_left"] == ["pl"] and circuit.outputs["steer_right"] == ["pr"]


# ----------------------------------------------------------------------------- 5.2 receptors

def test_receptor_rate_rises_and_falls_smoothly_with_odour(circuit):
    levels = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 0.8, 0.6, 0.4, 0.2, 0.0]
    net = Network(circuit, seed=2)
    measured = []
    for x in levels:
        net.set_input("odour_left", x)
        measured.append(net.group_counts(net.run(2000))["rl"] / 50 / 2.0)
    up, down = measured[:6], measured[5:][::-1]
    assert up == sorted(up) and down == sorted(down)                        # rises, then falls back
    b = circuit.inputs["odour_left"]
    for x, r in zip(levels, measured):
        assert r == pytest.approx(b.base_hz + b.gain_hz * x, rel=0.1, abs=1.0)  # linear rate code
    assert np.corrcoef(levels[:6], up)[0, 1] > 0.999


# ----------------------------------------------------------------------------- 5.3 antennal lobe

def test_projection_neurons_silent_in_clean_air_and_respond_to_weak_odour(circuit):
    assert rates(circuit, 0.0, 0.0)["pl"] < 0.5
    assert rates(circuit, 0.06, 0.06)["pl"] > 3.0
    pn = [rates(circuit, x, x)["pl"] for x in (0.03, 0.1, 0.3, 0.8)]
    assert pn == sorted(pn)


@pytest.mark.parametrize("ol,orr", [(0.06, 0.04), (0.12, 0.08), (0.3, 0.2), (0.5, 0.4)])
def test_pn_contrast_is_clearer_than_the_sensor_contrast(circuit, ol, orr):
    r = rates(circuit, ol, orr)
    raw = (ol - orr) / (ol + orr)
    pn = (r["pl"] - r["pr"]) / (r["pl"] + r["pr"])
    assert pn > raw * 1.3


def test_lateral_inhibition_is_what_sharpens_the_contrast(circuit):
    with_ln = rates(circuit, 0.5, 0.4)
    without = rates(circuit, 0.5, 0.4, no_ln=True)
    c = lambda r: (r["pl"] - r["pr"]) / (r["pl"] + r["pr"])   # noqa: E731
    assert c(with_ln) > 1.8 * c(without)                       # inhibition roughly doubles the contrast
    assert with_ln["pr"] < without["pr"]                       # the weaker side is suppressed


def test_mirror_symmetry(circuit):
    a, b = rates(circuit, 0.3, 0.2, seed=5), rates(circuit, 0.2, 0.3, seed=5)
    assert a["pl"] == pytest.approx(b["pr"], rel=0.25) and a["pr"] == pytest.approx(b["pl"], rel=0.6, abs=2)
