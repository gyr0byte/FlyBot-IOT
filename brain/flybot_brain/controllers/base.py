"""Controller interface. A controller turns one tick's inputs into wheel commands + a decision.

Controllers know nothing about MQTT, wall-clock time or the contract envelope: the core gives
them robot time (t_ms) and the integrated step (dt_ms, already gap-capped, contract 7.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Inputs:
    ol: float            # left odour, 0-1 (change from baseline)
    orr: float           # right odour, 0-1 ("or" is a Python keyword)
    d: float | None      # distance, cm (None = no echo)
    lt: float | None     # light 0-1 (None = no LDR)
    bat: float           # pack voltage
    dt_ms: int           # robot time integrated this tick
    t_ms: int            # monotonic robot time


@dataclass
class Decision:
    l: float
    r: float
    dec: str                                   # contract brain/state.dec value
    why: list[str] = field(default_factory=list)  # up to 3 contributors, strongest first


class Controller:
    name = "base"

    def reset(self) -> None:
        """Forget internal state (and re-seed randomness) as if freshly started."""

    def step(self, inp: Inputs) -> Decision:
        raise NotImplementedError

    def spikes(self) -> dict[str, int]:
        """Spike counts per group for brain/state.spk ({} for non-spiking controllers)."""
        return {}

    def state_dict(self) -> dict:
        return {}

    def load_state(self, state: dict) -> None:
        pass
