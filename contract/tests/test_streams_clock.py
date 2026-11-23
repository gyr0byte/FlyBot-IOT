"""StreamTracker (contract section 6) and RobotClock (contract section 7.1)."""
from flybot_contract import RobotClock, StreamTracker


def test_stream_tracker_counts_loss_and_handles_wrap():
    t = StreamTracker()
    assert t.accept(("x",), 4294967294, 10, 0)
    assert t.accept(("x",), 4294967295, 20, 0)
    assert t.accept(("x",), 1, 30, 0)        # wrapped: 0 was lost
    assert t.lost == 1
    assert not t.accept(("x",), 1, 30, 0)    # duplicate
    assert not t.accept(("x",), 0, 40, 0)    # late
    assert t.accept(("y",), 77, 5, 0)        # new sender: no loss counted
    assert t.lost == 1 and t.dropped == 2


def test_stream_tracker_detects_restart_by_up_going_down():
    t = StreamTracker()
    t.accept(("s",), 900, 45000, 0)
    assert t.accept(("s",), 0, 10, 0) and t.last_restarted and t.restarts == 1
    assert t.lost == 0                       # the jump in seq is not loss
    assert t.accept(("s",), 1, 60, 0) and not t.last_restarted


def test_stream_tracker_forgets_idle_senders():
    t = StreamTracker(idle_forget_ms=1000)
    t.accept(("x",), 50, 100, 0)
    assert t.accept(("x",), 3, 200, 5000)    # forgotten: starts again, no loss
    assert t.lost == 0


def test_robot_clock_is_monotonic_caps_gaps_and_reports_reboots():
    c = RobotClock()
    assert [c.advance(u) for u in (1000, 1050, 1100)] == [0, 50, 100]
    assert c.advance(40) == 100 and c.rebooted and c.reboots == 1      # reboot: step counts 0
    assert c.advance(90) == 150 and not c.rebooted
    assert c.advance(5090) == 250 and c.gaps == 1 and c.step == 100    # 5 s gap capped at 100 ms
    assert c.advance(5590) == 750 and c.gaps == 1                      # exactly 500 ms is not a gap


def test_bid_change_is_a_reboot_and_same_bid_late_message_is_dropped():
    t = StreamTracker()
    assert t.accept(("s",), 900, 45000, 0, bid=111)
    assert not t.accept(("s",), 899, 44950, 0, bid=111)      # late: lower seq AND lower up, same boot
    assert t.dropped == 1 and t.restarts == 0
    assert t.accept(("s",), 0, 2000, 0, bid=222) and t.last_restarted   # new boot, even with up rising
    assert t.lost == 0 and t.restarts == 1
    assert t.accept(("s",), 1, 1990, 0, bid=222)             # same boot: up is ignored, seq decides


def test_quick_reboot_with_higher_up_is_still_a_reboot():
    t = StreamTracker()
    t.accept(("s",), 30, 1500, 0, bid=111)
    assert t.accept(("s",), 0, 1800, 0, bid=333) and t.last_restarted  # up rose, seq fell: new bid wins


def test_robot_clock_reboots_on_bid_change_only():
    c = RobotClock()
    assert [c.advance(u, 7) for u in (1000, 1050, 1100)] == [0, 50, 100]
    assert c.advance(1200, 9) == 100 and c.rebooted          # new bid: step 0 although up rose
    assert c.advance(1250, 9) == 150 and not c.rebooted
    assert c.advance(1240, 9) == 150 and not c.rebooted      # same boot, up back: counts 0, no reboot
