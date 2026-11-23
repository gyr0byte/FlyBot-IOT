"""Contract messages: building, validation, and every message the simulator actually publishes."""
import json

import pytest

from flybot_sim.config import load_scenario
from flybot_sim.contract import Contract, ContractError, Outbox, offline_status
from flybot_sim.link import FakeLink
from flybot_sim.sim import Simulation

C = Contract("01")


def run_sim(seconds, scenario="default", brain=None, link=None, **kw):
    """Run the simulator offline. brain(sim, sensors_seq) may inject commands each sensors tick."""
    link = link or FakeLink(C)
    sim = Simulation(load_scenario(scenario), link, **kw)
    for _ in range(int(seconds * 1000) // 5):
        seq = sim.step()
        if brain and seq is not None:
            brain(sim, seq)
    return sim, link


def published(link, name):
    return [m for n, m in link.published if n == name]


# ----------------------------------------------------------------------------- contract basics

def test_topics_and_versions_come_from_the_schemas():
    assert C.topic("cmd/motor") == "flybot/01/cmd/motor"
    assert C.name_of("flybot/01/cmd/sleep") == "cmd/sleep"
    assert C.name_of("flybot/02/cmd/sleep") is None
    assert C.versions["brain/control"] == 3 and C.versions["sensors"] == 1


def test_outbox_counts_seq_per_topic_and_validates():
    ob = Outbox(C)
    a = ob.build("ping", 100)
    b = ob.build("ping", 200)
    c = ob.build("reward", 300, r=1, src="btn")
    assert (a["seq"], b["seq"], c["seq"]) == (0, 1, 0)
    assert list(a) == ["v", "seq", "up"]


def test_invalid_outgoing_message_raises_and_does_not_consume_seq():
    ob = Outbox(C)
    with pytest.raises(ContractError):
        ob.build("sensors", 0, ol=1.5, **{"or": 0.1}, d=10.0, lt=None, bat=7.5)   # ol > 1
    with pytest.raises(ContractError):
        ob.build("reward", 0, r=1, src="dash")                                  # bare "dash"
    assert ob.build("reward", 0, r=1, src="btn")["seq"] == 0


def test_decode_rejects_invalid_and_unknown_version():
    with pytest.raises(ContractError):
        C.decode("cmd/motor", b'{"v":1,"seq":1,"up":1,"l":2,"r":0,"ttl":300,"rs":0}')
    with pytest.raises(ContractError):
        C.decode("cmd/motor", b'{"v":2,"seq":1,"up":1,"l":0,"r":0,"ttl":300,"rs":0}')
    with pytest.raises(ContractError):
        C.decode("cmd/motor", b"not json")


def test_last_will_is_the_contract_offline_message():
    lwt = offline_status(C, 2310)
    assert lwt == {"v": 1, "seq": 0, "up": 2310, "st": "offline"}


# ----------------------------------------------------------------------------- what the simulator publishes

def test_every_published_message_is_valid_and_has_increasing_seq():
    sim, link = run_sim(30)
    names = {n for n, _ in link.published}
    assert {"sensors", "status", "events", "ping"} <= names
    by_topic = {}
    for name, msg in link.published:
        C.validate(name, msg)                               # FakeLink already did; be explicit
        by_topic.setdefault(name, []).append(msg["seq"])
    for name, seqs in by_topic.items():
        assert seqs == list(range(len(seqs))), name
    assert all(len(json.dumps(m, separators=(",", ":"))) < 128 for m in published(link, "sensors"))


def test_sensor_rate_is_20_hz_and_keys_in_contract_order():
    sim, link = run_sim(10)
    s = published(link, "sensors")
    assert len(s) == 200
    assert [m["up"] for m in s[:3]] == [0, 50, 100]
    assert list(s[0]) == ["v", "seq", "up", "ol", "or", "d", "lt", "bat"]


def test_odour_is_null_during_warmup_then_numbers():
    sim, link = run_sim(8)
    s = published(link, "sensors")
    assert s[10]["ol"] is None and s[10]["or"] is None
    assert isinstance(s[-1]["ol"], float) and 0.0 <= s[-1]["ol"] <= 1.0


def test_boot_event_then_status_retained_shape():
    sim, link = run_sim(8)   # odour warm-up is 5 s
    ev = published(link, "events")
    assert ev[0]["e"] == "boot" and ev[0]["rst"] == "power_on"
    st = published(link, "status")
    assert st[0]["st"] == "boot" and st[-1]["st"] in ("cloud", "fallback")


def test_without_a_brain_the_robot_falls_back_and_wanders():
    sim, link = run_sim(12)
    kinds = [m["e"] for m in published(link, "events")]
    assert "fallback_on" in kinds
    assert sim.edge.state == "fallback"
    assert sim.robot.path_length > 0.2


def test_answering_each_sensors_message_keeps_cloud_control():
    def brain(sim, seq):
        sim.link.send("cmd/motor", {"v": 1, "seq": seq, "up": sim.t_ms, "l": 0.3, "r": 0.3, "ttl": 300, "rs": seq})

    sim, link = run_sim(12, brain=brain)
    assert sim.edge.state in ("cloud", "escape")
    assert "fallback_on" not in [m["e"] for m in published(link, "events")]
    assert sim.results()["crtt_median_ms"] is not None


def test_estop_retained_keeps_robot_still():
    link = FakeLink(C)
    link.send("cmd/mode", {"v": 2, "seq": 4, "up": 1, "m": "estop", "src": "dash-3fa2c1"}, retained=True)
    sim, _ = run_sim(10, link=link)
    assert sim.edge.state == "estop"
    assert sim.robot.path_length == pytest.approx(0.0, abs=1e-9)
    assert published(link, "status")[-1]["m"] == "estop"


def test_invalid_inbound_command_is_rejected_not_obeyed():
    link = FakeLink(C)
    sim, _ = run_sim(8, link=link)
    link.send("cmd/motor", {"v": 1, "seq": 0, "up": 1, "l": 5.0, "r": 0.0, "ttl": 300, "rs": 0})
    assert link.invalid_in == 1
    sim.step()
    assert sim.edge.motor_cmd is None


def test_buttons_publish_reward_from_btn():
    sim, link = run_sim(1)
    sim.press_reward(1)
    sim.press_reward(-1)
    assert [(m["r"], m["src"]) for m in published(link, "reward")] == [(1, "btn"), (-1, "btn")]


def test_sleep_from_brain_stops_robot():
    state = {"slept": False}

    def brain(sim, seq):
        if sim.t_ms > 9000 and not state["slept"]:
            state["slept"] = True
            sim.link.send("cmd/sleep", {"v": 1, "seq": 0, "up": sim.t_ms, "s": True, "why": "dark"})
        elif not state["slept"]:
            sim.link.send("cmd/motor", {"v": 1, "seq": seq, "up": sim.t_ms, "l": 0.3, "r": 0.3, "ttl": 300, "rs": seq})

    sim, link = run_sim(12, brain=brain)
    assert sim.edge.state == "sleep"
    assert published(link, "status")[-1]["st"] == "sleep"


def test_lockstep_results_leave_latency_empty():
    def brain(sim, seq):
        sim.link.send("cmd/motor", {"v": 1, "seq": seq, "up": sim.t_ms, "l": 0.3, "r": 0.3, "ttl": 300, "rs": seq})

    sim, _ = run_sim(8, brain=brain, lockstep=True)
    r = sim.results()
    assert r["rtt_median_ms"] is None and r["crtt_median_ms"] is None


def test_thrown_object_triggers_escape_through_the_whole_chain():
    """World -> ultrasonic -> distance filter -> edge looming detector -> escape event."""
    def brain(sim, seq):   # a brain that keeps the robot standing still, facing open space
        sim.link.send("cmd/motor", {"v": 1, "seq": seq, "up": sim.t_ms, "l": 0.0, "r": 0.0, "ttl": 300, "rs": seq})
        if 9000 <= sim.t_ms < 9050:     # brain() runs on sensors ticks only (every 50 ms)
            sim.launch_looming()

    sim, link = run_sim(11, scenario="night", brain=brain)   # night: no obstacles, open arena
    ev = [m for m in published(link, "events") if m["e"] == "escape"]
    assert len(ev) == 1
    assert 0 < ev[0]["ttc"] < 700 and ev[0]["d"] < 80
    assert sim.robot.path_length > 0.03          # it actually backed off and turned
