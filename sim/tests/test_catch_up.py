"""Experiments wait for the brain to process a trial's last sensors message (sleep-experiment fix)."""
import sys
import threading
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from learning_experiment import Monitor  # noqa: E402


def monitor(rs=None):
    m = object.__new__(Monitor)                    # no MQTT connection needed for the waiting rule
    m.lock = threading.Lock()
    m.state = None if rs is None else {"rs": rs}
    return m


def sim(seq):
    return types.SimpleNamespace(last_sensors_seq=seq)


@pytest.mark.parametrize("rs", [96, 98, 100])
def test_caught_up_when_the_newest_state_used_one_of_the_last_sensors(rs):
    monitor(rs).wait_caught_up(sim(100), timeout_s=0.2)


@pytest.mark.parametrize("rs", [None, 50, 95, 1200])
def test_not_caught_up_while_behind_or_on_a_state_from_an_earlier_boot(rs):
    with pytest.raises(RuntimeError, match="did not catch up"):
        monitor(rs).wait_caught_up(sim(100), timeout_s=0.2)


def test_waits_until_the_brain_reaches_the_end():
    m = monitor(10)
    threading.Timer(0.3, lambda: m.state.update(rs=99)).start()
    m.wait_caught_up(sim(100), timeout_s=3.0)


def test_nothing_to_wait_for_without_sensors():
    monitor().wait_caught_up(sim(None), timeout_s=0.1)
