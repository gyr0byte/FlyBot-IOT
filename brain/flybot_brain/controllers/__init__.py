"""Controllers: "baseline" (rule-based, Phase 4) and "neural" (Phase 5; a stub for now)."""
from __future__ import annotations

from ..config import BrainConfig
from .base import Controller, Decision, Inputs


def make_controller(cfg: BrainConfig) -> Controller:
    if cfg.controller == "baseline":
        from .baseline import BaselineController
        return BaselineController(cfg.baseline, cfg.seed)
    if cfg.controller == "neural":
        from .neural import NeuralController
        return NeuralController()
    raise ValueError(f"unknown controller {cfg.controller!r}")


__all__ = ["Controller", "Decision", "Inputs", "make_controller"]
