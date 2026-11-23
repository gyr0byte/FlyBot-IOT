"""Circuit format (loader accept / reject) and network engine (plan step 5.1)."""
from __future__ import annotations

import copy
import time

import numpy as np
import pytest

from flybot_brain.network import CircuitError, Network, load_circuit, write_circuit
from flybot_brain.network.synthetic import synthetic_flywire_scale

LIF = dict(model="lif", tau_m_ms=20, v_thresh=1.0, v_reset=0.0, t_ref_ms=2, tau_syn_ms=5, bias=0.0, noise=0.0)


def base():
    """A small valid circuit using every feature of format 1."""
    groups = [
        {"group": "inp", "size": 4, "model": "poisson", "side": "L", "spk_key": "rl"},
        {"group": "pn", "size": 3, **LIF, "side": "L", "spk_key": "pl", "lesion_target": "rcpt"},
        {"group": "inh", "size": 2, **LIF, "side": "-", "spk_key": "ln", "lesion_target": "ln"},
        {"group": "out", "size": 4, **LIF, "side": "-", "spk_key": "ma"},
        {"group": "dan", "size": 2, **LIF, "side": "-", "spk_key": "dp"},
    ]
    conns = [("inp_pn", 0, 0, 10, "exc"), ("inp_pn", 1, 1, 10, "exc"), ("inp_pn", 2, 2, 10, "exc"),
             ("pn_inh", 0, 0, 5, "exc"), ("inh_pn", 0, 1, 8, "inh"), ("inh_pn", 1, 2, 8, "inh"),
             ("pn_out", 0, 0, 1.0, "exc"), ("pn_out", 1, 3, 1.5, "exc"),
             ("dan_out", 0, 0, 3, "mod"), ("dan_out", 1, 3, 3, "mod")]
    manifest = {
        "name": "test", "description": "loader test circuit", "source": {"kind": "designed"},
        "inputs": {"odour_left": {"group": "inp", "encoding": "poisson_rate", "base_hz": 1.0, "gain_hz": 100.0}},
        "outputs": {"steer_left": ["pn"]},
        "projections": [
            {"name": "inp_pn", "pre": "inp", "post": "pn", "scale": 0.1},
            {"name": "pn_inh", "pre": "pn", "post": "inh", "scale": 0.2},
            {"name": "inh_pn", "pre": "inh", "post": "pn", "scale": 0.1},
            {"name": "pn_out", "pre": "pn", "post": "out", "scale": 0.5, "plastic": True,
             "plasticity": {"rule": "three_factor", "lr": 0.01, "tau_elig_ms": 1000.0, "w_min": 0.0, "w_max": 2.0}},
            {"name": "dan_out", "pre": "dan", "post": "out", "scale": 1.0},
        ],
    }
    neurons = [{"group": "out", "index": i, "external_id": 1000 + i, "cell_type": "MBON-x",
                "compartment": "g1" if i < 2 else "g2"} for i in range(4)]
    modulation = [{"modulator_group": "dan", "modulator_index": 0, "target_group": "out",
                   "target_from": 0, "target_to": 2},
                  {"modulator_group": "dan", "modulator_index": 1, "target_group": "out", "compartment": "g2"}]
    return manifest, groups, conns, neurons, modulation


def build(tmp_path, manifest=None, groups=None, conns=None, neurons="default", modulation="default"):
    m, g, c, n, mo = base()
    return write_circuit(tmp_path / "c", manifest or m, groups or g, conns or c,
                         n if neurons == "default" else neurons, mo if modulation == "default" else modulation)


# ----------------------------------------------------------------------------- loader: accepts

def test_valid_circuit_loads_with_every_feature(tmp_path):
    c = build(tmp_path)
    assert c.n_neurons == 15 and c.n_connections == 10
    assert c.projections["pn_out"].plasticity.rule == "three_factor"
    assert c.projections["inh_pn"].kind.tolist() == [-1, -1]
    assert [(r.target_from, r.target_to, r.compartment) for r in c.modulation] == [(0, 2, None), (2, 4, "g2")]
    assert (tmp_path / "c" / "connections.csv").read_text().splitlines()[0] == "projection,pre,post,weight,type"


def test_neurons_and_modulation_files_are_optional(tmp_path):
    m, g, c, _, _ = base()
    c2 = write_circuit(tmp_path / "c", m, g, c)
    assert c2.neurons is None and c2.modulation == []


# ----------------------------------------------------------------------------- loader: rejects

def reject(tmp_path, match, **parts):
    with pytest.raises(CircuitError, match=match):
        build(tmp_path, **parts)


def test_rejects_undeclared_projection(tmp_path):
    _, _, c, _, _ = base()
    reject(tmp_path, "not declared", conns=c + [("nope", 0, 0, 1, "exc")])


def test_rejects_bad_type(tmp_path):
    _, _, c, _, _ = base()
    reject(tmp_path, "exc, inh or mod", conns=[("inp_pn", 0, 0, 10, "excit")] + c[1:])


def test_rejects_index_out_of_range(tmp_path):
    _, _, c, _, _ = base()
    reject(tmp_path, "out of range", conns=c + [("inp_pn", 3, 7, 1, "exc")])


def test_rejects_dale_violation(tmp_path):
    _, _, c, _, _ = base()
    reject(tmp_path, "one type per presynaptic neuron", conns=c + [("pn_inh", 0, 1, 5, "inh")])


def test_rejects_duplicate_connection(tmp_path):
    _, _, c, _, _ = base()
    reject(tmp_path, "duplicate", conns=c + [c[0]])


def test_rejects_negative_weight(tmp_path):
    _, _, c, _, _ = base()
    reject(tmp_path, ">= 0", conns=[("inp_pn", 0, 0, -1, "exc")] + c[1:])


@pytest.mark.parametrize("change,match", [
    (lambda p: p.pop("plasticity"), "needs a plasticity spec"),
    (lambda p: p["plasticity"].update(rule="hebb"), "rule must be one of"),
    (lambda p: p["plasticity"].update(w_min=3.0), "w_min must not exceed w_max"),
    (lambda p: p["plasticity"].update(lr=0), "must be > 0"),
    (lambda p: p["plasticity"].pop("tau_elig_ms"), "plasticity keys"),
    (lambda p: p["plasticity"].update(w_max=1.2), "within \\[w_min, w_max\\]"),
    (lambda p: p.update(scale=0), "must be > 0"),
    (lambda p: p.update(extra=1), "unknown keys"),
])
def test_rejects_bad_plasticity_or_scale(tmp_path, change, match):
    m, *_ = base()
    m = copy.deepcopy(m)
    change(next(p for p in m["projections"] if p["name"] == "pn_out"))
    reject(tmp_path, match, manifest=m)


def test_rejects_plasticity_on_non_plastic_projection(tmp_path):
    m, *_ = base()
    m["projections"][0]["plasticity"] = {"rule": "three_factor", "lr": 1, "tau_elig_ms": 1, "w_min": 0, "w_max": 1}
    reject(tmp_path, "plastic is false", manifest=m)


def test_rejects_mod_connection_in_plastic_projection(tmp_path):
    _, _, c, _, _ = base()
    reject(tmp_path, "cannot contain mod", conns=c + [("pn_out", 2, 1, 1.0, "mod")])


def test_rejects_connections_into_a_poisson_group(tmp_path):
    m, g, c, n, mo = base()
    m["projections"].append({"name": "pn_inp", "pre": "pn", "post": "inp", "scale": 1})
    reject(tmp_path, "poisson input group", manifest=m, conns=c + [("pn_inp", 0, 0, 1, "exc")])


def test_rejects_input_bound_to_a_lif_group(tmp_path):
    m, *_ = base()
    m["inputs"]["odour_left"]["group"] = "pn"
    reject(tmp_path, "must be a poisson group", manifest=m)


def test_rejects_non_contract_spk_key_and_lesion_target(tmp_path):
    _, g, *_ = base()
    g[1]["spk_key"] = "pnx"
    reject(tmp_path, "spk_key", groups=g)
    _, g, *_ = base()
    g[1]["lesion_target"] = "cortex"
    reject(tmp_path, "lesion_target", groups=g)


def test_rejects_lif_params_on_poisson_group(tmp_path):
    _, g, *_ = base()
    g[0]["tau_m_ms"] = 20
    reject(tmp_path, "poisson groups take no LIF parameters", groups=g)


def test_rejects_wrong_format_version_and_columns(tmp_path):
    m, *_ = base()
    c = build(tmp_path)
    mp = c.path / "manifest.yaml"
    mp.write_text(mp.read_text().replace("format: 1", "format: 2"))
    with pytest.raises(CircuitError, match="format must be 1"):
        load_circuit(c.path)
    mp.write_text(mp.read_text().replace("format: 2", "format: 1"))
    cp = c.path / "connections.csv"
    cp.write_text(cp.read_text().replace("weight,type", "weight,sign", 1))
    with pytest.raises(CircuitError, match="columns must be exactly"):
        load_circuit(c.path)


@pytest.mark.parametrize("row,match", [
    ({"modulator_group": "nope", "modulator_index": 0, "target_group": "out", "target_from": 0, "target_to": 1}, "modulator group"),
    ({"modulator_group": "dan", "modulator_index": 5, "target_group": "out", "target_from": 0, "target_to": 1}, "modulator index"),
    ({"modulator_group": "dan", "modulator_index": 0, "target_group": "inh", "target_from": 0, "target_to": 1}, "not the post group of any plastic"),
    ({"modulator_group": "dan", "modulator_index": 0, "target_group": "out", "target_from": 2, "target_to": 9}, "target range"),
    ({"modulator_group": "dan", "modulator_index": 0, "target_group": "out", "target_from": 0, "target_to": 1, "compartment": "g1"}, "not both"),
    ({"modulator_group": "dan", "modulator_index": 0, "target_group": "out"}, "needs target_from/target_to or compartment"),
    ({"modulator_group": "dan", "modulator_index": 0, "target_group": "out", "compartment": "g9"}, "compartment 'g9' not found"),
])
def test_rejects_bad_modulation_rows(tmp_path, row, match):
    reject(tmp_path, match, modulation=[row])


def test_compartment_labels_need_neurons_csv(tmp_path):
    row = {"modulator_group": "dan", "modulator_index": 0, "target_group": "out", "compartment": "g1"}
    reject(tmp_path, "need neurons.csv", neurons=None, modulation=[row])


# ----------------------------------------------------------------------------- engine

def one_neuron(tmp_path, bias=0.0, noise=0.0, rate_gain=0.0):
    groups = [{"group": "inp", "size": 1, "model": "poisson", "side": "-", "spk_key": "rl"},
              {"group": "n", "size": 1, **{**LIF, "bias": bias, "noise": noise}, "side": "-", "spk_key": "pl"}]
    manifest = {"name": "one", "source": {"kind": "designed"},
                "inputs": {"x": {"group": "inp", "encoding": "poisson_rate", "base_hz": 0.0, "gain_hz": rate_gain}},
                "outputs": {"o": ["n"]},
                "projections": [{"name": "inp_n", "pre": "inp", "post": "n", "scale": 1.0}]}
    return write_circuit(tmp_path / "one", manifest, groups, [("inp_n", 0, 0, 0.5, "exc")])


def test_no_input_no_spikes(tmp_path):
    net = Network(one_neuron(tmp_path), seed=1)
    assert net.run(2000).sum() == 0


def test_constant_input_gives_steady_rate_matching_lif_theory(tmp_path):
    net = Network(one_neuron(tmp_path, bias=2.0), seed=1)
    a = net.run(1000)[1]
    b = net.run(1000)[1]
    assert abs(int(a) - int(b)) <= 1                                   # steady
    period = 20 * np.log(2.0 / (2.0 - 1.0)) + 2                         # tau ln(b/(b - th)) + t_ref
    assert int(a) == pytest.approx(1000 / period, rel=0.1)


def test_stronger_input_fires_faster(tmp_path):
    rates = []
    for b in (1.2, 1.6, 2.5, 4.0):
        rates.append(int(Network(one_neuron(tmp_path, bias=b), seed=1).run(1000)[1]))
    assert rates == sorted(rates) and rates[0] < rates[-1]


def test_poisson_rate_and_step_count(tmp_path):
    net = Network(one_neuron(tmp_path, rate_gain=1000.0), seed=1)
    net.set_input("x", 1.0)                                             # 1000 Hz: one spike per 1 ms step
    assert net.run(50)[0] == 50
    assert net.run(0)[0] == 0


def test_one_spike_delivers_exactly_its_signed_weight(tmp_path):
    c = build(tmp_path)
    net = Network(c, seed=1)
    pre = net.offset["pn"] + 0                                          # pn[0] -> inh[0] (5 * 0.2) and out[0] (1.0 * 0.5)
    net.spikes = np.array([pre])
    net.run(1)
    i = net.i_syn
    assert i[net.offset["inh"] + 0] == pytest.approx(5 * 0.2, rel=1e-9)   # delivered after this step's decay
    assert i[net.offset["out"] + 0] == pytest.approx(0.5, rel=1e-9)
    net.reset()
    net.spikes = np.array([net.offset["inh"] + 0])                      # inh[0] -> pn[1]: -8 * 0.1
    net.run(1)
    assert net.i_syn[net.offset["pn"] + 1] == pytest.approx(-0.8, rel=1e-9)


def test_mod_connections_are_not_membrane_input(tmp_path):
    net = Network(build(tmp_path), seed=1)
    net.spikes = np.array([net.offset["dan"] + 0])
    net.run(1)
    assert not np.any(net.i_syn)
    assert len(net.mod_pre) == 2 and np.all(net.mod_w == 3.0)


def test_plastic_edges_are_flagged(tmp_path):
    net = Network(build(tmp_path), seed=1)
    assert int(net.e_plastic.sum()) == 2


def test_seeded_reproducible_and_reset_restarts(tmp_path):
    def run(net):
        net.set_input("odour_left", 0.6)
        return [net.run(50).tolist() for _ in range(20)]
    a, b = Network(build(tmp_path), seed=3), Network(build(tmp_path), seed=3)
    first = run(a)
    assert first == run(b)
    a.reset()
    assert run(a) == first
    assert run(Network(build(tmp_path), seed=4)) != first


def test_spike_counts_by_contract_key(tmp_path):
    net = Network(build(tmp_path), seed=1)
    net.set_input("odour_left", 1.0)
    net.run(500)
    spk = net.take_spk()
    assert set(spk) <= {"rl", "pl", "ln", "ma", "dp"} and spk["rl"] > 0
    assert net.take_spk() == {k: 0 for k in spk}                        # counts reset after reading


# ----------------------------------------------------------------------------- performance (plan 5.1)

def test_flywire_scale_tick_is_well_under_10ms(tmp_path, capsys):
    c = synthetic_flywire_scale(tmp_path / "syn")
    assert c.n_neurons >= 2900 and c.n_connections >= 24000
    net = Network(c, seed=1)
    net.set_input("drive", 1.0)
    net.run(300)
    times = []
    for _ in range(200):
        t = time.perf_counter()
        net.run(50)
        times.append((time.perf_counter() - t) * 1000)
    med, p95 = float(np.median(times)), float(np.percentile(times, 95))
    with capsys.disabled():
        print(f"\n  FlyWire-scale tick ({c.n_neurons} neurons, {c.n_connections} connections): "
              f"median {med:.2f} ms, p95 {p95:.2f} ms per 50 ms of robot time")
    assert med < 10.0 and p95 < 10.0
