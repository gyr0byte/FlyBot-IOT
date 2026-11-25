"""The message contract in code: topics, schemas, message building and validation.

Schemas are read from docs/schemas (the source of truth), so a contract change is picked up
without touching this file. Every outgoing message is validated BEFORE its sequence number is
used, so a rejected message never creates a false gap for receivers.
"""
from __future__ import annotations

import functools
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import ValidationError

REPO = Path(__file__).resolve().parents[2]   # installed editable, so this is the repo checkout
CONTRACT_MD = REPO / "docs" / "message-contract.md"
SCHEMA_DIR = REPO / "docs" / "schemas"
# topic name (after flybot/<id>/) -> schema file stem
TOPIC_SCHEMA = {
    "sensors": "sensors", "events": "events", "status": "status",
    "cmd/motor": "cmd-motor", "cmd/manual": "cmd-manual", "cmd/mode": "cmd-mode", "cmd/sleep": "cmd-sleep",
    "reward": "reward", "brain/status": "brain-status", "brain/state": "brain-state",
    "brain/weights": "brain-weights", "brain/control": "brain-control", "brain/sleep": "brain-sleep",
    "ping": "ping", "pong": "pong",
}


class ContractError(ValueError):
    """A message does not match its schema."""


@functools.lru_cache(maxsize=None)
def _ecma_regex(pattern: str) -> re.Pattern:
    r"""A JSON Schema pattern with ECMA-262 meaning (contract revision 10): "$" matches only at the very
    end of the string. Python's "$" also matches just before a trailing newline, which would accept
    "bot\n" as a sender name. Unescaped "$" outside character classes becomes \Z."""
    out, i, in_class = [], 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            out.append(pattern[i:i + 2])
            i += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        out.append(r"\Z" if c == "$" and not in_class else c)
        i += 1
    return re.compile("".join(out))


def _pattern(validator, pattern, instance, schema):
    if validator.is_type(instance, "string") and not _ecma_regex(pattern).search(instance):
        yield ValidationError(f"{instance!r} does not match {pattern!r}")


# Draft 2020-12 with whole-string patterns (the meaning every JSON Schema implementation but Python's
# re gives them, e.g. the dashboard's JavaScript).
StrictValidator = validators.extend(Draft202012Validator, {"pattern": _pattern})


def _reject_constant(name: str):
    raise ValueError(f"{name} is not a JSON number")


def _non_finite(value) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_non_finite(v) for v in value.values())
    if isinstance(value, list):
        return any(_non_finite(v) for v in value)
    return False


class Contract:
    def __init__(self, robot_id: str = "01", schema_dir: Path = SCHEMA_DIR):
        self.robot_id = robot_id
        self.prefix = f"flybot/{robot_id}/"
        self.validators: dict[str, Draft202012Validator] = {}
        self.versions: dict[str, int] = {}
        for name, stem in TOPIC_SCHEMA.items():
            schema = json.loads((schema_dir / f"{stem}.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            self.validators[name] = StrictValidator(schema)
            self.versions[name] = schema["properties"]["v"]["const"]

    def topic(self, name: str) -> str:
        return self.prefix + name

    def name_of(self, topic: str) -> str | None:
        return topic[len(self.prefix):] if topic.startswith(self.prefix) else None

    def validate(self, name: str, msg: dict) -> None:
        if _non_finite(msg):   # revision 10: NaN and infinities are never valid numbers
            raise ContractError(f"{name}: NaN or infinity in {msg}")
        errors = sorted(self.validators[name].iter_errors(msg), key=lambda e: list(e.path))
        if errors:
            raise ContractError(f"{name}: {errors[0].message} (at {list(errors[0].path)}) in {msg}")

    def encode(self, name: str, msg: dict) -> bytes:
        self.validate(name, msg)
        return json.dumps(msg, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def decode(self, name: str, payload: bytes) -> dict:
        try:
            # strict JSON: Python would otherwise accept NaN, Infinity and -Infinity
            msg = json.loads(payload.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError) as e:   # JSONDecodeError is a ValueError
            raise ContractError(f"{name}: not JSON ({e})") from e
        self.validate(name, msg)
        return msg


class Outbox:
    """Builds outgoing messages: v from the schema, seq per topic (from 0), up from the caller."""

    def __init__(self, contract: Contract):
        self.contract = contract
        self.next_seq: dict[str, int] = defaultdict(int)

    def build(self, name: str, up: int, **fields) -> dict:
        msg = {"v": self.contract.versions[name], "seq": self.next_seq[name], "up": int(up), **fields}
        self.contract.validate(name, msg)          # raises ContractError; seq not consumed
        self.next_seq[name] = (self.next_seq[name] + 1) % 2 ** 32
        return msg


def offline_status(contract: Contract, up_at_connect: int, name: str = "status", bid: int | None = None) -> dict:
    """A last-will payload, fixed at connect time with seq 0: the robot's `status` (section 3.3,
    with its boot id `bid`) or the brain's `brain/status` (section 3.9, no bid)."""
    if name not in ("status", "brain/status"):
        raise ValueError(f"no last-will form for {name}")
    msg = {"v": contract.versions[name], "seq": 0, "up": int(up_at_connect)}
    if bid is not None:
        msg["bid"] = int(bid)
    msg["st"] = "offline"
    contract.validate(name, msg)
    return msg


ROBOT_TOPICS = ("sensors", "events", "status", "ping")   # always carry bid; reward only from "btn"


def new_boot_id() -> int:
    """A random boot id, 1 .. 4294967295 (contract section 2). Unique per boot, NOT seeded:
    two boots must never share one, even in reproducible simulator runs."""
    import secrets
    return secrets.randbelow(2 ** 32 - 1) + 1
