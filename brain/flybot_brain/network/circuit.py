"""Circuit file format, version 1: circuits are data, not code (plan, Phase 5 design rule).

A circuit is a folder:
  manifest.yaml     identity, source, inputs, outputs, projections (scale, plasticity), file names
  groups.csv        one row per neuron group
  connections.csv   one row per connection: projection, pre, post, weight, type
  neurons.csv       optional: one row per neuron (group, index, external id, cell type, compartment)
  modulation.csv    optional: which modulator neurons gate which plastic synapses
  NOTICE            data-derived circuits: licence and required citations
The engine (engine.py) knows nothing about any particular circuit. The full specification is in
brain/README.md ("Circuit file format"). load_circuit() validates everything and raises
CircuitError with a precise message; write_circuit() is used by generator scripts.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

FORMAT = 1
MODELS = ("lif", "poisson")
TYPES = {"exc": 1, "inh": -1, "mod": 0}
RULES = ("three_factor",)
ENCODINGS = ("poisson_rate",)
SIDES = ("L", "R", "-")
# The contract's brain/state.spk keys and brain/control lesion targets (contract 3.10, 3.12).
SPK_KEYS = ("rl", "rr", "pl", "pr", "ln", "kc", "apl", "ma", "mv", "dp", "dn")
LESION_TARGETS = ("rcpt", "ln", "mb", "apl", "learn", "hab", "hun", "fear")
LIF_PARAMS = ("tau_m_ms", "v_thresh", "v_reset", "t_ref_ms", "tau_syn_ms", "bias", "noise")
GROUP_COLUMNS = ("group", "size", "model") + LIF_PARAMS + ("side", "spk_key", "lesion_target")
CONNECTION_COLUMNS = ("projection", "pre", "post", "weight", "type")
NEURON_COLUMNS = ("group", "index", "external_id", "cell_type", "compartment")
MODULATION_COLUMNS = ("modulator_group", "modulator_index", "target_group", "target_from", "target_to",
                      "compartment")


class CircuitError(ValueError):
    """A circuit folder does not follow format version 1."""


@dataclass
class Group:
    name: str
    size: int
    model: str
    params: dict[str, float]          # LIF parameters (empty for poisson groups)
    side: str
    spk_key: str | None
    lesion_target: str | None


@dataclass
class Plasticity:
    rule: str
    lr: float
    tau_elig_ms: float
    w_min: float
    w_max: float


@dataclass
class Projection:
    name: str
    pre: str
    post: str
    scale: float
    plastic: bool
    plasticity: Plasticity | None
    pre_idx: np.ndarray = field(repr=False, default=None)    # int32, index within the pre group
    post_idx: np.ndarray = field(repr=False, default=None)   # int32, index within the post group
    weight: np.ndarray = field(repr=False, default=None)     # float32, raw units (>= 0)
    kind: np.ndarray = field(repr=False, default=None)       # int8: +1 exc, -1 inh, 0 mod


@dataclass
class InputBinding:
    name: str
    group: str
    encoding: str
    base_hz: float
    gain_hz: float


@dataclass
class ModulationRow:
    modulator_group: str
    modulator_index: int
    target_group: str
    target_from: int          # half-open range [from, to) of target neurons
    target_to: int
    compartment: str | None


@dataclass
class Circuit:
    path: Path
    name: str
    manifest: dict
    groups: dict[str, Group]
    projections: dict[str, Projection]
    inputs: dict[str, InputBinding]
    outputs: dict[str, list[str]]
    neurons: list[dict] | None
    modulation: list[ModulationRow]

    @property
    def n_neurons(self) -> int:
        return sum(g.size for g in self.groups.values())

    @property
    def n_connections(self) -> int:
        return sum(len(p.weight) for p in self.projections.values())


# ----------------------------------------------------------------------------- helpers

def _fail(where: str, msg: str):
    raise CircuitError(f"{where}: {msg}")


def _num(where: str, value: str, positive: bool = False, nonneg: bool = False) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        _fail(where, f"not a number: {value!r}")
    if not math.isfinite(x):
        _fail(where, f"not finite: {value!r}")
    if positive and x <= 0:
        _fail(where, f"must be > 0, got {x}")
    if nonneg and x < 0:
        _fail(where, f"must be >= 0, got {x}")
    return x


def _int(where: str, value: str) -> int:
    try:
        x = float(value)
    except (TypeError, ValueError):
        _fail(where, f"not an integer: {value!r}")
    if not x.is_integer():
        _fail(where, f"not an integer: {value!r}")
    return int(x)


def _read_csv(path: Path, columns: tuple[str, ...]) -> list[dict]:
    if not path.exists():
        _fail(path.name, "file missing")
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if r.fieldnames is None or tuple(r.fieldnames) != columns:
            _fail(path.name, f"columns must be exactly {','.join(columns)} (got {r.fieldnames})")
        return list(r)


# ----------------------------------------------------------------------------- loading

def load_circuit(path: str | Path) -> Circuit:
    path = Path(path)
    mpath = path / "manifest.yaml"
    if not mpath.exists():
        _fail(str(path), "manifest.yaml missing")
    m = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
    if m.get("format") != FORMAT:
        _fail("manifest.yaml", f"format must be {FORMAT}, got {m.get('format')!r}")
    for key in ("name", "source", "inputs", "outputs", "projections"):
        if key not in m:
            _fail("manifest.yaml", f"missing key {key!r}")
    known = {"format", "name", "description", "source", "generator", "designed_parts", "inputs", "outputs",
             "projections", "neurons", "modulation"}
    if set(m) - known:
        _fail("manifest.yaml", f"unknown keys {sorted(set(m) - known)}")

    groups = _load_groups(path / "groups.csv")
    projections = _load_projections(m["projections"], groups)
    _load_connections(path / "connections.csv", projections, groups)
    _check_dale(projections, groups)
    inputs = _load_inputs(m["inputs"], groups)
    outputs = _load_outputs(m["outputs"], groups)
    neurons = _load_neurons(path / m["neurons"], groups) if m.get("neurons") else None
    modulation = (_load_modulation(path / m["modulation"], groups, projections, neurons)
                  if m.get("modulation") else [])
    return Circuit(path, str(m["name"]), m, groups, projections, inputs, outputs, neurons, modulation)


def _load_groups(path: Path) -> dict[str, Group]:
    groups: dict[str, Group] = {}
    for i, row in enumerate(_read_csv(path, GROUP_COLUMNS), start=2):
        where = f"groups.csv line {i}"
        name = row["group"].strip()
        if not name or not name.replace("_", "").isalnum():
            _fail(where, f"bad group name {name!r}")
        if name in groups:
            _fail(where, f"duplicate group {name!r}")
        size = _int(where + " size", row["size"])
        if size < 1:
            _fail(where, "size must be >= 1")
        model = row["model"].strip()
        if model not in MODELS:
            _fail(where, f"model must be one of {MODELS}, got {model!r}")
        params = {}
        if model == "lif":
            for p in LIF_PARAMS:
                params[p] = _num(f"{where} {p}", row[p], positive=p in ("tau_m_ms", "tau_syn_ms"),
                                 nonneg=p in ("t_ref_ms", "noise"))
            if params["v_reset"] >= params["v_thresh"]:
                _fail(where, "v_reset must be below v_thresh")
        elif any(row[p].strip() for p in LIF_PARAMS):
            _fail(where, "poisson groups take no LIF parameters (leave them empty)")
        side = row["side"].strip()
        if side not in SIDES:
            _fail(where, f"side must be one of {SIDES}")
        spk = row["spk_key"].strip() or None
        if spk is not None and spk not in SPK_KEYS:
            _fail(where, f"spk_key {spk!r} is not a contract brain/state.spk key {SPK_KEYS}")
        les = row["lesion_target"].strip() or None
        if les is not None and les not in LESION_TARGETS:
            _fail(where, f"lesion_target {les!r} is not a contract lesion target {LESION_TARGETS}")
        groups[name] = Group(name, size, model, params, side, spk, les)
    if not groups:
        _fail("groups.csv", "no groups")
    return groups


def _load_projections(specs, groups) -> dict[str, Projection]:
    if not isinstance(specs, list) or not specs:
        _fail("manifest.yaml projections", "must be a non-empty list")
    out: dict[str, Projection] = {}
    for spec in specs:
        name = spec.get("name") if isinstance(spec, dict) else None
        where = f"manifest.yaml projection {name!r}"
        if not name:
            _fail("manifest.yaml projections", f"entry without name: {spec!r}")
        unknown = set(spec) - {"name", "pre", "post", "scale", "plastic", "plasticity"}
        if unknown:
            _fail(where, f"unknown keys {sorted(unknown)}")
        if name in out:
            _fail(where, "duplicate projection name")
        for end in ("pre", "post"):
            if spec.get(end) not in groups:
                _fail(where, f"{end} group {spec.get(end)!r} does not exist")
        if groups[spec["post"]].model == "poisson":
            _fail(where, f"post group {spec['post']!r} is a poisson input group and cannot receive connections")
        scale = _num(where + " scale", spec.get("scale"), positive=True)
        plastic = spec.get("plastic", False)
        if not isinstance(plastic, bool):
            _fail(where, "plastic must be true or false")
        plasticity = None
        if plastic:
            ps = spec.get("plasticity")
            if not isinstance(ps, dict):
                _fail(where, "plastic projection needs a plasticity spec (rule, lr, tau_elig_ms, w_min, w_max)")
            if set(ps) != {"rule", "lr", "tau_elig_ms", "w_min", "w_max"}:
                _fail(where, f"plasticity keys must be rule, lr, tau_elig_ms, w_min, w_max (got {sorted(ps)})")
            if ps["rule"] not in RULES:
                _fail(where, f"plasticity rule must be one of {RULES}, got {ps['rule']!r}")
            plasticity = Plasticity(ps["rule"], _num(where + " lr", ps["lr"], positive=True),
                                    _num(where + " tau_elig_ms", ps["tau_elig_ms"], positive=True),
                                    _num(where + " w_min", ps["w_min"], nonneg=True),
                                    _num(where + " w_max", ps["w_max"], positive=True))
            if plasticity.w_min > plasticity.w_max:
                _fail(where, "w_min must not exceed w_max")
        elif spec.get("plasticity") is not None:
            _fail(where, "plasticity given but plastic is false")
        out[name] = Projection(name, spec["pre"], spec["post"], scale, plastic, plasticity)
    return out


def _load_connections(path: Path, projections, groups) -> None:
    rows = _read_csv(path, CONNECTION_COLUMNS)
    per: dict[str, list] = {p: [] for p in projections}
    for i, row in enumerate(rows, start=2):
        where = f"connections.csv line {i}"
        p = row["projection"]
        if p not in projections:
            _fail(where, f"projection {p!r} is not declared in manifest.yaml")
        t = row["type"].strip()
        if t not in TYPES:
            _fail(where, f"type must be exc, inh or mod, got {t!r}")
        pre, post = _int(where + " pre", row["pre"]), _int(where + " post", row["post"])
        proj = projections[p]
        if not 0 <= pre < groups[proj.pre].size:
            _fail(where, f"pre index {pre} out of range for group {proj.pre!r} (size {groups[proj.pre].size})")
        if not 0 <= post < groups[proj.post].size:
            _fail(where, f"post index {post} out of range for group {proj.post!r} (size {groups[proj.post].size})")
        w = _num(where + " weight", row["weight"], nonneg=True)
        per[p].append((pre, post, w, TYPES[t]))
    for name, proj in projections.items():
        e = per[name]
        if not e:
            _fail(f"projection {name!r}", "has no connections in connections.csv")
        arr = np.array(e, dtype=np.float64)
        proj.pre_idx, proj.post_idx = arr[:, 0].astype(np.int32), arr[:, 1].astype(np.int32)
        proj.weight, proj.kind = arr[:, 2].astype(np.float32), arr[:, 3].astype(np.int8)
        pairs = proj.pre_idx.astype(np.int64) * (2 ** 31) + proj.post_idx
        if len(np.unique(pairs)) != len(pairs):
            _fail(f"projection {name!r}", "duplicate (pre, post) connection")
        if proj.plastic:
            if np.any(proj.kind == 0):
                _fail(f"projection {name!r}", "a plastic projection cannot contain mod connections")
            ps = proj.plasticity
            if proj.weight.min() < ps.w_min or proj.weight.max() > ps.w_max:
                _fail(f"projection {name!r}", f"initial weights must lie within [w_min, w_max] = [{ps.w_min}, {ps.w_max}]")


def _check_dale(projections, groups) -> None:
    """Every presynaptic neuron has one connection type across all its outgoing connections."""
    seen: dict[tuple[str, int], tuple[int, str]] = {}
    for proj in projections.values():
        for pre, kind in zip(proj.pre_idx.tolist(), proj.kind.tolist()):
            key = (proj.pre, pre)
            if key in seen and seen[key][0] != kind:
                names = {1: "exc", -1: "inh", 0: "mod"}
                _fail(f"neuron {proj.pre}[{pre}]", f"has both {names[seen[key][0]]} ({seen[key][1]}) and "
                      f"{names[kind]} ({proj.name}) connections: one type per presynaptic neuron")
            seen[key] = (kind, proj.name)


def _load_inputs(specs, groups) -> dict[str, InputBinding]:
    out = {}
    for name, s in (specs or {}).items():
        where = f"manifest.yaml input {name!r}"
        if not isinstance(s, dict) or set(s) != {"group", "encoding", "base_hz", "gain_hz"}:
            _fail(where, "needs exactly group, encoding, base_hz, gain_hz")
        if s["group"] not in groups:
            _fail(where, f"group {s['group']!r} does not exist")
        if groups[s["group"]].model != "poisson":
            _fail(where, f"group {s['group']!r} must be a poisson group")
        if s["encoding"] not in ENCODINGS:
            _fail(where, f"encoding must be one of {ENCODINGS}")
        out[name] = InputBinding(name, s["group"], s["encoding"], _num(where, s["base_hz"], nonneg=True),
                                 _num(where, s["gain_hz"], nonneg=True))
    return out


def _load_outputs(specs, groups) -> dict[str, list[str]]:
    out = {}
    for name, gs in (specs or {}).items():
        if not isinstance(gs, list) or not gs:
            _fail(f"manifest.yaml output {name!r}", "must be a non-empty list of groups")
        for g in gs:
            if g not in groups:
                _fail(f"manifest.yaml output {name!r}", f"group {g!r} does not exist")
        out[name] = list(gs)
    return out


def _load_neurons(path: Path, groups) -> list[dict]:
    rows = _read_csv(path, NEURON_COLUMNS)
    seen = set()
    for i, row in enumerate(rows, start=2):
        where = f"neurons.csv line {i}"
        g = row["group"]
        if g not in groups:
            _fail(where, f"group {g!r} does not exist")
        idx = _int(where + " index", row["index"])
        if not 0 <= idx < groups[g].size:
            _fail(where, f"index {idx} out of range for group {g!r}")
        if (g, idx) in seen:
            _fail(where, f"duplicate neuron {g}[{idx}]")
        seen.add((g, idx))
        row["index"] = idx
    return rows


def _load_modulation(path: Path, groups, projections, neurons) -> list[ModulationRow]:
    plastic_posts = {p.post for p in projections.values() if p.plastic}
    compartments: dict[str, dict[str, list[int]]] = {}
    for n in neurons or []:
        if n["compartment"]:
            compartments.setdefault(n["group"], {}).setdefault(n["compartment"], []).append(n["index"])
    out = []
    for i, row in enumerate(_read_csv(path, MODULATION_COLUMNS), start=2):
        where = f"modulation.csv line {i}"
        mg, tg = row["modulator_group"], row["target_group"]
        if mg not in groups:
            _fail(where, f"modulator group {mg!r} does not exist")
        mi = _int(where + " modulator_index", row["modulator_index"])
        if not 0 <= mi < groups[mg].size:
            _fail(where, f"modulator index {mi} out of range for group {mg!r}")
        if tg not in groups:
            _fail(where, f"target group {tg!r} does not exist")
        if tg not in plastic_posts:
            _fail(where, f"target group {tg!r} is not the post group of any plastic projection")
        comp = row["compartment"].strip() or None
        has_range = bool(row["target_from"].strip() or row["target_to"].strip())
        if comp and has_range:
            _fail(where, "give either target_from/target_to or compartment, not both")
        if comp:
            if neurons is None:
                _fail(where, "compartment labels need neurons.csv (with a compartment column)")
            idx = compartments.get(tg, {}).get(comp)
            if not idx:
                _fail(where, f"compartment {comp!r} not found for group {tg!r} in neurons.csv")
            out.append(ModulationRow(mg, mi, tg, min(idx), max(idx) + 1, comp))
            continue
        if not has_range:
            _fail(where, "needs target_from/target_to or compartment")
        a, b = _int(where + " target_from", row["target_from"]), _int(where + " target_to", row["target_to"])
        if not 0 <= a < b <= groups[tg].size:
            _fail(where, f"target range [{a}, {b}) invalid for group {tg!r} (size {groups[tg].size})")
        out.append(ModulationRow(mg, mi, tg, a, b, None))
    return out


# ----------------------------------------------------------------------------- writing (for generators)

def write_circuit(path: str | Path, manifest: dict, groups: list[dict], connections: list[tuple],
                  neurons: list[dict] | None = None, modulation: list[dict] | None = None,
                  notice: str | None = None) -> Circuit:
    """Write a circuit folder and load it back (so a generator can never write an invalid circuit).

    connections: (projection, pre, post, weight, type) tuples.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    m = {"format": FORMAT, **manifest}
    if neurons is not None:
        m["neurons"] = "neurons.csv"
    if modulation is not None:
        m["modulation"] = "modulation.csv"
    (path / "manifest.yaml").write_text(yaml.safe_dump(m, sort_keys=False, allow_unicode=True), encoding="utf-8",
                                        newline="\n")

    def write(name, columns, rows):
        with (path / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(columns)
            for r in rows:
                w.writerow(["" if r.get(c) is None else r.get(c) for c in columns] if isinstance(r, dict) else r)

    write("groups.csv", GROUP_COLUMNS, groups)
    write("connections.csv", CONNECTION_COLUMNS,
          [(p, pre, post, f"{w:.6g}", t) for p, pre, post, w, t in connections])
    if neurons is not None:
        write("neurons.csv", NEURON_COLUMNS, neurons)
    if modulation is not None:
        write("modulation.csv", MODULATION_COLUMNS, modulation)
    if notice:
        (path / "NOTICE").write_text(notice, encoding="utf-8", newline="\n")
    return load_circuit(path)
