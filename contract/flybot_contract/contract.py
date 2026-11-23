"""The message contract in code: topics, schemas, message building and validation.

Schemas are read from docs/schemas (the source of truth), so a contract change is picked up
without touching this file. Every outgoing message is validated BEFORE its sequence number is
used, so a rejected message never creates a false gap for receivers.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from jsonschema import Draft202012Validator

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


class Contract:
    def __init__(self, robot_id: str = "01", schema_dir: Path = SCHEMA_DIR):
        self.robot_id = robot_id
        self.prefix = f"flybot/{robot_id}/"
        self.validators: dict[str, Draft202012Validator] = {}
        self.versions: dict[str, int] = {}
        for name, stem in TOPIC_SCHEMA.items():
            schema = json.loads((schema_dir / f"{stem}.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            self.validators[name] = Draft202012Validator(schema)
            self.versions[name] = schema["properties"]["v"]["const"]

    def topic(self, name: str) -> str:
        return self.prefix + name

    def name_of(self, topic: str) -> str | None:
        return topic[len(self.prefix):] if topic.startswith(self.prefix) else None

    def validate(self, name: str, msg: dict) -> None:
        errors = sorted(self.validators[name].iter_errors(msg), key=lambda e: list(e.path))
        if errors:
            raise ContractError(f"{name}: {errors[0].message} (at {list(errors[0].path)}) in {msg}")

    def encode(self, name: str, msg: dict) -> bytes:
        self.validate(name, msg)
        return json.dumps(msg, separators=(",", ":")).encode("utf-8")

    def decode(self, name: str, payload: bytes) -> dict:
        try:
            msg = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
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


def offline_status(contract: Contract, up_at_connect: int) -> dict:
    """The robot's last will (contract section 3.3): fixed at connect time, seq 0."""
    msg = {"v": contract.versions["status"], "seq": 0, "up": int(up_at_connect), "st": "offline"}
    contract.validate("status", msg)
    return msg
