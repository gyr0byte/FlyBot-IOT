"""Spiking network engine: leaky integrate-and-fire neurons, 1 ms steps, any circuit (circuit.py).

Model (dimensionless voltage, dt = 1 ms):
  i_syn <- i_syn * exp(-dt / tau_syn) + sum of signed input from spikes in the previous step
  v     <- v + dt / tau_m * (-v + i_syn + bias) + noise * N(0, 1)                (unless refractory)
  spike when v >= v_thresh: v <- v_reset, refractory for t_ref_ms
Poisson input groups spike with probability rate * dt; rates come from the circuit's input bindings.
Connections are delta pulses into i_syn with a 1-step (1 ms) delay. Effective weight of an edge =
weight * projection scale * (+1 exc / -1 inh). "mod" connections are not membrane input.
Plastic projections learn with the "three_factor" rule (dopamine-gated depression, see
_init_plasticity); modulation.csv says which modulator neurons gate which plastic synapses.

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
        self.mod_pre = np.concatenate(mod_pre).astype(np.int32)    # direct DAN contacts (not membrane input)
        self.mod_post = np.concatenate(mod_post).astype(np.int32)
        self.mod_w = np.concatenate(mod_w)
        self.n_groups = G
        self._init_plasticity()
        self.reset()

    # ------------------------------------------------------------------ plasticity (Phase 5.6)

    def _init_plasticity(self) -> None:
        """Rule "three_factor" = dopamine-gated depression (the fly mushroom body's KC->MBON LTD):
        each presynaptic neuron of a plastic projection keeps an eligibility trace (+1 per spike,
        decaying with tau_elig_ms); when a modulator neuron spikes, every plastic synapse it gates
        (modulation.csv) changes by -lr * eligibility(pre), clipped to [w_min, w_max]."""
        c = self.circuit
        idx = np.flatnonzero(self.e_plastic)
        self.p_idx = idx                                         # plastic edges, as indices into e_*
        n_p = idx.size
        self.p_raw = np.zeros(n_p)                               # raw weights (circuit units)
        self.p_factor = np.zeros(n_p)                            # raw -> signed synaptic input
        self.p_lr = np.zeros(n_p); self.p_wmin = np.zeros(n_p); self.p_wmax = np.zeros(n_p)
        self.p_pre = self.e_pre[idx].astype(np.int64)
        self.p_post = self.e_post[idx].astype(np.int64)
        self.p_proj = self.e_proj[idx]
        self.elig_decay = np.ones(self.n)                        # per neuron; 1 = no trace kept
        for k, name in enumerate(self.projection_names):
            proj = c.projections[name]
            m = self.p_proj == k
            if not m.any():
                continue
            ps = proj.plasticity
            sign = self.e_w[idx[m]] / np.abs(self.e_w[idx[m]]).clip(min=1e-300)
            self.p_factor[m] = proj.scale * np.where(sign < 0, -1.0, 1.0)
            self.p_raw[m] = np.abs(self.e_w[idx[m]]) / proj.scale
            self.p_lr[m], self.p_wmin[m], self.p_wmax[m] = ps.lr, ps.w_min, ps.w_max
            decay = np.exp(-DT_MS / ps.tau_elig_ms)
            pres = np.unique(self.p_pre[m])
            clash = (self.elig_decay[pres] != 1.0) & ~np.isclose(self.elig_decay[pres], decay)
            if clash.any():
                raise ValueError(f"projection {name!r}: plastic projections sharing presynaptic neurons "
                                 "must use the same tau_elig_ms")
            self.elig_decay[pres] = decay
        self.p_raw0 = self.p_raw.copy()
        # Which plastic edges each modulator neuron gates.
        gates: dict[int, list[np.ndarray]] = {}
        for row in c.modulation:
            mod = self.offset[row.modulator_group] + row.modulator_index
            lo = self.offset[row.target_group] + row.target_from
            hi = self.offset[row.target_group] + row.target_to
            gates.setdefault(mod, []).append(np.flatnonzero((self.p_post >= lo) & (self.p_post < hi)))
        self.gates = {m: np.unique(np.concatenate(v)) for m, v in gates.items()}
        self.is_modulator = np.zeros(self.n, dtype=bool)
        self.is_modulator[list(self.gates)] = True
        self.learning = True                                     # lesion "learn" switches this off (5.10)
        self.plastic_changes = 0

    def reset_weights(self) -> None:
        """Back to the circuit's initial plastic weights (brain/control reset "weights")."""
        self.set_plastic_weights(self.p_raw0)

    def plastic_weights(self) -> np.ndarray:
        return self.p_raw.copy()

    def set_plastic_weights(self, raw) -> None:
        raw = np.asarray(raw, dtype=np.float64)
        if raw.shape != self.p_raw.shape:
            raise ValueError(f"expected {self.p_raw.size} plastic weights, got {raw.size}")
        self.p_raw = np.clip(raw, self.p_wmin, self.p_wmax)
        self.e_w[self.p_idx] = self.p_raw * self.p_factor
        self.plastic_changes += 1

    def relax(self, dt_ms: float, tau_ms: float) -> None:
        """Forgetting: plastic weights drift back toward their initial values (time constant tau_ms)."""
        if tau_ms <= 0 or dt_ms <= 0 or self.p_raw.size == 0:
            return
        self.p_raw += (self.p_raw0 - self.p_raw) * (1.0 - np.exp(-dt_ms / tau_ms))
        self.e_w[self.p_idx] = self.p_raw * self.p_factor

    def _depress(self, modulators: np.ndarray) -> None:
        for m in modulators.tolist():
            edges = self.gates[m]
            dw = self.p_lr[edges] * self.elig[self.p_pre[edges]]
            if not dw.any():
                continue
            self.p_raw[edges] = np.clip(self.p_raw[edges] - dw, self.p_wmin[edges], self.p_wmax[edges])
            self.e_w[self.p_idx[edges]] = self.p_raw[edges] * self.p_factor[edges]
            self.plastic_changes += 1

    # ------------------------------------------------------------------ state

    def reset(self) -> None:
        """Fresh state and re-seeded randomness (contract 7.1: reset on reboot when configured)."""
        self.rng = np.random.default_rng(self.seed)
        self.v = self.v_reset.copy()
        self.i_syn = np.zeros(self.n_lif)
        self.refrac = np.zeros(self.n_lif, dtype=np.int32)
        self.spikes = np.empty(0, dtype=np.int64)                   # global indices that spiked last step
        self.counts = np.zeros(self.n_groups, dtype=np.int64)        # since take_spk()
        self.elig = np.zeros(self.n)                                 # eligibility traces (weights are kept)

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
        plastic = self.p_raw.size > 0
        elig, elig_decay, is_mod = self.elig, self.elig_decay, self.is_modulator
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
            # 4. plasticity: eligibility traces, dopamine-gated depression
            if plastic:
                elig *= elig_decay
                if s.size:
                    elig[s] += 1.0
                    if self.learning:
                        mods = s[is_mod[s]]
                        if mods.size:
                            self._depress(mods)
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
