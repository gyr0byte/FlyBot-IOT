"""Spiking network engine: leaky integrate-and-fire neurons, 1 ms steps, any circuit (circuit.py).

Model (dimensionless voltage, dt = 1 ms):
  i_syn <- i_syn * exp(-dt / tau_syn) + sum of signed input from spikes in the previous step
  v     <- v + dt / tau_m * (-v + i_syn + bias) + noise * N(0, 1)                (unless refractory)
  spike when v >= v_thresh: v <- v_reset, refractory for t_ref_ms
Poisson input groups spike with probability rate * dt; rates come from the circuit's input bindings.
Connections are delta pulses into i_syn with a 1-step (1 ms) delay. Effective weight of an edge =
weight * projection scale * (+1 exc / -1 inh). "mod" connections are not membrane input: they are
kept apart for the modulation system (Phase 5.6), as are the plastic flags and plasticity specs.

Time: run(n_steps) integrates n 1 ms steps; the controller passes the robot-time step of each
control tick (contract 7.1: monotonic, gaps capped at 100 ms), so 50 ms of robot time = 50 steps.

Speed: LIF neurons are stored first (contiguous slices, no fancy-index copies), Poisson inputs
after them; edges are sorted by presynaptic neuron (CSR) so each step only touches the outgoing
edges of neurons that spiked; a tick's Poisson draws and noise are made in one call.
"""
from __future__ import annotations

import numpy as np

from .circuit import Circuit

DT_MS = 1.0


class Network:
    def __init__(self, circuit: Circuit, seed: int = 1):
        self.circuit = circuit
        self.seed = seed
        self.group_names = list(circuit.groups)                  # circuit order (for counts / spk)
        G = len(self.group_names)
        layout = ([g for g in self.group_names if circuit.groups[g].model == "lif"]
                  + [g for g in self.group_names if circuit.groups[g].model == "poisson"])
        self.offset: dict[str, int] = {}
        pos = 0
        for g in layout:
            self.offset[g] = pos
            pos += circuit.groups[g].size
        self.n = pos
        self.n_lif = sum(circuit.groups[g].size for g in layout if circuit.groups[g].model == "lif")
        gidx = {g: i for i, g in enumerate(self.group_names)}
        self.group_of = np.empty(self.n, dtype=np.int32)
        for g in layout:
            self.group_of[self.offset[g]:self.offset[g] + circuit.groups[g].size] = gidx[g]

        L = self.n_lif
        self.k_m = np.empty(L); self.v_thresh = np.empty(L); self.v_reset = np.empty(L)
        self.t_ref = np.empty(L, dtype=np.int32); self.decay_syn = np.empty(L)
        self.bias = np.empty(L); self.noise = np.empty(L)
        for g in layout:
            grp = circuit.groups[g]
            if grp.model != "lif":
                continue
            p = grp.params
            sl = slice(self.offset[g], self.offset[g] + grp.size)
            self.k_m[sl] = DT_MS / p["tau_m_ms"]; self.v_thresh[sl] = p["v_thresh"]; self.v_reset[sl] = p["v_reset"]
            self.t_ref[sl] = int(round(p["t_ref_ms"] / DT_MS)); self.decay_syn[sl] = np.exp(-DT_MS / p["tau_syn_ms"])
            self.bias[sl] = p["bias"]; self.noise[sl] = p["noise"]
        self.noisy = bool(np.any(self.noise > 0))
        self.rate_hz = np.zeros(self.n - L)                       # Poisson neurons, in layout order

        # Membrane edges (exc / inh), CSR by global presynaptic index. Posts are always LIF (< L).
        pre, post, w, plastic, proj_id = [], [], [], [], []
        mod_pre, mod_post, mod_w = [], [], []
        self.projection_names = list(circuit.projections)
        for k, (name, p) in enumerate(circuit.projections.items()):
            gp = p.pre_idx.astype(np.int64) + self.offset[p.pre]
            gq = p.post_idx.astype(np.int64) + self.offset[p.post]
            ws = p.weight.astype(np.float64) * p.scale
            m = p.kind != 0
            pre.append(gp[m]); post.append(gq[m]); w.append(ws[m] * p.kind[m])
            plastic.append(np.full(int(m.sum()), p.plastic)); proj_id.append(np.full(int(m.sum()), k))
            mod_pre.append(gp[~m]); mod_post.append(gq[~m]); mod_w.append(ws[~m])
        pre = np.concatenate(pre)
        order = np.argsort(pre, kind="stable")
        self.e_pre = pre[order].astype(np.int32)
        self.e_post = np.concatenate(post)[order].astype(np.int32)
        self.e_w = np.concatenate(w)[order]
        self.e_plastic = np.concatenate(plastic)[order]
        self.e_proj = np.concatenate(proj_id)[order].astype(np.int16)
        self.indptr = np.searchsorted(self.e_pre, np.arange(self.n + 1)).astype(np.int64)
        self.mod_pre = np.concatenate(mod_pre).astype(np.int32)    # used from Phase 5.6
        self.mod_post = np.concatenate(mod_post).astype(np.int32)
        self.mod_w = np.concatenate(mod_w)
        self.n_groups = G
        self.reset()

    # ------------------------------------------------------------------ state

    def reset(self) -> None:
        """Fresh state and re-seeded randomness (contract 7.1: reset on reboot when configured)."""
        self.rng = np.random.default_rng(self.seed)
        self.v = self.v_reset.copy()
        self.i_syn = np.zeros(self.n_lif)
        self.refrac = np.zeros(self.n_lif, dtype=np.int32)
        self.spikes = np.empty(0, dtype=np.int64)                   # global indices that spiked last step
        self.counts = np.zeros(self.n_groups, dtype=np.int64)        # since take_spk()

    def set_input(self, name: str, value: float) -> None:
        """Set an input binding's value (e.g. odour 0-1) -> Poisson rate of its group."""
        b = self.circuit.inputs[name]
        o = self.offset[b.group] - self.n_lif
        self.rate_hz[o:o + self.circuit.groups[b.group].size] = max(0.0, b.base_hz + b.gain_hz * value)

    # ------------------------------------------------------------------ simulation

    def run(self, n_steps: int) -> np.ndarray:
        """Integrate n_steps of 1 ms. Returns spike counts per group (circuit order) for this run."""
        n_steps = int(n_steps)
        counts = np.zeros(self.n_groups, dtype=np.int64)
        if n_steps <= 0:
            return counts
        L, n = self.n_lif, self.n
        P = n - L
        poisson_spikes = (self.rng.random((n_steps, P)) < self.rate_hz * (DT_MS / 1000.0)) if P else None
        noise = self.rng.standard_normal((n_steps, L)) * self.noise if self.noisy else None
        v, i_syn, refrac = self.v, self.i_syn, self.refrac
        k_m, bias, thresh, reset, t_ref, decay = (self.k_m, self.bias, self.v_thresh, self.v_reset,
                                                  self.t_ref, self.decay_syn)
        indptr, e_post, e_w, group_of = self.indptr, self.e_post, self.e_w, self.group_of
        s = self.spikes
        for t in range(n_steps):
            # 1. deliver last step's spikes (1 ms delay)
            i_syn *= decay
            if s.size:
                starts = indptr[s]
                lens = indptr[s + 1] - starts
                total = int(lens.sum())
                if total:
                    idx = np.repeat(starts - (np.cumsum(lens) - lens), lens) + np.arange(total)
                    i_syn += np.bincount(e_post[idx], weights=e_w[idx], minlength=L)
            # 2. integrate LIF neurons (refractory ones are held at reset)
            v += k_m * (bias + i_syn - v)
            if noise is not None:
                v += noise[t]
            ref = refrac > 0
            if ref.any():
                v[ref] = reset[ref]
            refrac -= 1
            # 3. spikes
            fired = np.flatnonzero(v >= thresh)
            if fired.size:
                v[fired] = reset[fired]
                refrac[fired] = t_ref[fired]
            if P:
                pf = np.flatnonzero(poisson_spikes[t]) + L
                s = np.concatenate([fired, pf]) if pf.size else fired
            else:
                s = fired
            if s.size:
                counts += np.bincount(group_of[s], minlength=self.n_groups)
        self.spikes = s
        self.counts += counts
        return counts

    # ------------------------------------------------------------------ readout

    def group_counts(self, counts: np.ndarray) -> dict[str, int]:
        return {g: int(c) for g, c in zip(self.group_names, counts)}

    def take_spk(self) -> dict[str, int]:
        """Spike counts since the last call, summed by contract spk key (brain/state.spk)."""
        out: dict[str, int] = {}
        for g, c in zip(self.group_names, self.counts):
            key = self.circuit.groups[g].spk_key
            if key:
                out[key] = min(out.get(key, 0) + int(c), 100000)
        self.counts[:] = 0
        return out
