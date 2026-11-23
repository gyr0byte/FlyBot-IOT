"""Contract section 7.1 (3) and (4): monotonic robot time built from sensors.up.

  - positive steps between consecutive up values are added;
  - a decrease (robot reboot) counts as 0 and is reported, so the caller can reset its
    per-sender seq state (and, if configured, its internal state);
  - a step larger than GAP_MS is a data gap: only GAP_CAP_MS is integrated and the gap is counted.
"""
from __future__ import annotations

GAP_MS = 500
GAP_CAP_MS = 100


class RobotClock:
    def __init__(self, gap_ms: int = GAP_MS, gap_cap_ms: int = GAP_CAP_MS):
        self.gap_ms = gap_ms
        self.gap_cap_ms = gap_cap_ms
        self.last_up: int | None = None
        self.ms = 0          # monotonic robot time
        self.step = 0        # what the last advance() integrated
        self.gaps = 0
        self.reboots = 0
        self.rebooted = False   # did the last advance() see a reboot?

    def advance(self, up: int) -> int:
        self.rebooted = False
        step = 0
        if self.last_up is not None:
            step = up - self.last_up
            if step < 0:
                self.reboots += 1
                self.rebooted = True
                step = 0
            elif step > self.gap_ms:
                self.gaps += 1
                step = self.gap_cap_ms
        self.last_up = up
        self.step = step
        self.ms += step
        return self.ms
