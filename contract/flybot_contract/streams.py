"""Contract section 6 rules 3-8 and 13: per-stream sequence tracking for any receiver.

Used by the brain and by the simulator's firmware logic (and the reference for the ESP32).
Plain Python, integer milliseconds.
"""
from __future__ import annotations

SEQ_MOD = 2 ** 32


class StreamTracker:
    def __init__(self, idle_forget_ms: int = 600000):
        self.idle_forget_ms = idle_forget_ms
        self.streams: dict[tuple, tuple[int, int, int]] = {}  # key -> (seq, up, last_seen_ms)
        self.lost = 0
        self.restarts = 0
        self.dropped = 0
        self.last_restarted = False   # did the most recent accept() see a sender restart?

    def accept(self, key: tuple, seq: int, up: int, now: int) -> bool:
        """True if the message is new and in order. Duplicates and late messages are dropped."""
        self.last_restarted = False
        for k in [k for k, (_, _, seen) in self.streams.items() if now - seen > self.idle_forget_ms]:
            del self.streams[k]                                    # rule 13: forget idle senders
        prev = self.streams.get(key)
        if prev is None:                                           # rule 13: new sender
            self.streams[key] = (seq, up, now)
            return True
        last_seq, last_up, _ = prev
        if up < last_up:                                           # rule 4: sender restarted
            self.restarts += 1
            self.last_restarted = True
            self.streams[key] = (seq, up, now)
            return True
        diff = (seq - last_seq) % SEQ_MOD
        if diff == 0 or diff >= SEQ_MOD // 2:                      # rule 5 duplicate / rule 7 late
            self.dropped += 1
            return False
        self.lost += diff - 1                                      # rules 6 and 8 (wrap-safe)
        self.streams[key] = (seq, up, now)
        return True

    def reset(self, key: tuple) -> None:
        self.streams.pop(key, None)
