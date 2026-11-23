"""Neural controller: a stub until Phase 5. It keeps the robot still."""
from __future__ import annotations

from .base import Controller, Decision, Inputs


class NeuralController(Controller):
    name = "neural"

    def step(self, inp: Inputs) -> Decision:
        return Decision(0.0, 0.0, "freeze", ["neural_stub"])
