"""Contract revision 10: stricter validation in the contract code (no message version changes).

P1: NaN, Infinity and -Infinity are not JSON numbers: rejected when decoding, and never validated or
    encoded (Python's json module would otherwise accept and produce them).
P2: string patterns match the whole string (JSON Schema / ECMA-262 meaning). Python's re "$" would also
    match before a trailing newline, so "bot\\n" passed as a sender name.
"""
from __future__ import annotations

import json
import math

import pytest

from flybot_contract import Contract, ContractError, Outbox

C = Contract()

MOTOR = {"v": 1, "seq": 1, "up": 1, "l": 0.5, "r": -0.5, "ttl": 300, "rs": 0}


def raw(msg: dict, **swap) -> bytes:
    """JSON text of msg with some values replaced by raw JSON tokens."""
    text = json.dumps(msg, separators=(",", ":"))
    for key, token in swap.items():
        text = text.replace(f'"{key}":{json.dumps(msg[key])}', f'"{key}":{token}', 1)
    return text.encode()


# ----------------------------------------------------------------------------- P1: NaN and infinities

@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("field", ["l", "r"])
def test_decode_rejects_nan_and_infinities(token, field):
    with pytest.raises(ContractError):
        C.decode("cmd/motor", raw(MOTOR, **{field: token}))


def test_decode_still_accepts_ordinary_numbers():
    assert C.decode("cmd/motor", raw(MOTOR, l="-1.0", r="1e0"))["r"] == 1.0
    assert C.decode("cmd/motor", raw(MOTOR, ttl="3e2"))["ttl"] == 300.0


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_validate_and_encode_reject_non_finite_floats(bad):
    msg = dict(MOTOR, l=bad)
    with pytest.raises(ContractError):
        C.validate("cmd/motor", msg)
    with pytest.raises(ContractError):
        C.encode("cmd/motor", msg)


def test_outbox_never_builds_a_nan_message_and_keeps_its_seq():
    ob = Outbox(C)
    with pytest.raises(ContractError):
        ob.build("sensors", 100, bid=1, ol=math.nan, **{"or": 0.1}, d=50.0, lt=None, bat=7.4)
    msg = ob.build("sensors", 150, bid=1, ol=0.2, **{"or": 0.1}, d=50.0, lt=None, bat=7.4)
    assert msg["seq"] == 0                      # the rejected message did not use a sequence number


def test_non_finite_values_nested_in_lists_are_rejected():
    state = {"v": 5, "seq": 1, "up": 1, "hun": 0.1, "fear": 0.1, "hab": 0.0, "slp": False, "dec": "seek",
             "why": [], "spk": {"pl": 1}, "les": [], "pref": {"odour_l": math.nan}, "rs": 0, "ack": None}
    with pytest.raises(ContractError):
        C.validate("brain/state", state)


# ----------------------------------------------------------------------------- P2: whole-string patterns

SRC_CASES = [  # (topic, message with a sender name)
    ("cmd/mode", {"v": 2, "seq": 1, "up": 1, "m": "auto", "src": "bot"}),
    ("cmd/mode", {"v": 2, "seq": 1, "up": 1, "m": "estop", "src": "dash-3fa2c1"}),
    ("cmd/manual", {"v": 2, "seq": 1, "up": 1, "l": 0, "r": 0, "ttl": 300, "rs": 0, "src": "dash-3fa2c1"}),
    ("reward", {"v": 3, "seq": 1, "up": 1, "r": 1, "src": "bot"}),
    ("reward", {"v": 3, "seq": 1, "up": 1, "bid": 7, "r": -1, "src": "btn"}),
    ("brain/sleep", {"v": 2, "seq": 1, "up": 1, "s": True, "src": "bot"}),
]


@pytest.mark.parametrize("topic,msg", SRC_CASES)
def test_sender_names_still_valid(topic, msg):
    assert C.decode(topic, C.encode(topic, msg)) == msg


@pytest.mark.parametrize("suffix", ["\n", "\r\n", " ", "x", "\u0000"])
@pytest.mark.parametrize("topic,msg", SRC_CASES)
def test_sender_names_must_match_exactly(topic, msg, suffix):
    bad = dict(msg, src=msg["src"] + suffix)
    with pytest.raises(ContractError):
        C.validate(topic, bad)
    with pytest.raises(ContractError):        # also as JSON text, with "\n" escaped in the payload
        C.decode(topic, json.dumps(bad).encode())


def test_firmware_version_pattern_is_exact_too():
    boot = {"v": 3, "seq": 0, "up": 1, "bid": 7, "e": "boot", "rst": "power_on", "fw": "0.6.0"}
    C.validate("events", boot)
    with pytest.raises(ContractError):
        C.validate("events", dict(boot, fw="0.6.0\n"))


def test_every_schema_pattern_rejects_a_trailing_newline():
    """Walk all schemas: no pattern may accept a value followed by "\\n"."""
    from flybot_contract.contract import _ecma_regex

    found = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("pattern"), str):
                found.append(node["pattern"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for v in C.validators.values():
        walk(v.schema)
    assert found
    samples = {"^dash-[0-9a-f]{6}$": "dash-3fa2c1", "^(bot|dash-[0-9a-f]{6})$": "bot",
               "^(btn|bot|dash-[0-9a-f]{6})$": "btn", "^[0-9A-Za-z.+-]{1,16}$": "0.6.0",
               "^[a-z0-9_-]{1,32}$": "snap_1", "^[a-z][a-z0-9_.]{0,47}$": "kc.gain", "^[a-z][a-z0-9_]{0,15}$": "kc"}
    for p in set(found):
        good = samples[p]
        assert _ecma_regex(p).search(good), p
        assert not _ecma_regex(p).search(good + "\n"), p
