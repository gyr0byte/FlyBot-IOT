"""Contract section 7.1 (3) and (4): monotonic robot time built from sensors.up.

  - positive steps between consecutive up values are added;
  - a reboot step counts 0 and is reported, so the caller can reset its per-sender seq state
    (and, if configured, its internal state). A reboot is a change of sensors.bid (revision 6);
    without a bid (older callers, tests) it is a decrease of up;
  - within one boot up never steps back (late messages are dropped before the clock); if it
    did, the step would count 0 without being reported as a reboot;
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
        self.last_bid: int | None = None
        self.ms = 0          # monotonic robot time
        self.step = 0        # what the last advance() integrated
        self.gaps = 0
        self.reboots = 0
        self.rebooted = False   # did the last advance() see a reboot?

    def advance(self, up: int, bid: int | None = None) -> int:
        self.rebooted = False
        step = 0
        if self.last_up is not None:
            if bid is not None or self.last_bid is not None:
                reboot = bid != self.last_bid
            else:
                reboot = up < self.last_up
            if reboot:
                self.reboots += 1
                self.rebooted = True
            else:
                step = max(0, up - self.last_up)
                if step > self.gap_ms:
                    self.gaps += 1
                    step = self.gap_cap_ms
        self.last_up = up
        self.last_bid = bid
        self.step = step
        self.ms += step
        return self.ms
