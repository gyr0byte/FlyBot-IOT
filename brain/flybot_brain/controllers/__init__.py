"""Controllers: "baseline" (rule-based, Phase 4) and "neural" (spiking network, Phase 5)."""
from __future__ import annotations

from ..config import BrainConfig
from .base import Controller, Decision, Inputs


def make_controller(cfg: BrainConfig) -> Controller:
    if cfg.controller == "baseline":
        from .baseline import BaselineController
        return BaselineController(cfg.baseline, cfg.motor, cfg.seed)
    if cfg.controller == "neural":
        from .neural import NeuralController
        return NeuralController(cfg)
    raise ValueError(f"unknown controller {cfg.controller!r}")


__all__ = ["Controller", "Decision", "Inputs", "make_controller"]
