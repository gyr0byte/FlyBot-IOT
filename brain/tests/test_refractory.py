"""The engine's refractory convention (found by the Brian2 check, step 5.12).

t_ref_ms is the number of 1 ms steps a neuron is held at reset AFTER the step in which it spiked.
The effective refractory period, in the usual sense (spike step included), is therefore
t_ref_ms + 1 ms, and the shortest interval between spikes is t_ref_ms + 1 steps. Literature values
(e.g. for the FlyWire circuit in step 5.13) must have 1 ms subtracted when entered.
"""
from __future__ import annotations

import numpy as np
import pytest

from flybot_brain.network import Network, write_circuit


def one_neuron(tmp_path, t_ref_ms):
    groups = [{"group": "n", "size": 1, "model": "lif", "tau_m_ms": 2, "v_thresh": 1.0, "v_reset": 0.0,
               "t_ref_ms": t_ref_ms, "tau_syn_ms": 5, "bias": 1000.0, "noise": 0.0, "side": "-"},
              {"group": "silent", "size": 1, "model": "poisson", "side": "-"}]       # rate 0: never fires
    manifest = {"name": f"refractory-{t_ref_ms}", "source": {"kind": "designed"}, "inputs": {}, "outputs": {},
                "projections": [{"name": "silent_n", "pre": "silent", "post": "n", "scale": 1.0}]}
    return write_circuit(tmp_path / f"r{t_ref_ms}", manifest, groups, [("silent_n", 0, 0, 1.0, "exc")])


def spike_steps(net, steps=40):
    out = []
    for t in range(steps):
        net.run(1)
        if net.spikes.size:
            out.append(t)
    return out


@pytest.mark.parametrize("t_ref_ms", [0, 1, 2, 5])
def test_a_strongly_driven_neuron_fires_every_t_ref_plus_one_steps(tmp_path, t_ref_ms):
    """Bias far above threshold: the neuron fires as soon as it is allowed to."""
    steps = spike_steps(Network(one_neuron(tmp_path, t_ref_ms), seed=1))
    intervals = set(np.diff(steps).tolist())
    assert intervals == {t_ref_ms + 1}


def test_maximum_rate_follows_the_convention(tmp_path):
    """t_ref_ms 2 (the designed circuit's value) caps a neuron at 1000 / 3 = 333 Hz, not 500 Hz."""
    net = Network(one_neuron(tmp_path, 2), seed=1)
    counts = net.run(3000)
    assert int(counts[net.group_names.index("n")]) == 1000
