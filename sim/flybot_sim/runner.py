"""How the simulation clock relates to the wall clock (contract section 7.1).

run_realtime: simulated time follows the wall clock (live view, latency measurements).
run_lockstep: after each sensors message (seq n) wait for the cmd/motor with rs = n, then go on
              at once; no sleeping, so it runs as fast as the brain answers. Waits at most
              WAIT_MS of real time; after MAX_TIMEOUTS timeouts in a row it runs freely (no
              brain: the watchdog and fallback still work) until a cmd/motor arrives again.
              In free run it still listens PROBE_MS after each sensors message, so a brain that
              comes back is picked up within one or two steps instead of facing a backlog.
"""
from __future__ import annotations

import queue
import time
from typing import Callable

from .sim import Simulation

WAIT_MS = 100
MAX_TIMEOUTS = 3
PROBE_MS = 2.0   # free run: still listen this long after each sensors message


def trial_done(duration_s: float) -> Callable[[Simulation], bool]:
    """A trial ends when the robot reaches the source (after any training reward and its hold) or
    duration_s after boot."""
    return lambda sim: sim.finished(duration_s)


def run_realtime(sim: Simulation, done: Callable[[Simulation], bool] = lambda s: False,
                 on_frame: Callable[[], bool] | None = None, paused: Callable[[], bool] = lambda: False,
                 speed: float = 1.0) -> None:
    """on_frame() is called between batches of steps (about 60 times a second); return False to stop."""
    wall0, sim0 = time.perf_counter(), sim.t_ms
    while not done(sim):
        if paused():
            wall0, sim0 = time.perf_counter(), sim.t_ms       # don't catch up after a pause
        else:
            target = sim0 + (time.perf_counter() - wall0) * 1000.0 * speed
            while sim.t_ms < target and not done(sim):
                sim.step()
        if on_frame is not None:
            if on_frame() is False:
                return
        else:
            time.sleep(0.002)


class LockstepStats:
    def __init__(self):
        self.waits = 0
        self.matched = 0
        self.timeouts = 0
        self.free_run_entries = 0


def run_lockstep(sim: Simulation, done: Callable[[Simulation], bool],
                 wait_ms: int = WAIT_MS, max_timeouts: int = MAX_TIMEOUTS,
                 probe_ms: float = PROBE_MS) -> LockstepStats:
    """Free run is not unpaced: after each sensors message it still listens for probe_ms, so
    sim time cannot race thousands of ms ahead of a brain that starts answering again."""
    st = LockstepStats()
    in_row = 0
    free_run = False
    cmds_at_free_run = 0
    while not done(sim):
        seq = sim.step()
        if free_run and sim.m.motor_cmds > cmds_at_free_run:
            free_run, in_row = False, 0                       # a brain is answering again
        if seq is None or not sim.expect_command():
            continue
        st.waits += 1
        deadline = time.perf_counter() + (probe_ms if free_run else wait_ms) / 1000.0
        matched = False
        while not matched:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            try:
                item = sim.link.inbox.get(timeout=remaining)
            except queue.Empty:
                break
            sim.handle(item)
            matched = item[0] == "msg" and item[1] == "cmd/motor" and item[2]["rs"] == seq
        if matched:
            st.matched += 1
            in_row = 0
            free_run = False
        elif not free_run:
            st.timeouts += 1
            in_row += 1
            if in_row >= max_timeouts:
                free_run = True
                cmds_at_free_run = sim.m.motor_cmds
                st.free_run_entries += 1
    return st
