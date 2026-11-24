"""Telegram bot logic (Phase 10), independent of MQTT and of Telegram's transport.

Feed it MQTT messages (`on_mqtt`) and chat messages (`on_text`); it returns what to send:
`Reply(chat_id, text)` and `Publish(name, msg, qos, retain)` items. The runner does the I/O.

What the `bot` MQTT user may do (contract section 4): read status, events, cmd/mode, brain/status,
brain/state; publish cmd/mode (retained), reward (src "bot") and brain/sleep (src "bot").
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from flybot_contract import Contract, Outbox

SUBSCRIBE = ("status", "events", "cmd/mode", "brain/status", "brain/state")
QOS = {"cmd/mode": 1, "reward": 1, "brain/sleep": 1}
RETAIN = {"cmd/mode"}

HELP = ("FlyBot commands:\n/status – state, hunger, fear, battery, uptime\n/good, /bad – reward or punish\n"
        "/sleep, /wake – ask the brain to sleep or wake\n/stop – emergency stop\n/resume – back to autonomous\n"
        "/learned – what it has learned")


@dataclass
class Reply:
    chat_id: int
    text: str


@dataclass
class Publish:
    name: str
    msg: dict
    qos: int
    retain: bool


@dataclass
class BotConfig:
    allowed_chats: frozenset[int]
    low_battery_v: float = 6.8
    escape_window_s: float = 60.0      # at most one escape alert per window; later ones are counted
    lag_alert: int = 40                # brain/status lag above this: alert (once, until it recovers)
    learned_level: float = 0.5         # |preference| above this: "learned" alert (once per cue and sign)
    learned_rearm: float = 0.25        # ... re-armed when it falls below this


CUES = {"lt": "the light", "odr": "the odour"}


class Bot:
    def __init__(self, contract: Contract, cfg: BotConfig, clock=time.monotonic):
        self.contract = contract
        self.cfg = cfg
        self.clock = clock
        self.t0 = clock()
        self.outbox = Outbox(contract)
        self.status: dict | None = None
        self.brain_status: dict | None = None
        self.brain_state: dict | None = None
        self.mode: str | None = None
        self.robot_online: bool | None = None
        self.brain_online: bool | None = None
        self.lagging = False
        self.escape_window_start: float | None = None
        self.escapes_suppressed = 0
        self.learned: dict[str, int] = {}        # cue -> sign already announced (+1 / -1)

    # ------------------------------------------------------------------ helpers

    def _up(self) -> int:
        return int((self.clock() - self.t0) * 1000)

    def _publish(self, name: str, **fields) -> Publish:
        if name in ("reward", "brain/sleep", "cmd/mode"):
            fields["src"] = "bot"
        return Publish(name, self.outbox.build(name, self._up(), **fields), QOS[name], name in RETAIN)

    def _alert(self, text: str) -> list[Reply]:
        return [Reply(c, text) for c in sorted(self.cfg.allowed_chats)]

    # ------------------------------------------------------------------ MQTT in

    def on_mqtt(self, name: str, msg: dict, retained: bool = False) -> list[Reply]:
        out: list[Reply] = []
        if name == "status":
            self.status = msg
            online = msg["st"] != "offline"
            if self.robot_online is not None and online != self.robot_online:
                out += self._alert("🟢 Robot is back online." if online else "🔴 Robot went offline.")
            self.robot_online = online
        elif name == "brain/status":
            self.brain_status = msg
            online = msg["st"] == "online"
            if self.brain_online is not None and online != self.brain_online:
                out += self._alert("🧠 Brain is back online." if online else "⚠️ Brain went offline.")
            self.brain_online = online
            lag = msg.get("lag")
            if online and lag is not None:
                if lag > self.cfg.lag_alert and not self.lagging:
                    self.lagging = True
                    out += self._alert(f"🐢 Brain is falling behind: {lag} messages waiting.")
                elif lag <= self.cfg.lag_alert and self.lagging:
                    self.lagging = False
                    out += self._alert("✅ Brain has caught up.")
        elif name == "cmd/mode":
            self.mode = msg["m"]
        elif name == "brain/state":
            self.brain_state = msg
            out += self._check_learned(msg.get("pref") or {})
        elif name == "events":
            out += self._event(msg)
        return out

    def _event(self, msg: dict) -> list[Reply]:
        e = msg["e"]
        if e == "low_battery":
            return self._alert(f"🪫 Battery {'cut-off reached, motors off' if msg['cut'] else 'low'}: {msg['bat']:.2f} V.")
        if e == "fallback_on":
            return self._alert(f"⚠️ Robot is in fallback ({msg['why'].replace('_', ' ')}): driving on its own sensors.")
        if e == "fallback_off":
            return self._alert(f"✅ Fallback ended after {msg['dur'] / 1000:.0f} s.")
        if e == "escape" and msg.get("resp", 1.0) > 0:
            now = self.clock()
            if self.escape_window_start is None or now - self.escape_window_start >= self.cfg.escape_window_s:
                extra = f" (+{self.escapes_suppressed} more since the last alert)" if self.escapes_suppressed else ""
                self.escape_window_start, self.escapes_suppressed = now, 0
                return self._alert(f"💨 Escape! Something loomed at {msg['d']:.0f} cm.{extra}")
            self.escapes_suppressed += 1
        return []

    def _check_learned(self, pref: dict) -> list[Reply]:
        out = []
        for cue, v in pref.items():
            if cue not in CUES or v is None:
                continue
            sign = 1 if v > 0 else -1
            if abs(v) >= self.cfg.learned_level and self.learned.get(cue) != sign:
                self.learned[cue] = sign
                out += self._alert(f"🎓 Learned: {CUES[cue]} = {'good' if sign > 0 else 'bad'} ({v:+.2f}).")
            elif abs(v) < self.cfg.learned_rearm:
                self.learned.pop(cue, None)
        return out

    # ------------------------------------------------------------------ chat in

    def on_text(self, chat_id: int, text: str) -> tuple[list[Reply], list[Publish]]:
        if chat_id not in self.cfg.allowed_chats:
            return [], []                                   # ignore strangers silently
        cmd = text.strip().split()[0].split("@")[0].lower() if text.strip() else ""
        if cmd in ("/start", "/help"):
            return [Reply(chat_id, HELP)], []
        if cmd == "/status":
            return [Reply(chat_id, self.status_text())], []
        if cmd == "/learned":
            return [Reply(chat_id, self.learned_text())], []
        if cmd in ("/good", "/bad"):
            r = 1 if cmd == "/good" else -1
            return [Reply(chat_id, "👍 Reward sent." if r > 0 else "👎 Punishment sent.")], [self._publish("reward", r=r)]
        if cmd in ("/sleep", "/wake"):
            s = cmd == "/sleep"
            return [Reply(chat_id, "😴 Asked the brain to sleep." if s else "☀️ Asked the brain to wake.")], \
                [self._publish("brain/sleep", s=s)]
        if cmd == "/stop":
            return [Reply(chat_id, "🛑 Emergency stop sent. /resume to drive again.")], [self._publish("cmd/mode", m="estop")]
        if cmd == "/resume":
            return [Reply(chat_id, "▶️ Back to autonomous mode.")], [self._publish("cmd/mode", m="auto")]
        return [Reply(chat_id, "Unknown command.\n" + HELP)], []

    def status_text(self) -> str:
        st, bs, s = self.status or {}, self.brain_status or {}, self.brain_state or {}
        lines = []
        if not st or st.get("st") == "offline":
            lines.append("Robot: offline")
        else:
            up = st.get("up", 0) / 1000
            lines.append(f"Robot: {st.get('st')} · mode {self.mode or st.get('m', '?')} · battery {st.get('bat', '?')} V · "
                         f"Wi-Fi {st.get('rssi', '?')} dBm · up {int(up // 3600)}h{int(up % 3600 // 60):02d}m")
        if bs.get("st") == "online":
            lines.append(f"Brain: {bs.get('ctrl')} · sensors {bs.get('sens')} · lag {bs.get('lag', 0)}")
        else:
            lines.append("Brain: offline")
        if s:
            lines.append(f"Decision: {s.get('dec')} ({', '.join(s.get('why') or []) or '–'})")
            lines.append(f"Hunger {s.get('hun', 0):.2f} · fear {s.get('fear', 0):.2f} · habituation {s.get('hab', 0):.2f}"
                         + (" · asleep" if s.get("slp") else ""))
            if s.get("les"):
                lines.append(f"Lesions: {', '.join(s['les'])}")
        return "\n".join(lines)

    def learned_text(self) -> str:
        pref = (self.brain_state or {}).get("pref") or {}
        if not pref:
            return "Nothing learned yet (or the brain is not running the neural controller)."
        def word(v):
            return "good" if v >= 0.5 else "bad" if v <= -0.5 else "slightly good" if v > 0.15 else \
                "slightly bad" if v < -0.15 else "neutral"
        return "Learned preferences:\n" + "\n".join(f"{CUES.get(k, k)}: {word(v)} ({v:+.2f})" for k, v in pref.items())
