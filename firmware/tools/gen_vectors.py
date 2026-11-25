"""Generate cross-check vectors from the Python reference (sim/flybot_sim/edge.py, edge_sensing.py and
flybot_contract.StreamTracker) for the C++ port (firmware/test/test_crosscheck).

Run with the simulator's venv (Python 3.12, as the brain and simulator use):
    sim\\.venv\\Scripts\\python -B firmware\\tools\\gen_vectors.py

Vectors record every input call in order, every random number drawn, and every output, so the C++
port can replay them and must reproduce the outputs exactly (doubles as their 64-bit patterns).

Edge vector lines (one call per line; d = double as 16 hex digits of its IEEE bits, "-" = None):
  cfg <field> <d>                       EdgeConfig field (all fields, first)
  r <d>                                 a random number the NEXT call draws (in order)
  link <t> <0|1>
  mode <t> <retained> <m> <src> <seq> <up>
  motor <t> <seq> <up> <l:d> <r:d> <ttl> <rs>
  manual <t> <seq> <up> <l:d> <r:d> <ttl> <rs> <src>
  sleep <t> <seq> <up> <s> <why>
  inv <topic>
  dist <t> <raw:d|-> <filtered:d|->
  bat <t> <v:d>
  odr <0|1>
  clr                                   the program cleared status_dirty (after publishing status)
  chk <state> <mode> <dirty>            expected right after the previous (non-update) call
  U <t> <l:d> <r:d> <state> <mode> <dirty> <hab:d>     update(t) and its expected results
  u <t>                                 update(t); results as in the last U line (hab not checked)
  pop <n>                               pop_events() returns these n events:
  ev <type> <key>=<value> ...
  end <escapes> <fallback_ms> <obstacle_stops> <looms> <habituated> <lost> <dropped> <restarts>
"""
from __future__ import annotations

import math
import random
import struct
import sys
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sim"))
sys.path.insert(0, str(ROOT / "contract"))

from flybot_contract import Contract, StreamTracker  # noqa: E402
from flybot_sim import edge as edge_mod  # noqa: E402
from flybot_sim import edge_sensing, sim as sim_mod  # noqa: E402
from flybot_sim.config import load_scenario  # noqa: E402
from flybot_sim.edge_config import EdgeConfig  # noqa: E402
from flybot_sim.link import FakeLink  # noqa: E402

OUT = ROOT / "firmware" / "test" / "vectors"
C = Contract()
FULL_EVERY = 50          # a full U line at least every 50 updates (checks the habituation level)


def d(x) -> str:
    if x is None:
        return "-"
    return struct.pack(">d", float(x)).hex()


def b(x) -> str:
    return "1" if x else "0"


class Trace:
    """Collects the lines of one vector file."""

    def __init__(self, name: str, cfg: EdgeConfig, comment: str):
        self.name = name
        self.lines = [f"# {comment}"] + [f"cfg {f.name} {d(getattr(cfg, f.name))}" for f in fields(EdgeConfig)]
        self.rands: list[float] = []
        self.last_full = None
        self.since_full = 0
        self.updates = 0

    def call(self, line: str, edge=None, check: bool = True) -> None:
        self.lines += [f"r {d(v)}" for v in self.rands]
        self.rands = []
        self.lines.append(line)
        if check and edge is not None:
            self.lines.append(f"chk {edge.state} {edge.mode} {b(edge.status_dirty)}")

    def update(self, edge, t: int, out) -> None:
        self.lines += [f"r {d(v)}" for v in self.rands]
        self.rands = []
        self.updates += 1
        key = (out[0], out[1], edge.state, edge.mode, edge.status_dirty)
        self.since_full += 1
        if key != self.last_full or self.since_full >= FULL_EVERY:
            self.lines.append(f"U {t} {d(out[0])} {d(out[1])} {edge.state} {edge.mode} {b(edge.status_dirty)} "
                              f"{d(edge.hab_level(t))}")
            self.last_full, self.since_full = key, 0
        else:
            self.lines.append(f"u {t}")

    def events(self, evs: list[dict]) -> None:
        if not evs and self.lines[-1].startswith(("U ", "u ")):
            return                                     # nothing popped: no line needed
        self.lines.append(f"pop {len(evs)}")
        for ev in evs:
            parts = [f"ev {ev['e']}"]
            for k, v in ev.items():
                if k == "e":
                    continue
                if isinstance(v, bool):
                    parts.append(f"{k}={b(v)}")
                elif isinstance(v, int):
                    parts.append(f"{k}={v}")
                elif isinstance(v, float):
                    parts.append(f"{k}={d(v)}")
                else:
                    parts.append(f"{k}={v}")
            self.lines.append(" ".join(parts))

    def finish(self, edge) -> None:
        s, st = edge.stats, edge.streams
        self.lines.append(f"end {s['escapes']} {s['fallback_ms']} {s['obstacle_stops']} {s['looms']} "
                          f"{s['habituated']} {st.lost} {st.dropped} {st.restarts}")
        path = OUT / f"edge_{self.name}.txt"
        path.write_text("\n".join(self.lines) + "\n", encoding="ascii", newline="\n")
        print(f"  {path.name}: {self.updates} updates, {len(self.lines)} lines, {path.stat().st_size // 1024} KB")


class RecEdge(edge_mod.Edge):
    """The Python Edge, recording every call into a Trace."""

    trace: Trace | None = None

    def __init__(self, cfg, rand):
        object.__setattr__(self, "_dirty", True)
        tr = RecEdge.trace

        def recorded_rand():
            v = rand()
            tr.rands.append(v)
            return v

        super().__init__(cfg, recorded_rand)
        self.tr = tr

    @property
    def status_dirty(self):
        return self._dirty

    @status_dirty.setter
    def status_dirty(self, v):
        if not v and self._dirty and getattr(self, "tr", None) is not None:
            self._dirty = False
            self.tr.call("clr", self)
            return
        self._dirty = v

    def on_link(self, up, now):
        super().on_link(up, now)
        self.tr.call(f"link {now} {b(up)}", self)

    def set_odour_ready(self, ready):
        changed = ready != self.odour_ready
        super().set_odour_ready(ready)
        if changed:
            self.tr.call(f"odr {b(ready)}", self)

    def on_mode(self, msg, now, retained=False):
        super().on_mode(msg, now, retained)
        self.tr.call(f"mode {now} {b(retained)} {msg['m']} {msg['src']} {msg['seq']} {msg['up']}", self)

    def on_motor(self, msg, now):
        super().on_motor(msg, now)
        self.tr.call(f"motor {now} {msg['seq']} {msg['up']} {d(msg['l'])} {d(msg['r'])} {msg['ttl']} {msg['rs']}", self)

    def on_manual(self, msg, now):
        super().on_manual(msg, now)
        self.tr.call(f"manual {now} {msg['seq']} {msg['up']} {d(msg['l'])} {d(msg['r'])} {msg['ttl']} {msg['rs']} "
                     f"{msg['src']}", self)

    def on_sleep(self, msg, now):
        super().on_sleep(msg, now)
        self.tr.call(f"sleep {now} {msg['seq']} {msg['up']} {b(msg['s'])} {msg['why']}", self)

    def on_invalid(self, topic):
        super().on_invalid(topic)
        self.tr.call(f"inv {topic}", self)

    def on_distance(self, raw_cm, filtered_cm, now):
        super().on_distance(raw_cm, filtered_cm, now)
        self.tr.call(f"dist {now} {d(raw_cm)} {d(filtered_cm)}", self)

    def on_battery(self, volts, now):
        super().on_battery(volts, now)
        self.tr.call(f"bat {now} {d(volts)}", self)

    def update(self, now):
        out = super().update(now)
        self.tr.update(self, now, out)
        return out

    def pop_events(self):
        ev = super().pop_events()
        self.tr.events(ev)
        return ev


# ----------------------------------------------------------------------------- sensing recorders

class SensingTrace:
    def __init__(self, name: str, cfg: EdgeConfig):
        self.name = name
        self.lines = [f"cfg {f.name} {d(getattr(cfg, f.name))}" for f in fields(EdgeConfig)]
        self.next_ch = 0

    def save(self) -> None:
        path = OUT / f"sensing_{self.name}.txt"
        path.write_text("\n".join(self.lines) + "\n", encoding="ascii", newline="\n")
        print(f"  {path.name}: {len(self.lines)} lines, {path.stat().st_size // 1024} KB")


class RecOdour(edge_sensing.OdourChannel):
    st: SensingTrace | None = None

    def __init__(self, cfg):
        super().__init__(cfg)
        self.ch = RecOdour.st.next_ch
        RecOdour.st.next_ch += 1

    def feed(self, volts, now_ms):
        v = super().feed(volts, now_ms)
        RecOdour.st.lines.append(f"o {self.ch} {now_ms} {d(volts)} {d(v)}")
        return v


class RecDist(edge_sensing.DistanceFilter):
    def feed(self, cm, now_ms):
        v = super().feed(cm, now_ms)
        RecOdour.st.lines.append(f"f {now_ms} {d(cm)} {d(v)}")
        return v


class RecBattery(edge_sensing.BatteryMonitor):
    def feed(self, volts):
        v = super().feed(volts)
        RecOdour.st.lines.append(f"b {d(volts)} {d(v)}")
        return v


# ----------------------------------------------------------------------------- simulator runs

class FakeBrain:
    """Answers the simulator's sensors with cmd/motor like a brain would, with scripted trouble:
    lost, duplicated and invalid commands, outages, mode changes, sleep, link drops, looming."""

    def __init__(self, sim, link, rng: random.Random, script: dict):
        self.sim, self.link, self.rng, self.script = sim, link, rng, script
        self.seen = 0
        self.seq = {"motor": 0, "mode": 0, "sleep": 0, "manual": 0}
        self.last_manual = -10**9
        self.last_sleep = -10**9
        self.mode = "auto"
        self.link_up = True

    def _send(self, name, **kw):
        key = {"cmd/motor": "motor", "cmd/mode": "mode", "cmd/sleep": "sleep", "cmd/manual": "manual"}[name]
        msg = {"v": C.versions[name], "seq": self.seq[key], "up": self.sim.t_ms + 12345, **kw}
        self.seq[key] += 1
        self.link.send(name, msg)
        return msg

    def in_window(self, key, t):
        return any(a <= t < b_ for a, b_ in self.script.get(key, []))

    def step(self):
        t = self.sim.t_ms
        sc = self.script
        for at, m in sc.get("modes", []):
            if t == at:
                self.mode = m
                self._send("cmd/mode", m=m, src="dash-3fa2c1")
        for at in sc.get("loom", []):
            if t == at[0]:
                self.sim.sc.looming.speed_mps = at[1]
                self.sim.launch_looming()
        for at, up in sc.get("link", []):
            if t == at:
                self.link_up = up
                self.link.connected = up
                self.link.inbox.put(("link", up))
        if self.in_window("sleep", t) and t - self.last_sleep >= 5000:
            self.last_sleep = t
            self._send("cmd/sleep", s=True, why="dark")
        for at in sc.get("wake", []):
            if t == at:
                self._send("cmd/sleep", s=False, why="light")
        if self.mode == "manual" and t - self.last_manual >= 100:
            self.last_manual = t
            phase = (t // 1500) % 3
            l, r = [(0.5, 0.5), (0.4, -0.4), (0.0, 0.0)][phase]
            self._send("cmd/manual", l=l, r=r, ttl=300, rs=0, src="dash-3fa2c1")
        pub = self.link.published
        while self.seen < len(pub):
            name, msg = pub[self.seen]
            self.seen += 1
            if name != "sensors" or self.in_window("outage", t) or self.mode != "auto":
                continue
            if msg["ol"] is None:
                continue                                 # warm-up: the brain sends nothing
            u = self.rng.random()
            if u < 0.02:
                continue                                 # lost
            steer = (msg["ol"] - msg["or"]) * 2.0
            l = round(max(-1.0, min(1.0, 0.45 - steer + self.rng.uniform(-0.05, 0.05))), 2)
            r = round(max(-1.0, min(1.0, 0.45 + steer + self.rng.uniform(-0.05, 0.05))), 2)
            if u < 0.025:
                try:
                    self.link.send("cmd/motor", {"v": 1, "seq": self.seq["motor"], "up": t, "l": 3.0, "r": 0.0,
                                                 "ttl": 300, "rs": msg["seq"]})
                except Exception:
                    pass
                continue                                 # invalid (schema), counted by the robot
            sent = self._send("cmd/motor", l=l, r=r, ttl=300, rs=msg["seq"])
            if u > 0.99:
                self.link.send("cmd/motor", sent)        # duplicate (same seq)


def run_sim(name: str, scenario: str, seconds: float, script: dict, seed: int, start=None, comment=""):
    sc = load_scenario(scenario)
    cfg = EdgeConfig.with_overrides(sc.edge)
    tr = Trace(name, cfg, comment or f"simulator run: scenario {scenario}, seed {seed}, {seconds} s")
    st = SensingTrace(name, cfg)
    RecEdge.trace, RecOdour.st = tr, st
    saved = sim_mod.Edge, sim_mod.OdourChannel, sim_mod.DistanceFilter, sim_mod.BatteryMonitor
    sim_mod.Edge, sim_mod.OdourChannel, sim_mod.DistanceFilter, sim_mod.BatteryMonitor = (
        RecEdge, RecOdour, RecDist, RecBattery)
    try:
        link = FakeLink(C, connected=True)
        sim = sim_mod.Simulation(sc, link, C, seed=seed, start=start)
        if script.get("retained_mode"):
            link.send("cmd/mode", {"v": 2, "seq": 7, "up": 999, "m": script["retained_mode"], "src": "bot"},
                      retained=True)
        brain = FakeBrain(sim, link, random.Random(seed), script)
        steps = int(seconds * 1000 / sim_mod.PHYSICS_MS)
        for _ in range(steps):
            brain.step()
            sim.step()
        tr.finish(sim.edge)
        st.save()
    finally:
        sim_mod.Edge, sim_mod.OdourChannel, sim_mod.DistanceFilter, sim_mod.BatteryMonitor = saved


# ----------------------------------------------------------------------------- scripted and random scenarios

class Driver:
    """Drives a recording Edge directly (no simulator): exact control over timing and inputs."""

    def __init__(self, name: str, cfg: EdgeConfig, seed: int, comment: str):
        self.tr = Trace(name, cfg, comment)
        RecEdge.trace = self.tr
        self.rng = random.Random(seed)
        self.e = RecEdge(cfg, random.Random(seed + 1).random)
        self.t = 0
        self.seq = {}

    def nseq(self, key):
        self.seq[key] = self.seq.get(key, -1) + 1
        return self.seq[key]

    def run(self, ms: int):
        end = self.t + ms
        while self.t < end:
            self.t += 5
            self.e.update(self.t)
            self.e.pop_events()

    def motor(self, l, r, ttl=300, seq=None, up=None):
        s = self.nseq("motor") if seq is None else seq
        self.e.on_motor({"seq": s, "up": self.t + 500 if up is None else up, "l": l, "r": r, "ttl": ttl,
                         "rs": s}, self.t)

    def manual(self, l, r, src="dash-3fa2c1", ttl=300):
        s = self.nseq("manual" + src)
        self.e.on_manual({"seq": s, "up": self.t + 7, "l": l, "r": r, "ttl": ttl, "rs": 0, "src": src}, self.t)

    def mode(self, m, src="dash-3fa2c1", retained=False, seq=None, up=None):
        s = self.nseq("mode" + src) if seq is None else seq
        self.e.on_mode({"seq": s, "up": self.t + 3 if up is None else up, "m": m, "src": src}, self.t, retained)

    def sleep(self, s, why="dark"):
        self.e.on_sleep({"seq": self.nseq("sleep"), "up": self.t + 1, "s": s, "why": why}, self.t)

    def dist(self, raw, filt=None):
        self.e.on_distance(raw, raw if filt is None and raw is not None else filt, self.t)

    def loom(self, start_cm, speed_cm_s, period_ms=40, until_cm=8.0):
        dd = start_cm
        while dd > until_cm:
            self.dist(dd)
            self.t += period_ms // 2
            self.e.update(self.t); self.e.pop_events()
            self.t += period_ms - period_ms // 2
            self.e.update(self.t); self.e.pop_events()
            dd -= speed_cm_s * period_ms / 1000.0
        self.dist(None)

    def boot(self, warm=True, link=True, mode="auto"):
        if link:
            self.e.on_link(True, self.t)
        if mode:
            self.mode(mode, src="bot", retained=True, seq=4, up=90000)
        if warm:
            self.e.set_odour_ready(True)
        self.run(50)

    def finish(self):
        self.tr.finish(self.e)


def scripted(cfg: EdgeConfig):
    # 1. boot: warm-up hold, 2 s mode wait, link timeout, retained estop wins
    g = Driver("boot", cfg, 1, "boot: warm-up hold, mode wait, link timeout, retained estop, late mode")
    g.e.on_link(True, g.t); g.run(1500); g.e.set_odour_ready(True); g.run(1000)       # auto after 2 s
    g.finish()
    g = Driver("boot_timeout", cfg, 2, "boot without a broker: leaves boot after 10 s into fallback")
    g.run(4000); g.e.set_odour_ready(True); g.run(7000); g.e.on_link(True, g.t); g.run(500)
    for _ in range(4):
        g.motor(0.3, 0.3); g.run(60)
    g.run(1000); g.finish()
    g = Driver("boot_estop", cfg, 3, "retained estop on subscribe, then cleared by a bot auto; mode during boot")
    g.e.on_link(True, g.t); g.run(300); g.mode("manual"); g.run(200)
    g.mode("estop", src="bot", retained=True, seq=9, up=50000); g.e.set_odour_ready(True); g.run(500)
    g.motor(0.5, 0.5); g.manual(0.5, 0.5); g.run(800); g.sleep(True); g.run(200)
    g.mode("auto", src="bot", seq=10, up=60000); g.run(100); g.motor(0.6, 0.6); g.run(700); g.finish()

    # 2. cloud: ttl, watchdog, fallback, three good commands, invalid and gaps reset, link loss
    g = Driver("cloud_fallback", cfg, 4, "ttl expiry, 500 ms watchdog, leave fallback rules, invalid and gaps")
    g.boot()
    for i in range(30):
        g.motor(0.5 + 0.01 * i, 0.4, ttl=300); g.run(50)
    g.motor(0.7, 0.7, ttl=80); g.run(200)                  # ttl expires: stop, then watchdog
    g.run(600)
    g.motor(0.3, 0.3); g.run(50); g.motor(0.3, 0.3); g.run(400)       # gap > ttl 300: count resets
    g.motor(0.3, 0.3, ttl=100); g.run(150); g.motor(0.3, 0.3); g.run(50)   # gap > ttl 100: resets
    g.motor(0.3, 0.3); g.run(50); g.e.on_invalid("cmd/motor"); g.motor(0.3, 0.3); g.run(50)
    g.e.on_invalid("cmd/manual"); g.motor(0.3, 0.3); g.run(50); g.motor(0.3, 0.3); g.run(50)
    g.motor(0.2, 0.2, seq=g.seq["motor"]); g.run(50)       # duplicate: ignored
    g.motor(0.2, 0.2, seq=g.seq["motor"] - 3); g.run(50)   # late: ignored
    g.motor(0.4, 0.4, seq=g.seq["motor"] + 5); g.seq["motor"] += 5; g.run(50)   # 4 lost
    g.motor(0.4, 0.4, seq=0, up=1); g.seq["motor"] = 0; g.run(50)   # brain restarted (up lower)
    for _ in range(10):
        g.motor(0.45, 0.45); g.run(50)
    g.e.on_link(False, g.t); g.run(1500); g.e.on_link(True, g.t); g.run(200)
    for _ in range(5):
        g.motor(0.45, 0.45); g.run(50)
    g.run(2000); g.finish()

    # 3. fallback wander with obstacles
    g = Driver("wander", cfg, 5, "fallback wander: obstacle turns, early stop when clear, curve changes")
    g.boot()
    g.run(700)
    for k in range(400):
        dd = 20.0 + 80.0 * abs(math.sin(k / 23.0)) + g.rng.uniform(-3, 3)
        g.dist(dd if g.rng.random() > 0.05 else None)
        g.run(40)
    g.finish()

    # 4. manual mode: obstacle stop, manual watchdog (stop and wait), reflexes, two tabs
    g = Driver("manual", cfg, 6, "manual: follows cmd/manual, ttl stop, obstacle stop, escape, two tabs, sleep ignored")
    g.boot(); g.mode("manual"); g.run(100)
    for i in range(20):
        g.manual(0.6, 0.6); g.dist(60.0 - 2.5 * i); g.run(50)
    g.dist(10.0); g.manual(0.6, 0.4); g.run(100); g.manual(-0.5, -0.5); g.run(100)
    g.run(1000)                                            # no commands: stop, stay manual
    g.manual(0.5, 0.5, src="dash-00beef"); g.run(100); g.manual(0.3, -0.3); g.run(100)
    g.sleep(True); g.run(100)
    g.dist(None); g.run(300); g.loom(70.0, 120.0); g.run(1500)
    g.mode("auto"); g.run(800); g.finish()

    # 5. looming: escape, cooldown, appeared, turning suppression, habituation, dishabituation, recovery
    g = Driver("looming", cfg, 7, "looming: escape, cooldown, appeared jump, turning holdoff, habituation, dishab")
    g.boot()
    for _ in range(3):
        g.motor(0.3, 0.3); g.run(50)
    g.e.on_link(False, g.t)                                 # fallback: wander keeps out non-zero
    g.run(500)
    g.loom(75.0, 90.0); g.run(1200)
    g.loom(60.0, 100.0); g.loom(60.0, 100.0); g.run(1500)   # second one within the cooldown
    g.dist(250.0); g.run(40); g.dist(30.0); g.run(40); g.dist(28.0); g.run(40); g.dist(None); g.run(500)
    for k in range(9):                                     # slow repeated stimuli: habituate
        g.run(1500); g.loom(70.0, 70.0 + 3 * k)
    g.run(1500); g.loom(70.0, 260.0)                       # much faster: dishabituation
    g.run(1500); g.loom(70.0, 80.0); g.run(1500)
    g.run(90000); g.loom(70.0, 80.0); g.run(2000)          # recovered after rest
    g.finish()

    # 6. sleep
    g = Driver("sleep", cfg, 8, "sleep: enter, repeats, wake, 15 s timeout, ignored in manual / estop, during escape")
    g.boot()
    for _ in range(4):
        g.motor(0.4, 0.4); g.run(50)
    g.sleep(True); g.run(4000); g.sleep(True); g.run(5000); g.sleep(False); g.run(300)
    for _ in range(4):
        g.motor(0.4, 0.4); g.run(50)
    g.sleep(True); g.run(16000)                            # no repeat: wakes after 15 s
    g.e.on_link(False, g.t); g.run(600); g.sleep(True); g.run(200); g.sleep(False); g.run(200)
    g.e.on_link(True, g.t); g.mode("estop"); g.run(100); g.sleep(True); g.run(200)
    g.mode("auto"); g.run(100); g.loom(70.0, 120.0); g.sleep(True); g.run(800); g.finish()

    # 8. boundaries: every comparison exactly at its threshold (catches < vs <= slips in the port)
    g = Driver("boundaries", cfg, 10, "thresholds hit exactly: ttl window, obstacle 12 cm, wander 30/45 cm, holdoff")
    g.boot()
    g.e.on_link(False, g.t); g.e.on_link(True, g.t)        # fallback (mqtt_lost)
    g.run(100)
    g.motor(0.3, 0.3, ttl=300); g.run(300)                 # next one exactly ttl later: still consecutive
    g.motor(0.3, 0.3, ttl=300); g.run(300)
    g.motor(0.3, 0.3, ttl=300); g.run(50)                  # third: leaves fallback
    for dd in (12.0, 12.0, 11.999999, 12.0, 12.000001, 11.0):
        g.dist(None, dd); g.motor(0.5, 0.5); g.run(50)     # obstacle stop at < 12.0 exactly
    g.dist(None, None); g.run(700)                         # watchdog: fallback
    for dd in (30.0, 30.0, 29.999999, 44.999999, 45.0, 45.0, 30.000001, 29.0, 46.0):
        g.dist(None, dd); g.run(450)                       # wander: turn at < 30, clear at >= 45
    for clear in (45.0, 44.999999, 45.000001):             # exactly 400 ms into a turn (the minimum)
        g.dist(None, 29.0); g.run(5)                       # the turn starts at this update
        g.run(395); g.dist(None, clear); g.run(1500)
    g.mode("manual"); g.run(100)
    g.manual(0.6, -0.6); g.dist(200.0); g.run(40); g.dist(200.0); g.run(40)   # turning: holdoff starts
    g.manual(0.0, 0.0); g.dist(200.0)
    t_turn = g.t
    dd = 200.0
    for k in range(1, 12):                                 # readings every 40 ms: +200 ms is exactly the holdoff
        g.run(40)
        dd = 70.0 - 4.0 * k if k > 1 else 70.0
        g.dist(dd)
    g.run(1500)
    _ = t_turn
    for extra in (0, 5):                                   # cooldown: detection exactly 1000 ms after the end
        g.dist(None); g.run(3000)
        g.loom(70.0, 150.0)
        while g.e.state == "escape":
            g.run(5)
        end = g.e.escape_end_ms
        for k, dd in enumerate((60.0, 52.0, 44.0, 36.0)):
            target = end + 920 + 40 * k + extra
            if target > g.t:
                g.run(target - g.t)
            g.dist(dd)
        g.run(1500)
    g.finish()

    # 7. battery
    g = Driver("battery", cfg, 9, "battery: warning once, cut-off disables motors and reflexes")
    g.boot()
    for v in (7.6, 7.2, 6.9, 6.79, 6.75, 6.5, 6.41, 6.39, 6.2, 7.5):
        g.e.on_battery(v, g.t); g.motor(0.5, 0.5); g.run(100)
    g.loom(70.0, 120.0); g.run(500); g.finish()


def fuzz(cfg: EdgeConfig, seed: int, seconds: int):
    """Random inputs at random times: interleavings no script thinks of."""
    g = Driver(f"fuzz{seed}", cfg, seed, f"random inputs, seed {seed}, {seconds} s")
    rng = g.rng
    g.e.on_link(True, 0)
    srcs = ["dash-3fa2c1", "dash-00beef", "bot"]
    dist = 120.0
    while g.t < seconds * 1000:
        g.t += 5
        u = rng.random()
        if u < 0.04:
            g.motor(round(rng.uniform(-1, 1), 2), round(rng.uniform(-1, 1), 2), ttl=rng.choice([50, 100, 300, 300, 1000]))
        elif u < 0.045:
            g.manual(round(rng.uniform(-1, 1), 2), round(rng.uniform(-1, 1), 2), src=rng.choice(srcs[:2]))
        elif u < 0.0465:
            g.mode(rng.choice(["auto", "auto", "manual", "estop"]), src=rng.choice(srcs), retained=rng.random() < 0.2)
        elif u < 0.0475:
            g.sleep(rng.random() < 0.6)
        elif u < 0.048:
            g.e.on_invalid(rng.choice(["cmd/motor", "cmd/mode"]))
        elif u < 0.0484:
            g.e.on_link(rng.random() < 0.6, g.t)
        elif u < 0.0486:
            g.e.set_odour_ready(rng.random() < 0.9)
        elif u < 0.0490:
            g.e.on_battery(rng.choice([7.8, 7.4, 6.85, 6.7, 6.45, 6.3]), g.t)
        if g.t % 40 == 0:
            r = rng.random()
            if r < 0.02:
                dist = rng.uniform(5, 300)                  # jumps
            elif r < 0.3:
                dist = max(3.0, dist - rng.uniform(0, 8))   # approaching
            else:
                dist = min(350.0, dist + rng.uniform(-2, 3))
            raw = None if rng.random() < 0.05 else dist
            filt = None if raw is None and rng.random() < 0.5 else (raw if raw is not None else dist)
            g.e.on_distance(raw, filt, g.t)
        g.e.update(g.t)
        g.e.pop_events()
    g.finish()


# ----------------------------------------------------------------------------- arithmetic and streams

def arithmetic(seed: int):
    rng = random.Random(seed)
    lines = []
    vals = [0.125, 0.375, 2.675, 1.005, -0.125, 0.5, 1.5, 2.5, -2.5, 38.45, 0.0, -0.0, 1e-9, 123456.789,
            0.285, 0.615, 6.445, 9.995]
    vals += [rng.uniform(-10, 10) for _ in range(800)]
    vals += [(k + 0.5) / 10 ** n for n in (1, 2, 3) for k in range(-150, 150)]
    vals += [rng.randint(0, 99999) / 1000 for _ in range(400)]
    for x in vals:
        for n in (0, 1, 2, 3):
            exp = float(round(x)) if n == 0 else round(x, n)
            lines.append(f"R {d(x)} {n} {d(exp)}")
    for _ in range(500):
        k = rng.randint(1, 12)
        xs = [rng.choice([rng.uniform(-1, 1), rng.uniform(0, 1e6), 0.1, 1e-17, -0.3]) for _ in range(k)]
        lines.append("S " + " ".join(d(x) for x in xs) + " = " + d(sum(xs)))
    path = OUT / "arith.txt"
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    print(f"  {path.name}: {len(lines)} lines")


def streams(seed: int):
    rng = random.Random(seed)
    st = StreamTracker(600000)
    keys = [(0, ""), (1, "dash-3fa2c1"), (1, "dash-00beef"), (2, "bot"), (2, "dash-3fa2c1"), (3, ""), (5, "")]
    last = {}
    lines = []
    now = 0
    for _ in range(6000):
        now += rng.choice([1, 10, 50, 50, 50, 1000, 700000 if rng.random() < 0.002 else 20])
        topic, src = rng.choice(keys)
        prev = last.get((topic, src), (0, 0))
        r = rng.random()
        if r < 0.6:
            seq = (prev[0] + 1) % 2 ** 32
        elif r < 0.7:
            seq = prev[0]
        elif r < 0.8:
            seq = (prev[0] - rng.randint(1, 5)) % 2 ** 32
        elif r < 0.9:
            seq = (prev[0] + rng.randint(2, 6)) % 2 ** 32
        elif r < 0.95:
            seq = rng.choice([0, 4294967295, 4294967294])
        else:
            seq = rng.randint(0, 2 ** 32 - 1)
        up = prev[1] + rng.choice([50, 50, 0, -10, 1000]) if rng.random() > 0.03 else rng.randint(0, 100)
        up = max(0, up)
        bid = None if topic != 5 else rng.choice([77, 77, 77, 78])
        key = (topic, src) if src else (topic,)
        op = rng.random()
        if op < 0.9:
            ok = st.accept(key, seq, up, now, bid=bid)
            lines.append(f"A {topic} {src or '-'} {seq} {up} {now} {bid if bid is not None else '-'} {b(ok)} "
                         f"{st.lost} {st.dropped} {st.restarts} {b(st.last_restarted)}")
            if ok:
                last[(topic, src)] = (seq, up)
        elif op < 0.97:
            st.remember(key, seq, up, now, bid=bid)
            last[(topic, src)] = (seq, up)
            lines.append(f"M {topic} {src or '-'} {seq} {up} {now} {bid if bid is not None else '-'}")
        else:
            st.reset(key)
            lines.append(f"X {topic} {src or '-'}")
    path = OUT / "streams.txt"
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    print(f"  {path.name}: {len(lines)} lines")


def scripted_sensing(cfg: EdgeConfig):
    """Sensing inputs a simulator never produces: constant readings (where a plain sum and Python's
    compensated sum differ), readings exactly at the distance window edge, a recalibration-free drift."""
    st = SensingTrace("scripted", cfg)
    RecOdour.st = st
    rng = random.Random(31)
    ch = [RecOdour(cfg), RecOdour(cfg)]
    f, bm = RecDist(cfg), RecBattery(cfg)
    t = 0
    for i in range(400):
        ch[0].feed(0.059, t)                               # constant: a plain mean of 21 x 0.059 is below 0.059
        ch[1].feed(0.3 + (0.7 if 150 < i < 200 else 0.0) + rng.uniform(-0.01, 0.01), t)
        t += 50
    for i in range(300):
        ch[0].feed(0.059 - 0.0001 * i, t)                  # below baseline: fast follow
        ch[1].feed(0.3 + 0.0001 * i, t)                    # slow drift up in clean air
        t += 50
    for i in range(100):
        ch[0].feed(0.5 + 0.001 * i, t)                     # odour: above the baseline, value > 0
        ch[1].feed(0.9, t)
        t += 50
    t = 0
    for i in range(400):                                   # every 40 ms: entries exactly 200 ms old stay
        f.feed(None if i % 7 == 3 else 30.0 + 10 * math.sin(i / 9) + rng.choice([0.0, 0.0, 150.0]), t)
        t += rng.choice([40, 40, 40, 50, 200])
    for v in [8.4, 8.4, 8.3] + [7.4 + rng.uniform(-0.2, 0.2) for _ in range(200)]:
        bm.feed(v)
    st.save()


def messages(seed: int):
    """Incoming payloads (valid and broken) with the Python contract's verdict, for the C++ parsers.
    Lines: P <topic> <ok|invalid> <payload as hex>; after an ok one: V <decoded fields>."""
    import json as _json
    from flybot_contract import ContractError
    rng = random.Random(seed)

    def valid(topic):
        seq, up = rng.choice([0, 1, rng.randint(0, 2 ** 32 - 1), 4294967295]), rng.choice(
            [0, rng.randint(0, 10 ** 9), 9007199254740991])
        if topic == "cmd/motor":
            return {"v": 1, "seq": seq, "up": up, "l": round(rng.uniform(-1, 1), 2), "r": rng.choice([-1, 1, 0, 0.5, -0.33]),
                    "ttl": rng.choice([50, 300, 1000, rng.randint(50, 1000)]), "rs": rng.randint(0, 2 ** 32 - 1)}
        if topic == "cmd/manual":
            return {"v": 2, "seq": seq, "up": up, "l": round(rng.uniform(-1, 1), 2), "r": round(rng.uniform(-1, 1), 2),
                    "ttl": rng.choice([50, 300, 1000]), "rs": rng.randint(0, 99999),
                    "src": "dash-" + "".join(rng.choice("0123456789abcdef") for _ in range(6))}
        if topic == "cmd/mode":
            return {"v": 2, "seq": seq, "up": up, "m": rng.choice(["auto", "manual", "estop"]),
                    "src": rng.choice(["bot", "dash-3fa2c1", "dash-000000"])}
        if topic == "cmd/sleep":
            return {"v": 1, "seq": seq, "up": up, "s": rng.random() < 0.5, "why": rng.choice(["dark", "light", "user"])}
        return {"v": 1, "seq": seq, "up": up, "ps": rng.randint(0, 2 ** 32 - 1), "pu": rng.randint(0, 9007199254740991)}

    def text(m):
        return _json.dumps(m, separators=(",", ":"))

    bad_values = [None, True, False, "1", [], {}, -1, 0.5, 1.5, -0.0, 1e300, 4294967296, 9007199254740992,
                  2, -2, 49, 1001, 300.0, 3e2, 1.0, -1.0000001, 1.0000001, "dash-3FA2C1", "dash-3fa2c", "bot\n",
                  "dash-3fa2c1\n", "dash-3fa2c1x", "BOT", "btn", "auto ", "AUTO", "dark", "nap"]
    lines = []
    for topic in ("cmd/motor", "cmd/manual", "cmd/mode", "cmd/sleep", "pong"):
        payloads = []
        for _ in range(60):
            payloads.append(text(valid(topic)))
        base = valid(topic)
        for k in list(base):
            m = dict(base); m.pop(k); payloads.append(text(m))                    # missing key
            for bv in bad_values:
                m = dict(base); m[k] = bv; payloads.append(text(m))               # wrong type / range / pattern
        m = dict(base); m["extra"] = 1; payloads.append(text(m))
        m = dict(base); m["v"] = base["v"] + 1; payloads.append(text(m))         # unknown version
        t = text(base)
        payloads += [t + " ", " " + t, t + "x", t[:-1], "[" + t + "]", "", "null", "1", '"x"', t.replace('"', "'", 2),
                     t.replace(":", " : "), t.replace(",", " ,\n "), t.replace('"seq":', '"seq":0', 1),
                     t[:-1] + ',"v":' + str(base["v"]) + "}", t[:-1] + ',"seq":-1}', "{" + t[1:].replace("{", "", 1),
                     t.replace('"v"', '"\\u0076"'), t.replace('"v":', '"v\\u0000":'), t.encode().hex()[:10],
                     t.replace("}", "}}"), t + "\n", "﻿" + t, t.replace('"up":', '"up": 0', 1) if False else t]
        for p in payloads:
            raw = p.encode("utf-8")
            try:
                msg = C.decode(topic, raw)
                ok = True
            except ContractError:
                ok = False
            lines.append(f"P {topic} {'ok' if ok else 'invalid'} {raw.hex()}")
            if ok:
                if topic in ("cmd/motor", "cmd/manual"):
                    v = f"V {msg['seq']} {msg['up']} {d(msg['l'])} {d(msg['r'])} {int(msg['ttl'])} {int(msg['rs'])}"
                    if topic == "cmd/manual":
                        v += " " + msg["src"].encode().hex()
                elif topic == "cmd/mode":
                    v = f"V {int(msg['seq'])} {int(msg['up'])} {msg['m']} {msg['src'].encode().hex()}"
                elif topic == "cmd/sleep":
                    v = f"V {int(msg['seq'])} {int(msg['up'])} {b(msg['s'])} {msg['why']}"
                else:
                    v = f"V {int(msg['seq'])} {int(msg['up'])} {int(msg['ps'])} {int(msg['pu'])}"
                lines.append(v)
    # invalid UTF-8 and a raw control character inside a string
    for topic in ("cmd/mode",):
        lines.append(f"P {topic} invalid {(b'{\"v\":2,\"seq\":1,\"up\":1,\"m\":\"auto\",\"src\":\"b\\xffot\"}').hex()}")
        lines.append(f"P {topic} invalid {(b'{\"v\":2,\"seq\":1,\"up\":1,\"m\":\"auto\",\"src\":\"b\\x01ot\"}').hex()}")
    path = OUT / "messages.txt"
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    n_ok = sum(1 for line in lines if line.startswith("P ") and " ok " in line)
    print(f"  {path.name}: {sum(1 for line in lines if line.startswith('P '))} payloads ({n_ok} valid)")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.txt"):
        old.unlink()
    print(f"Python {sys.version.split()[0]} (sum() is compensated from 3.12)")
    print("arithmetic and streams:")
    arithmetic(11)
    streams(12)
    messages(13)
    print("scripted edge scenarios:")
    cfg = EdgeConfig()
    scripted(cfg)
    scripted_sensing(cfg)
    print("random edge scenarios:")
    for seed in (101, 102, 103):
        fuzz(cfg, seed, 40)
    print("simulator runs (edge and sensing):")
    from flybot_sim.config import StartPos
    run_sim("sim_seek", "default", 40.0,
            {"retained_mode": "auto", "outage": [(15000, 17500)], "link": [(25000, False), (27000, True)],
             "sleep": [(30000, 36000)], "wake": [36500]}, seed=21)
    run_sim("sim_obstacles", "obstacles", 40.0,
            {"outage": [(8000, 30000)], "modes": [(31000, "manual"), (35000, "estop"), (37000, "auto")]}, seed=22)
    run_sim("sim_threat", "threat", 30.0, {"retained_mode": "auto"}, seed=23,
            start=StartPos(1.3, 1.3, 45.0))
    run_sim("sim_habituation", "default", 50.0,
            {"retained_mode": "auto", "outage": [(0, 60000)],
             "loom": [(t, 0.6) for t in range(8000, 38000, 3000)] + [(41000, 1.6), (45000, 0.6)]},
            seed=24, start=StartPos(1.0, 1.0, 0.0))
    index = sorted(p.name for p in OUT.glob("*.txt") if p.name != "index.txt")
    (OUT / "index.txt").write_text("\n".join(index) + "\n", encoding="ascii", newline="\n")
    total = sum(p.stat().st_size for p in OUT.glob("*.txt"))
    print(f"{len(index)} vector files, {total // 1024} KB in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
