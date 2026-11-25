"""Validate the C++ message builders' output with the Python contract (run after pio test -e native).

firmware/test/test_messages writes firmware/.pio/messages_out.txt: one message per line plus the
inputs it was built from. Every message must pass the contract schema, and every number must be what
Python's round() gives for the (clamped) input, so the robot's messages match the simulator's.

    sim\\.venv\\Scripts\\python -B firmware\\tools\\check_messages.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contract"))
from flybot_contract import Contract, ContractError  # noqa: E402

C = Contract()
OUT = ROOT / "firmware" / ".pio" / "messages_out.txt"


def h(s: str):
    return None if s == "-" else struct.unpack(">d", bytes.fromhex(s))[0]


def expect(v, dec, lo, hi):
    if v is None:
        return None
    return round(max(lo, min(hi, v)), dec) + 0.0   # + 0.0: never -0.0


def main() -> int:
    if not OUT.exists():
        print(f"{OUT} missing: run pio test -e native first")
        return 1
    bad = 0
    n = 0
    for line in OUT.read_text(encoding="utf-8").splitlines():
        topic, rest = line.split(" ", 1)
        text, spec = rest.split(" | ")
        parts = spec.split()
        n += 1
        try:
            msg = C.decode(topic, text.encode())
        except ContractError as e:
            print("INVALID", topic, text, e)
            bad += 1
            continue
        seq, up, bid = int(parts[0]), int(parts[1]), int(parts[2])
        if topic == "status" and msg["st"] == "offline":
            seq = 0                                    # the last will is fixed at connect: seq 0 (3.3)
        problems = []
        if msg["seq"] != seq or msg["up"] != up or msg.get("bid", bid) != bid:
            problems.append("header")
        if topic == "sensors":
            ol, orr, d, lt, bat = (h(x) for x in parts[3:8])
            for key, v, dec, lo, hi in (("ol", ol, 3, 0, 1), ("or", orr, 3, 0, 1), ("d", d, 1, 2, 400),
                                        ("lt", lt, 2, 0, 1), ("bat", bat, 2, 0, 10)):
                if msg[key] != expect(v, dec, lo, hi):
                    problems.append(f"{key}: {msg[key]} != {expect(v, dec, lo, hi)}")
        elif topic == "events" and len(parts) > 3:
            d, lat, resp, hab, bat = (h(x) for x in parts[3:8])
            want = {"escape": {"d": expect(d, 1, 2, 400), "resp": expect(resp, 2, 0, 1), "hab": expect(hab, 2, 0, 1)},
                    "obstacle": {"d": expect(d, 1, 2, 400)}, "low_battery": {"bat": expect(bat, 2, 0, 10)}}.get(msg["e"], {})
            if msg["e"] == "escape" and resp > 0:
                want["lat"] = expect(lat, 1, 0, 1000)
            for key, v in want.items():
                if msg.get(key) != v:
                    problems.append(f"{key}: {msg.get(key)} != {v}")
        elif topic == "status" and msg["st"] != "offline":
            bat, rtt, crtt, hab = (h(x) for x in parts[3:7])
            for key, v, dec, lo, hi in (("bat", bat, 2, 0, 10), ("rtt", rtt, 1, 0, 60000), ("crtt", crtt, 1, 0, 60000),
                                        ("hab", hab, 2, 0, 1)):
                if msg[key] != expect(v, dec, lo, hi):
                    problems.append(f"{key}: {msg[key]} != {expect(v, dec, lo, hi)}")
        if "-0.0" in text:
            problems.append("negative zero")
        if problems:
            bad += 1
            print("WRONG", topic, text, "; ".join(problems))
    print(f"{n} C++-built messages: {n - bad} valid with the expected values, {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
