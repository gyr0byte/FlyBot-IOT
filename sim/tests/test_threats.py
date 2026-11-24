"""Scheduled looming threats (plan step 5.7) and the closest-approach metric."""

from flybot_contract import Contract
from flybot_sim.config import StartPos, Threat, load_scenario
from flybot_sim.edge_config import EdgeConfig
from flybot_sim.link import FakeLink
from flybot_sim.sim import Simulation

C = Contract("01")


def run(sc, start, seconds):
    sim = Simulation(sc, FakeLink(C), C, seed=1, start=start)
    for _ in range(int(seconds * 1000) // 5):
        sim.step()
    return sim


def test_threat_scenario_disables_habituation():
    sc = load_scenario("threat")
    assert sc.threats and sc.edge["hab_enabled"] is False
    assert EdgeConfig().hab_enabled is True                 # the default elsewhere


def test_no_threats_while_the_robot_stays_outside_the_zone():
    sc = load_scenario("threat")
    zone = Threat(x=1.9, y=1.9, radius_m=0.1, every_s=0.5, first_s=0.0)
    sc.threats = [zone]
    sim = run(sc, StartPos(0.3, 0.3, 225), 8)               # facing the corner away from the zone
    closest = min(((x - zone.x) ** 2 + (y - zone.y) ** 2) ** 0.5 for x, y in sim.trail)
    assert closest > zone.radius_m
    assert sim.m.threats == 0


def test_threats_are_launched_on_schedule_while_inside():
    sc = load_scenario("threat")
    sc.threats = [Threat(x=1.0, y=1.0, radius_m=5.0, every_s=2.0, first_s=0.5)]   # the whole arena
    sim = run(sc, StartPos(1.0, 1.0, 0), 12.0)
    boot_s = sim.m.boot_done_ms / 1000
    expected = int((12.0 - boot_s - 0.5) // 2.0) + 1
    assert abs(sim.m.threats - expected) <= 1
    escapes = [m for n, m in sim.link.published if n == "events" and m["e"] == "escape"]
    assert escapes and all(m["resp"] == 1.0 and m["hab"] == 0.0 for m in escapes)


def test_closest_approach_is_recorded_in_results():
    sc = load_scenario("threat")
    sc.threats = []
    s = sc.sources[0]
    sim = run(sc, StartPos(s.x - 0.05, s.y, 0), 9)
    r = sim.results()
    assert r["min_source_dist_m"] is not None and r["min_source_dist_m"] < 0.2
    assert r["threats"] == 0
