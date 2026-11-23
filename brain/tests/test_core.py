"""Brain core: contract rules around the controller (no broker needed)."""
from __future__ import annotations

import types

import pytest

from flybot_brain.config import BrainConfig
from flybot_brain.controllers import Controller, Decision
from flybot_brain.core import Brain
from flybot_brain.link import BrainLink
from flybot_brain.config import MqttSettings
from flybot_brain.persistence import Snapshots
from flybot_contract import Contract

C = Contract()
D = "dash-3fa2c1"


class Wall:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Recorder(Controller):
    """Stand-in controller: records inputs, returns a fixed decision."""
    name = "rec"

    def __init__(self):
        self.inputs, self.resets = [], 0

    def reset(self):
        self.resets += 1

    def step(self, inp):
        self.inputs.append(inp)
        return Decision(0.3, 0.2, "seek", ["odour_l"])

    def spikes(self):
        return {}

    def state_dict(self):
        return {"n": len(self.inputs)}

    def load_state(self, s):
        self.loaded = s


def make(tmp_path=None, **exp):
    cfg = BrainConfig(controller="neural")
    for k, v in exp.items():
        setattr(cfg.experiment, k, v)
    wall = Wall()
    b = Brain(cfg, C, Snapshots(tmp_path) if tmp_path else None, wall=wall)
    b.ctrl = Recorder()
    return b, wall


BID = 2847193551


def sensors(seq, up, ol=0.1, orr=0.05, d=120.0, bid=BID):
    msg = {"v": 2, "seq": seq, "up": up, "bid": bid, "ol": ol, "or": orr, "d": d, "lt": 0.8, "bat": 7.9}
    C.validate("sensors", msg)
    return msg


def names(outs):
    return [n for n, _ in outs]


def only(outs, name):
    return [m for n, m in outs if n == name]


def feed(b, wall, n, start_seq=0, start_up=1000, step=50, **kw):
    outs = []
    for i in range(n):
        wall.t += step / 1000
        outs += b.handle("sensors", sensors(start_seq + i, start_up + i * step, **kw))
    return outs


# ----------------------------------------------------------------------------- the tick

def test_one_cmd_motor_per_sensors_message_with_rs_and_ttl():
    b, wall = make()
    outs = feed(b, wall, 3, start_seq=7)
    cmds = only(outs, "cmd/motor")
    assert [m["rs"] for m in cmds] == [7, 8, 9]
    assert all(m["ttl"] == 300 and (m["l"], m["r"]) == (0.3, 0.2) for m in cmds)
    for name, msg in outs:
        C.validate(name, msg)


def test_duplicate_and_late_sensors_are_not_ticked():
    b, wall = make()
    feed(b, wall, 3)
    assert b.handle("sensors", sensors(2, 1100)) == []      # duplicate
    assert b.handle("sensors", sensors(1, 1050)) == []      # late: lower seq and up, same boot id
    assert b.counters["dropped"] == 2 and len(b.ctrl.inputs) == 3


# ----------------------------------------------------------------------------- time base (7.1)

def test_robot_time_comes_from_up_not_wall_clock():
    b, wall = make()
    for seq, up in enumerate((1000, 1050, 1100)):
        wall.t += 5.0                                         # wall time races ahead (lockstep-like)
        b.handle("sensors", sensors(seq, up))
    assert [i.dt_ms for i in b.ctrl.inputs] == [0, 50, 50]
    assert [i.t_ms for i in b.ctrl.inputs] == [0, 50, 100]


def test_reboot_counts_zero_and_restarts_the_seq_stream():
    b, wall = make()
    feed(b, wall, 3, start_seq=500, start_up=90000)
    outs = b.handle("sensors", sensors(0, 40, bid=99))       # robot rebooted: new boot id
    assert only(outs, "cmd/motor")[0]["rs"] == 0
    assert b.ctrl.inputs[-1].dt_ms == 0 and b.ctrl.inputs[-1].t_ms == 100
    assert b.counters["reboots"] == 1 and b.streams.lost == 0


def test_gap_over_500ms_is_capped_at_100ms_and_recorded():
    b, wall = make()
    feed(b, wall, 2)
    b.handle("sensors", sensors(2, 1050 + 5000))
    assert b.ctrl.inputs[-1].dt_ms == 100 and b.counters["gaps"] == 1


def test_reset_on_reboot_flag():
    b, wall = make()                                          # default: off
    feed(b, wall, 2, start_up=5000)
    b.hun = 0.7
    b.handle("sensors", sensors(0, 10, bid=99))
    assert b.ctrl.resets == 0 and b.hun == 0.7

    b, wall = make(reset_on_reboot=True)
    feed(b, wall, 2, start_up=5000)
    b.hun = 0.7
    b.handle("sensors", sensors(0, 10, bid=99))
    assert b.ctrl.resets == 1 and b.hun == 0.0


# ----------------------------------------------------------------------------- warm-up, stale, paused

def test_warmup_is_silent_reports_warmup_and_is_not_stale():
    b, wall = make()
    outs = []
    for i in range(5):
        wall.t += 0.05
        outs += b.handle("sensors", sensors(i, i * 50, ol=None, orr=None))
        outs += b.poll()
    assert "cmd/motor" not in names(outs)
    assert only(outs, "brain/state")[-1]["dec"] == "warmup"
    assert only(outs, "brain/status")[-1]["sens"] == "ok"


def test_no_sensors_yet_reports_none_and_stale_decision():
    b, wall = make()
    outs = b.poll()
    assert only(outs, "brain/status")[0]["sens"] == "none"
    assert only(outs, "brain/state")[0]["dec"] == "stale"


def test_stale_after_250ms_is_silent_and_reported_then_recovers():
    b, wall = make()
    feed(b, wall, 2)
    b.poll()
    wall.t += 0.2
    assert "brain/status" not in names(b.poll())            # 200 ms: still fresh
    wall.t += 0.1
    outs = b.poll()
    assert only(outs, "brain/status")[0]["sens"] == "stale"
    assert only(outs, "brain/state")[0]["dec"] == "stale"
    assert "cmd/motor" not in names(outs)
    wall.t += 0.05
    outs = b.handle("sensors", sensors(2, 1100)) + b.poll()
    assert only(outs, "brain/status")[0]["sens"] == "ok"
    assert only(outs, "cmd/motor")


def test_paused_unless_mode_is_auto():
    b, wall = make()
    b.handle("cmd/mode", {"v": 2, "seq": 4, "up": 1, "m": "manual", "src": D}, retained=True)
    outs = feed(b, wall, 3)
    assert "cmd/motor" not in names(outs) and b.dec == "paused"
    b.handle("cmd/mode", {"v": 2, "seq": 5, "up": 2, "m": "auto", "src": D})
    assert only(feed(b, wall, 1, start_seq=3, start_up=1150), "cmd/motor")


# ----------------------------------------------------------------------------- ping, state, status

def test_ping_gets_pong_echoing_seq_and_up():
    b, _ = make()
    (name, msg), = b.handle("ping", {"v": 2, "seq": 57, "up": 57012, "bid": 2847193551})
    assert name == "pong" and msg["ps"] == 57 and msg["pu"] == 57012


def test_brain_state_at_5hz_robot_time():
    b, wall = make()
    outs = feed(b, wall, 20)                                  # 1 s of robot time
    st = only(outs, "brain/state")
    assert len(st) == 5 and st[0]["dec"] == "seek" and st[0]["why"] == ["odour_l"]
    assert st[0]["spk"] == {} and st[0]["les"] == [] and st[0]["pref"] == {}


def test_brain_status_fields_and_heartbeat():
    b, wall = make()
    feed(b, wall, 5)
    (s,) = only(b.poll(), "brain/status")
    assert s["st"] == "online" and s["ctrl"] == "neural" and s["sens"] == "ok" and s["ovr"] == 0
    feed(b, wall, 1, start_seq=5, start_up=1250)
    assert not only(b.poll(), "brain/status")                 # no change: no status yet
    for i in range(6, 6 + 200):                               # 10 s of fresh sensors
        wall.t += 0.05
        b.handle("sensors", sensors(i, 1000 + i * 50))
        b.poll()
    wall.t += 0.05
    b.handle("sensors", sensors(500, 1000 + 500 * 50))
    assert only(b.poll(), "brain/status") or b.next_status_wall > wall.t


# ----------------------------------------------------------------------------- sleep requests

def test_sleep_request_sends_cmd_sleep_stops_commands_and_repeats():
    b, wall = make()
    feed(b, wall, 2)
    (n, m), = b.handle("brain/sleep", {"v": 2, "seq": 0, "up": 1, "s": True, "src": "bot"})
    assert n == "cmd/sleep" and m["s"] is True and m["why"] == "user"
    outs = feed(b, wall, 120, start_seq=2, start_up=1100)     # 6 s of robot time
    assert "cmd/motor" not in names(outs) and b.dec == "sleep"
    assert len(only(outs, "cmd/sleep")) == 1                  # repeated after 5 s
    (n, m), = b.handle("brain/sleep", {"v": 2, "seq": 1, "up": 2, "s": False, "src": D})
    assert m["s"] is False
    assert only(feed(b, wall, 1, start_seq=200, start_up=20000), "cmd/motor")


# ----------------------------------------------------------------------------- brain/control

def control(b, seq, c, **kw):
    msg = {"v": 3, "seq": seq, "up": seq, "src": D, "c": c, **kw}
    C.validate("brain/control", msg)
    outs = b.handle("brain/control", msg)
    return only(outs, "brain/state")[0]["ack"]


def test_param_changes_and_rejections():
    b, _ = make()
    assert control(b, 1, "param", k="baseline.speed", val=0.4) == {"src": D, "seq": 1, "ok": True}
    assert b.cfg.baseline.speed == 0.4
    assert control(b, 2, "param", k="baseline.nope", val=1)["ok"] is False
    assert control(b, 3, "param", k="baseline.speed", val="fast")["ok"] is False
    assert control(b, 4, "param", k="ctrl", val="baseline")["ok"] is True
    assert b.ctrl.name == "baseline" and b.status_dirty


def test_lesion_not_supported_yet():
    b, _ = make()
    assert control(b, 1, "lesion", tgt="mb", off=True)["ok"] is False


def test_save_load_and_reset(tmp_path):
    b, _ = make(tmp_path)
    b.hun, b.fear = 0.4, 0.2
    assert control(b, 1, "save", name="after_training")["ok"]
    b.hun = b.fear = 0.9
    assert control(b, 2, "load", name="after_training")["ok"]
    assert (b.hun, b.fear) == (0.4, 0.2)
    assert control(b, 3, "load", name="missing")["ok"] is False
    assert control(b, 4, "reset", what="state")["ok"] and b.ctrl.resets == 1 and b.hun == 0.0
    assert control(b, 5, "save")["ok"] and (tmp_path / "latest.json").exists()


def test_control_from_two_tabs_is_tracked_per_sender():
    b, _ = make()
    assert control(b, 10, "reset", what="state")["ok"]
    msg = {"v": 3, "seq": 0, "up": 5, "src": "dash-aaaaaa", "c": "reset", "what": "state"}
    assert only(b.handle("brain/control", msg), "brain/state")[0]["ack"]["src"] == "dash-aaaaaa"


# ----------------------------------------------------------------------------- link: parsing

def fake_msg(name, payload, retain=False):
    return types.SimpleNamespace(topic=C.topic(name), payload=payload, retain=retain)


def test_link_validates_incoming_and_answers_ping_at_once():
    b, _ = make()
    link = BrainLink(MqttSettings("127.0.0.1", 1883, "brain", "x"), C, b)
    sent = []
    link.client.publish = lambda topic, payload, qos=0, retain=False: sent.append((topic, payload, qos, retain))
    link._on_message(None, None, fake_msg("sensors", b'{"v":2,"seq":1,"up":1,"bid":2847193551,"ol":2,"or":0,"d":null,"lt":null,"bat":7}'))
    link._on_message(None, None, fake_msg("sensors", b"garbage"))
    assert link.invalid_in == 2 and link.inbox.empty()
    link._on_message(None, None, fake_msg("cmd/motor", b'{"v":1,"seq":1,"up":1,"l":0,"r":0,"ttl":300,"rs":0}'))
    assert link.inbox.empty()                                 # our own topic: ignored
    link._on_message(None, None, fake_msg("ping", b'{"v":2,"seq":3,"up":99,"bid":2847193551}'))
    assert sent and sent[0][0] == C.topic("pong") and b'"ps":3' in sent[0][1]
    link._on_message(None, None, fake_msg("sensors", b'{"v":2,"seq":1,"up":1,"bid":2847193551,"ol":0.1,"or":0,"d":null,"lt":null,"bat":7}'))
    assert link.inbox.qsize() == 1


def test_last_will_is_brain_status_offline():
    b, _ = make()
    link = BrainLink(MqttSettings("127.0.0.1", 1883, "brain", "x"), C, b)
    assert link.lwt == {"v": 2, "seq": 0, "up": link.lwt["up"], "st": "offline"}


def test_quick_reboot_is_detected_by_boot_id_even_when_up_rises():
    b, wall = make()
    feed(b, wall, 3, start_seq=30, start_up=1500)            # robot ran 1.6 s ...
    outs = b.handle("sensors", sensors(0, 1800, bid=77))      # ... rebooted fast: up higher, seq lower
    assert only(outs, "cmd/motor")[0]["rs"] == 0 and b.counters["reboots"] == 1
    assert b.ctrl.inputs[-1].dt_ms == 0
