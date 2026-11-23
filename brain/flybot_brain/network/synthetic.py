"""Synthetic circuits in format version 1, for tests and the performance check.

synthetic_flywire_scale(): about the size of the FlyWire mushroom-body circuit measured in the
Phase 5 feasibility check (right hemisphere: 2,950 neurons, ~25,000 connections at >= 5 synapses):
input -> PN -> KC -> MBON with an APL-like inhibitory loop and DAN-like modulatory connections.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .circuit import Circuit, write_circuit

LIF = dict(model="lif", tau_m_ms=20, v_thresh=1.0, v_reset=0.0, t_ref_ms=2, tau_syn_ms=5, bias=0.0, noise=0.0)


def _group(name, size, side="-", spk=None, **kw):
    g = {"group": name, "size": size, "side": side, "spk_key": spk, "lesion_target": None}
    if kw.get("model") == "poisson":
        g["model"] = "poisson"
    else:
        g.update(LIF, **kw)
    return g


def synthetic_flywire_scale(path: str | Path, seed: int = 0) -> Circuit:
    rng = np.random.default_rng(seed)
    sizes = dict(inp=100, pn=140, kc=2600, apl=1, mbon=48, dan=165)
    groups = [_group("inp", sizes["inp"], model="poisson", spk="rl"),
              _group("pn", sizes["pn"], spk="pl"), _group("kc", sizes["kc"], spk="kc"),
              _group("apl", 1, spk="apl"), _group("mbon", sizes["mbon"], spk="ma"),
              _group("dan", sizes["dan"], spk="dp", bias=1.2)]
    conns = []

    def wire(proj, pre, post, n_edges, w_lo, w_hi, t):
        pairs = set()
        while len(pairs) < n_edges:
            pairs.add((int(rng.integers(sizes[pre])), int(rng.integers(sizes[post]))))
        for a, b in sorted(pairs):
            conns.append((proj, a, b, float(rng.uniform(w_lo, w_hi)), t))

    wire("inp_pn", "inp", "pn", 1400, 5, 20, "exc")
    wire("pn_kc", "pn", "kc", 9800, 5, 30, "exc")
    wire("kc_apl", "kc", "apl", 2600, 5, 30, "exc")
    wire("apl_kc", "apl", "kc", 2600, 5, 30, "inh")
    wire("kc_mbon", "kc", "mbon", 7500, 5, 15, "exc")
    wire("dan_mbon", "dan", "mbon", 180, 5, 30, "mod")
    wire("mbon_mbon", "mbon", "mbon", 160, 5, 30, "inh")
    manifest = {
        "name": "synthetic_flywire_scale",
        "description": "Random circuit at FlyWire mushroom-body scale, for the engine performance test.",
        "source": {"kind": "designed"},
        "inputs": {"drive": {"group": "inp", "encoding": "poisson_rate", "base_hz": 5.0, "gain_hz": 60.0}},
        "outputs": {"out": ["mbon"]},
        "projections": [
            {"name": "inp_pn", "pre": "inp", "post": "pn", "scale": 0.09},
            {"name": "pn_kc", "pre": "pn", "post": "kc", "scale": 0.11},
            {"name": "kc_apl", "pre": "kc", "post": "apl", "scale": 0.03},
            {"name": "apl_kc", "pre": "apl", "post": "kc", "scale": 0.4},
            {"name": "kc_mbon", "pre": "kc", "post": "mbon", "scale": 0.05, "plastic": True,
             "plasticity": {"rule": "three_factor", "lr": 0.01, "tau_elig_ms": 1000.0, "w_min": 0.0, "w_max": 30.0}},
            {"name": "dan_mbon", "pre": "dan", "post": "mbon", "scale": 0.01},
            {"name": "mbon_mbon", "pre": "mbon", "post": "mbon", "scale": 0.05},
        ],
    }
    return write_circuit(path, manifest, groups, conns)
