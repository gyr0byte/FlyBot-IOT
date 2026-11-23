"""Learning experiment (plan step 5.6): conditions, per-animal scenarios and the analysis.

Pure functions only; sim/tools/learning_experiment.py runs the brain processes and MQTT.

Design: each animal is a fresh brain process (naive weights) that runs every phase of the scenario
(pre-test, training, post-test) and keeps what it learns between trials. Odd animals get the
mirrored arena (lamp on the right), so a side bias cannot look like learning. Animal k has the same
seeds and side in every condition (a paired design across conditions).

PI = (lit - dark) / (lit + dark) over the trials of a phase that reached a source (timeouts are
reported separately); undefined when no source was reached.
"""
from __future__ import annotations

import copy
import math
import statistics
from dataclasses import dataclass

from .config import Scenario, mirror_scenario

CONDITIONS: dict[str, dict] = {
    "punish_lit":     {"rewards": {"lit": -1}, "delay_s": 0.0},
    "reward_lit":     {"rewards": {"lit": 1},  "delay_s": 0.0},
    "control":        {"rewards": {},          "delay_s": 0.0},   # same training starts, no reinforcement
    "delayed_punish": {"rewards": {"lit": -1}, "delay_s": 1.5},
}
EXPECTED_SIGN = {"punish_lit": -1, "reward_lit": 1, "control": 0, "delayed_punish": -1}
TRAIN_PHASE = "train"


def animal_scenario(base: Scenario, condition: str, k: int) -> tuple[Scenario, bool]:
    """Scenario for animal k (0-based) in a condition, and whether it is mirrored."""
    cond = CONDITIONS[condition]
    sc = copy.deepcopy(base)
    sc.seed = base.seed + 1000 * k
    mirrored = k % 2 == 1
    if mirrored:
        sc = mirror_scenario(sc)
    sc.name = f"{base.name}-{condition}-a{k:02d}"
    train = [p for p in sc.experiment.phases if p.name == TRAIN_PHASE]
    if len(train) != 1:
        raise ValueError(f"scenario {base.name!r} needs exactly one phase named {TRAIN_PHASE!r}")
    for p in sc.experiment.phases:
        p.rewards = dict(cond["rewards"]) if p.name == TRAIN_PHASE else {}
    sc.experiment.reward_delay_s = cond["delay_s"]
    return sc, mirrored


def preference_index(lit: int, dark: int) -> float | None:
    return None if lit + dark == 0 else (lit - dark) / (lit + dark)


@dataclass
class PhaseSummary:
    n: int
    lit: int
    dark: int
    timeout: int
    rewards: int

    @property
    def pi(self) -> float | None:
        return preference_index(self.lit, self.dark)

    def rate(self, what: str) -> float:
        return getattr(self, what) / self.n if self.n else float("nan")


def summarize_phase(rows: list[dict]) -> PhaseSummary:
    labels = [r.get("reached_label") or "" for r in rows]
    return PhaseSummary(n=len(rows), lit=labels.count("lit"), dark=labels.count("dark"),
                        timeout=sum(1 for x in labels if x == ""),
                        rewards=sum(1 for r in rows if r.get("reward_sent") not in (None, "")))


def sign_test(n_pos: int, n_neg: int) -> float:
    """Two-sided exact sign test (ties already dropped)."""
    n = n_pos + n_neg
    if n == 0:
        return 1.0
    k = min(n_pos, n_neg)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def weight_means(msg: dict | None) -> tuple[float | None, float | None]:
    """Mean KC->approach and KC->avoid weight from a brain/weights message (0..w_max)."""
    if not msg or not msg.get("ap"):
        return None, None
    s = msg["sc"] / 255.0
    return round(statistics.fmean(msg["ap"]) * s, 4), round(statistics.fmean(msg["av"]) * s, 4)


# ----------------------------------------------------------------------------- report

def _f(x, fmt="{:+.2f}") -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else fmt.format(x)


def _mean(xs) -> float | None:
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return statistics.fmean(xs) if xs else None


def _num(x) -> float | None:
    return None if x in (None, "") else float(x)


def report(records: list[dict]) -> str:
    """Markdown report from per-animal-per-phase records (as written by the experiment tool)."""
    out = ["# Learning experiment", ""]
    conditions = [c for c in CONDITIONS if any(r["condition"] == c for r in records)]
    by = {(r["condition"], int(r["animal"]), r["phase"]): r for r in records}
    phases = list(dict.fromkeys(r["phase"] for r in records))

    out += ["## Per condition (means over animals)", "",
            "| condition | animals | phase | lit rate | dark rate | timeouts | PI | pref.lt | pref.odr | w KC→ap | w KC→av |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in conditions:
        animals = sorted({int(r["animal"]) for r in records if r["condition"] == c})
        for ph in phases:
            rs = [by[(c, a, ph)] for a in animals if (c, a, ph) in by]
            if not rs:
                continue
            n = sum(int(r["n"]) for r in rs)
            out.append(
                f"| {c} | {len(rs)} | {ph} | {_f(sum(int(r['lit']) for r in rs) / n, '{:.2f}')} | "
                f"{_f(sum(int(r['dark']) for r in rs) / n, '{:.2f}')} | "
                f"{_f(sum(int(r['timeout']) for r in rs) / n, '{:.2f}')} | {_f(_mean(_num(r['pi']) for r in rs))} | "
                f"{_f(_mean(_num(r['pref_lt']) for r in rs))} | {_f(_mean(_num(r['pref_odr']) for r in rs))} | "
                f"{_f(_mean(_num(r['w_ap']) for r in rs), '{:.3f}')} | {_f(_mean(_num(r['w_av']) for r in rs), '{:.3f}')} |")
    out.append("")

    out += ["## Change from pre-test to post-test (paired per animal)", "",
            "ΔPI uses animals with a defined PI in both tests. Sign test: two-sided, ties dropped.", "",
            "| condition | expected | ΔPI mean | up / down / tie | sign test p | Δ lit rate | Δ dark rate | Δ timeouts |",
            "|---|---|---|---|---|---|---|---|"]
    for c in conditions:
        animals = sorted({int(r["animal"]) for r in records if r["condition"] == c})
        pairs = [(by[(c, a, "pre")], by[(c, a, "post")]) for a in animals
                 if (c, a, "pre") in by and (c, a, "post") in by]
        d_pi = [_num(q["pi"]) - _num(p["pi"]) for p, q in pairs if _num(p["pi"]) is not None and _num(q["pi"]) is not None]
        up, down = sum(d > 0 for d in d_pi), sum(d < 0 for d in d_pi)

        def d_rate(key):
            return _mean(int(q[key]) / int(q["n"]) - int(p[key]) / int(p["n"]) for p, q in pairs)
        exp = {1: "PI up", -1: "PI down", 0: "no change"}[EXPECTED_SIGN.get(c, 0)]
        out.append(f"| {c} | {exp} | {_f(_mean(d_pi))} | {up} / {down} / {len(d_pi) - up - down} | "
                   f"{sign_test(up, down):.3f} | {_f(d_rate('lit'))} | {_f(d_rate('dark'))} | {_f(d_rate('timeout'))} |")
    out.append("")

    out += ["## Per animal", "",
            "| condition | animal | side | phase | lit | dark | timeout | rewards | PI | pref.lt | pref.odr | w KC→ap | w KC→av |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in conditions:
        for r in sorted((r for r in records if r["condition"] == c), key=lambda r: (int(r["animal"]), phases.index(r["phase"]))):
            out.append(f"| {c} | {r['animal']} | {'right' if r['mirrored'] in (True, 'True') else 'left'} | {r['phase']} | "
                       f"{r['lit']} | {r['dark']} | {r['timeout']} | {r['rewards']} | {_f(_num(r['pi']))} | "
                       f"{_f(_num(r['pref_lt']))} | {_f(_num(r['pref_odr']))} | {_f(_num(r['w_ap']), '{:.3f}')} | "
                       f"{_f(_num(r['w_av']), '{:.3f}')} |")
    out.append("")
    return "\n".join(out)
