"""FIRMWARE sensor processing (port to the ESP32, plan step 6.2).

Turns raw sensor outputs into the values the contract sends:
  - OdourChannel: MQ-3 voltage -> change from this sensor's own baseline, scaled 0-1
    (null until warm-up and calibration finish), with smoothing and slow drift correction.
  - DistanceFilter: raw echo distances -> median of recent valid readings, or None.
  - BatteryMonitor: smoothed pack voltage.

Plain Python, no numpy, integer milliseconds: each class maps directly to a small C++ struct.
"""
from __future__ import annotations

import math

from .edge_config import EdgeConfig


class OdourChannel:
    def __init__(self, cfg: EdgeConfig):
        self.cfg = cfg
        self.start_ms: int | None = None
        self.window: list[tuple[int, float]] = []  # recent (ms, volts) during warm-up
        self.baseline: float | None = None
        self.smoothed: float | None = None
        self.last_ms: int | None = None

    @property
    def ready(self) -> bool:
        return self.baseline is not None

    def feed(self, volts: float, now_ms: int) -> float | None:
        """Process one reading; return the 0-1 value, or None while warming up."""
        cfg = self.cfg
        if self.start_ms is None:
            self.start_ms = now_ms
        dt = 0 if self.last_ms is None else now_ms - self.last_ms
        self.last_ms = now_ms
        self.smoothed = volts if self.smoothed is None else (
            self.smoothed + cfg.odour_smooth_alpha * (volts - self.smoothed))

        if self.baseline is None:
            self.window.append((now_ms, self.smoothed))
            self.window = [(t, v) for t, v in self.window if now_ms - t <= cfg.odour_baseline_window_ms]
            if now_ms - self.start_ms < cfg.odour_warmup_ms:
                return None
            self.baseline = sum(v for _, v in self.window) / len(self.window)
            self.window = []

        value = (self.smoothed - self.baseline) / cfg.odour_full_scale_v
        # Slow drift correction: follow the baseline only in clean air, faster if below it.
        if dt > 0:
            if self.smoothed < self.baseline:
                k = 1.0 - math.exp(-dt / cfg.odour_baseline_down_tau_ms)
                self.baseline += k * (self.smoothed - self.baseline)
            elif value < cfg.odour_clean_threshold:
                k = 1.0 - math.exp(-dt / cfg.odour_baseline_tau_ms)
                self.baseline += k * (self.smoothed - self.baseline)
        return min(max(value, 0.0), 1.0)


class DistanceFilter:
    """Rejects single bad readings: median of the last n valid readings within a time window."""

    def __init__(self, cfg: EdgeConfig):
        self.cfg = cfg
        self.recent: list[tuple[int, float]] = []

    def feed(self, cm: float | None, now_ms: int) -> float | None:
        if cm is not None:
            self.recent.append((now_ms, cm))
        self.recent = [(t, d) for t, d in self.recent if now_ms - t <= self.cfg.dist_valid_ms]
        self.recent = self.recent[-self.cfg.dist_median_n:]
        if not self.recent:
            return None
        vals = sorted(d for _, d in self.recent)
        return vals[len(vals) // 2]


class BatteryMonitor:
    def __init__(self, cfg: EdgeConfig):
        self.cfg = cfg
        self.volts: float | None = None

    def feed(self, volts: float) -> float:
        self.volts = volts if self.volts is None else self.volts + self.cfg.battery_smooth_alpha * (volts - self.volts)
        return self.volts
