"""End-to-end over the real broker: simulator (user sim) + test driver (user brain).

Needs the broker running (infra\\start-broker.cmd local); skipped otherwise. Never run while
the real brain is running: the driver uses the brain credentials.
"""
import socket
import time

import pytest

from flybot_sim.config import MqttSettings, load_scenario
from flybot_sim.contract import Contract
from flybot_sim.driver import Driver, RobotClock
from flybot_sim.link import Link
from flybot_sim.runner import run_lockstep, run_realtime
from flybot_sim.sim import Simulation


def broker_up() -> bool:
    try:
        s = MqttSettings.from_env("SIM")
        with socket.create_connection((s.host, s.port), timeout=0.5):
            return True
    except (OSError, FileNotFoundError, KeyError):
        return False


pytestmark = pytest.mark.broker
needs_broker = pytest.mark.skipif(not broker_up(), reason="broker not running")


@pytest.fixture
def session(request):
    """(sim, driver) connected to the broker; both cleaned up afterwards."""
    script, lockstep = request.param
    contract = Contract()
    link = Link(MqttSettings.from_env("SIM"), contract, strict=True)
    assert link.start(), "sim could not connect"
    drv = Driver(MqttSettings.from_env("BRAIN"), script, contract=contract)
    drv.start()
    time.sleep(0.3)
    sim = Simulation(load_scenario("default"), link, contract, lockstep=lockstep)
    yield sim, drv
    drv.stop()
    link.stop()


def until(seconds):
    return lambda sim: sim.t_ms >= seconds * 1000


@needs_broker
@pytest.mark.parametrize("session", [("straight", True)], indirect=True)
def test_lockstep_runs_faster_than_real_time_and_matches_every_command(session):
    sim, drv = session
    wall0 = time.perf_counter()
    st = run_lockstep(sim, until(20))
    wall = time.perf_counter() - wall0
    if sim.edge.mode != "auto":
        pytest.skip(f"a retained cmd/mode '{sim.edge.mode}' is set on the broker")
    assert wall < 20 / 3, f"lockstep took {wall:.1f} s for 20 s of robot time"
    assert st.matched > 250 and st.timeouts <= 2
    assert sim.edge.state in ("cloud", "escape")
    r = sim.results()
    assert r["messages_lost"] == 0
    assert r["rtt_median_ms"] is None and r["crtt_median_ms"] is None   # lockstep: no latency figures
    assert sim.robot.path_length > 0.5


@needs_broker
@pytest.mark.parametrize("session", [("straight:2,silent:2,straight", True)], indirect=True)
def test_driver_silence_triggers_fallback_and_commands_recover(session):
    sim, _ = session
    run_lockstep(sim, until(16))
    if sim.edge.mode != "auto":
        pytest.skip(f"a retained cmd/mode '{sim.edge.mode}' is set on the broker")
    assert sim.edge.state in ("cloud", "escape")
    assert sim.results()["fallback_time_s"] > 0.5


@needs_broker
@pytest.mark.parametrize("session", [("straight", False)], indirect=True)
def test_realtime_measures_latency(session):
    sim, _ = session
    run_realtime(sim, until(8))
    r = sim.results()
    assert r["crtt_median_ms"] is not None and r["crtt_median_ms"] < 100
    assert r["rtt_median_ms"] is not None and r["rtt_median_ms"] < 100


def test_robot_clock_is_monotonic_and_caps_gaps():
    c = RobotClock()
    assert [c.advance(u) for u in (1000, 1050, 1100)] == [0, 50, 100]
    assert c.advance(40) == 100 and c.reboots == 1          # reboot: step counts 0
    assert c.advance(90) == 150
    assert c.advance(5090) == 250 and c.gaps == 1           # 5 s gap capped at 100 ms


@needs_broker
@pytest.mark.parametrize("realtime", [False, True], ids=["lockstep", "realtime"])
def test_batch_writes_one_row_per_trial(tmp_path, realtime):
    import csv
    from flybot_sim.batch import COLUMNS, run_trials

    sc = load_scenario("default")
    sc.duration_s = 3 if realtime else 20

    def factory(contract):
        link = Link(MqttSettings.from_env("SIM"), contract, strict=True)
        assert link.start()
        return link

    drv = Driver(MqttSettings.from_env("BRAIN"), "arc")
    drv.start()
    try:
        out = tmp_path / "b.csv"
        rows = run_trials(sc, 2, "driver-arc", realtime, out, factory)
    finally:
        drv.stop()
    with out.open(encoding="utf-8") as f:
        got = list(csv.DictReader(f))
    assert len(got) == 2 and list(got[0]) == COLUMNS
    assert [r["seed"] for r in got] == [str(sc.seed), str(sc.seed + 1)]
    assert [r["start_index"] for r in got] == ["0", "1"]
    assert all(r["mode"] == ("realtime" if realtime else "lockstep") for r in got)
    assert all(r["controller"] == "driver-arc" for r in got)
    if realtime:
        assert all(r["crtt_median_ms"] != "" for r in got)
    else:
        assert all(r["rtt_median_ms"] == "" and r["crtt_median_ms"] == "" for r in got)
        assert all(float(r["wall_duration_s"]) < float(r["sim_duration_s"]) for r in got)
