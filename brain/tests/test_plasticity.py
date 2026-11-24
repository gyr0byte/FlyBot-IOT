"""Engine plasticity: three-factor dopamine-gated depression and the modulation map (step 5.6)."""
from __future__ import annotations

import numpy as np
import pytest

from flybot_brain.network import Network, write_circuit

LIF = dict(model="lif", tau_m_ms=20, v_thresh=1.0, v_reset=0.0, t_ref_ms=2, tau_syn_ms=5, bias=0.0, noise=0.0)
TAU = 1000.0
LR = 0.1


def circuit(tmp_path, tau_mv=TAU, elig=(None, None)):
    groups = [{"group": "kc", "size": 4, "model": "poisson", "side": "-", "spk_key": "kc"},
              {"group": "rew", "size": 3, "model": "poisson", "side": "-"},
              {"group": "dan", "size": 1, **LIF, "side": "-", "spk_key": "dp"},
              {"group": "ma", "size": 2, **LIF, "side": "-", "spk_key": "ma"},
              {"group": "mv", "size": 2, **LIF, "side": "-", "spk_key": "mv"}]
    conns = [("kc_ma", k, j, 1.0, "exc") for k in range(4) for j in range(2)]
    conns += [("kc_mv", k, j, 1.0, "exc") for k in range(4) for j in range(2)]
    conns += [("rew_dan", r, 0, 10.0, "exc") for r in range(3)]
    def spec(tau, mode):
        ps = {"rule": "three_factor", "lr": LR, "tau_elig_ms": tau, "w_min": 0.0, "w_max": 1.0}
        return ps if mode is None else {**ps, "eligibility": mode}
    manifest = {"name": "plast", "source": {"kind": "designed"},
                "inputs": {"kc0": {"group": "kc", "encoding": "poisson_rate", "base_hz": 0, "gain_hz": 1000},
                           "reward": {"group": "rew", "encoding": "poisson_rate", "base_hz": 0, "gain_hz": 1000}},
                "outputs": {"approach": ["ma"], "avoid": ["mv"]},
                "projections": [{"name": "kc_ma", "pre": "kc", "post": "ma", "scale": 0.1, "plastic": True, "plasticity": spec(TAU, elig[0])},
                                {"name": "kc_mv", "pre": "kc", "post": "mv", "scale": 0.1, "plastic": True, "plasticity": spec(tau_mv, elig[1])},
                                {"name": "rew_dan", "pre": "rew", "post": "dan", "scale": 1.0}]}
    modulation = [{"modulator_group": "dan", "modulator_index": 0, "target_group": "mv", "target_from": 0, "target_to": 2}]
    return write_circuit(tmp_path / "p", manifest, groups, conns, modulation=modulation)


def weights(net, proj):
    k = net.projection_names.index(proj)
    m = net.p_proj == k
    return net.p_raw[m], net.p_pre[m] - net.offset["kc"]


def only_kc0(net, on: bool):
    """Drive only kc[0] (1000 Hz = a spike every step) by hand-setting the Poisson rates."""
    o = net.offset["kc"] - net.n_lif
    net.rate_hz[o:o + 4] = 0.0
    net.rate_hz[o] = 1000.0 if on else 0.0


def dopamine(net, on: bool):
    net.set_input("reward", 1.0 if on else 0.0)


def test_no_dopamine_no_change(tmp_path):
    net = Network(circuit(tmp_path), seed=1)
    only_kc0(net, True)
    net.run(500)
    assert np.all(net.p_raw == 1.0) and net.plastic_changes == 0


def test_dopamine_depresses_only_recently_active_synapses_in_its_compartment(tmp_path):
    net = Network(circuit(tmp_path), seed=1)
    only_kc0(net, True)
    net.run(200)
    dopamine(net, True)
    net.run(30)
    mv, pre_mv = weights(net, "kc_mv")
    ma, _ = weights(net, "kc_ma")
    assert np.all(mv[pre_mv == 0] < 1.0)                 # kc0 -> avoid: depressed
    assert np.all(mv[pre_mv != 0] == 1.0)                # silent KCs: untouched
    assert np.all(ma == 1.0)                             # approach compartment: not gated by this DAN
    k = net.projection_names.index("kc_mv")
    assert np.allclose(net.e_w[net.p_idx[net.p_proj == k]], net.p_raw[net.p_proj == k] * 0.1)   # effective input follows


def test_eligibility_decays_so_delayed_dopamine_depresses_less(tmp_path):
    drops = {}
    for delay in (0, 1500):
        net = Network(circuit(tmp_path), seed=1)
        only_kc0(net, True)
        net.run(5)                                       # 5 spikes: trace ~5, depression stays above w_min
        only_kc0(net, False)
        net.run(delay)
        e = net.elig[net.offset["kc"]]
        net._depress(np.array([net.offset["dan"]]))      # one dopamine spike
        mv, pre = weights(net, "kc_mv")
        drops[delay] = 1.0 - mv[pre == 0][0]
        assert drops[delay] == pytest.approx(min(1.0, LR * e))
    assert drops[1500] / drops[0] == pytest.approx(np.exp(-1.5), rel=0.02)   # tau_elig = 1 s


def test_weights_stay_within_bounds(tmp_path):
    net = Network(circuit(tmp_path), seed=1)
    only_kc0(net, True)
    net.run(200)
    dopamine(net, True)
    net.run(2000)
    mv, _ = weights(net, "kc_mv")
    assert mv.min() >= 0.0


def test_forgetting_relaxes_toward_initial_weights(tmp_path):
    net = Network(circuit(tmp_path), seed=1)
    net.set_plastic_weights(np.full(net.p_raw.size, 0.2))
    net.relax(1000.0, 1000.0)
    assert np.allclose(net.p_raw, 1.0 - 0.8 * np.exp(-1.0))
    net.relax(1000.0, 0.0)                               # tau 0 = forgetting off
    assert np.allclose(net.p_raw, 1.0 - 0.8 * np.exp(-1.0))


def test_reset_keeps_weights_and_reset_weights_restores_them(tmp_path):
    net = Network(circuit(tmp_path), seed=1)
    net.set_plastic_weights(np.full(net.p_raw.size, 0.3))
    net.reset()
    assert np.all(net.p_raw == 0.3) and not net.elig.any()
    net.reset_weights()
    assert np.all(net.p_raw == 1.0)


def test_learning_switch(tmp_path):
    net = Network(circuit(tmp_path), seed=1)
    net.learning = False
    only_kc0(net, True)
    dopamine(net, True)
    net.run(300)
    assert np.all(net.p_raw == 1.0)


def test_shared_presynaptic_neurons_need_one_eligibility_time_constant(tmp_path):
    with pytest.raises(ValueError, match="same tau_elig_ms"):
        Network(circuit(tmp_path, tau_mv=500.0), seed=1)


# ----------------------------------------------------------------------------- saturating eligibility

def drop_after(tmp_path, mode, kc_ms):
    """kc0 fires every step for kc_ms, then dopamine for 10 ms; returns the kc0 -> avoid weight drop."""
    net = Network(circuit(tmp_path, elig=(mode, mode)), seed=1)
    only_kc0(net, True)
    net.run(kc_ms)
    only_kc0(net, False)
    dopamine(net, True)
    net.run(10)
    mv, pre = weights(net, "kc_mv")
    return 1.0 - mv[pre == 0].mean(), net


def test_saturating_tag_never_exceeds_one(tmp_path):
    _, net = drop_after(tmp_path, "saturate", 200)
    kc0 = net.offset["kc"]
    assert 0.9 < net.elig[kc0] <= 1.0


def test_saturating_tag_makes_depression_independent_of_kc_firing_rate(tmp_path):
    sat_few, _ = drop_after(tmp_path, "saturate", 3)
    sat_many, _ = drop_after(tmp_path, "saturate", 60)
    assert sat_few == pytest.approx(sat_many, rel=0.05)
    traces = {}
    for n in (3, 6):                                     # the accumulating trace counts spikes
        net = Network(circuit(tmp_path), seed=1)
        only_kc0(net, True)
        net.run(n)
        traces[n] = net.elig[net.offset["kc"]]
    assert traces[6] > 1.8 * traces[3] > 1.8 * 2.5


def test_default_eligibility_is_accumulate(tmp_path):
    c = circuit(tmp_path)
    assert c.projections["kc_mv"].plasticity.eligibility == "accumulate"


def test_projections_sharing_kcs_must_agree_on_eligibility(tmp_path):
    with pytest.raises(ValueError, match="same eligibility"):
        Network(circuit(tmp_path, elig=("saturate", "accumulate")), seed=1)


def test_unknown_eligibility_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="eligibility"):
        circuit(tmp_path, elig=("forever", "forever"))
