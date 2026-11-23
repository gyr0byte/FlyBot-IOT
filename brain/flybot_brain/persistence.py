"""Brain snapshots (brain/control save / load): JSON files in .local/brain/snapshots/ on D:.

A snapshot holds the brain's internal state (hunger, fear, controller state, learned weights in
Phase 5). Parameters are NOT stored: brain/config/brain.yaml stays the source of truth for them.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")   # same rule as the contract's brain/control.name


class Snapshots:
    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def _path(self, name: str) -> Path:
        if not NAME_RE.match(name):
            raise ValueError(f"invalid snapshot name {name!r}")
        return self.dir / f"{name}.json"

    def save(self, name: str, data: dict) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(name)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def load(self, name: str) -> dict:
        return json.loads(self._path(name).read_text(encoding="utf-8"))

    def exists(self, name: str) -> bool:
        return self._path(name).exists()
