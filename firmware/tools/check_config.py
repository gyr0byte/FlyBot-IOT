"""Check that firmware/include/config.h section B mirrors sim/flybot_sim/edge_config.py.

Every EdgeConfig field must appear in config.h (UPPER_CASE, same value) and nothing extra may be in
section B. Differences are allowed only if listed in ALLOWED_DIFFERENCES with a reason.

    python -B firmware/tools/check_config.py        (exit code 1 on any mismatch)
"""
from __future__ import annotations

import re
import sys
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sim"))
from flybot_sim.edge_config import EdgeConfig  # noqa: E402

CONFIG_H = ROOT / "firmware" / "include" / "config.h"

# field -> (firmware value, reason). The simulator keeps its own value.
ALLOWED_DIFFERENCES = {
    "odour_warmup_ms": (60000, "a real MQ-3 needs about a minute to settle; the simulator uses 5 s "
                               "(its edge_config.py comment says it is shortened for development)"),
}

LINE = re.compile(r"^\s*constexpr\s+(\w+)\s+([A-Z0-9_]+)\s*=\s*([^;]+);")


def section_b(text: str) -> str:
    start = text.index("B. Edge thresholds")
    end = text.index("C. Hardware and timing")
    return text[start:end]


def parse_value(ctype: str, raw: str):
    raw = raw.strip()
    if ctype == "bool":
        return {"true": True, "false": False}[raw]
    if ctype == "double":
        return float(raw)
    return int(raw)


def check() -> list[str]:
    found = {}
    for line in section_b(CONFIG_H.read_text(encoding="utf-8")).splitlines():
        m = LINE.match(line)
        if m:
            found[m.group(2).lower()] = parse_value(m.group(1), m.group(3))
    problems = []
    defaults = EdgeConfig()
    names = [f.name for f in fields(EdgeConfig)]
    for name in names:
        want = getattr(defaults, name)
        if name not in found:
            problems.append(f"missing in config.h: {name.upper()} (sim value {want!r})")
            continue
        got = found[name]
        if name in ALLOWED_DIFFERENCES:
            fw, _ = ALLOWED_DIFFERENCES[name]
            if got != fw:
                problems.append(f"{name.upper()}: config.h {got!r}, allowed difference says {fw!r}")
            continue
        if type(want) is bool or type(got) is bool:
            same = got is want
        else:
            same = float(got) == float(want)
        if not same:
            problems.append(f"{name.upper()}: config.h {got!r} != sim {want!r}")
    for extra in sorted(set(found) - set(names)):
        problems.append(f"in config.h section B but not in EdgeConfig: {extra.upper()}")
    return problems


def main() -> int:
    problems = check()
    for p in problems:
        print("MISMATCH", p)
    n = len(fields(EdgeConfig))
    print(f"{n} edge thresholds compared; {len(ALLOWED_DIFFERENCES)} allowed difference(s); "
          f"{len(problems)} problem(s)")
    for name, (fw, why) in ALLOWED_DIFFERENCES.items():
        print(f"  allowed: {name.upper()} = {fw} ({why})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
