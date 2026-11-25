// Hand-written unit tests for the hardware-independent firmware code (run on the PC: pio test -e native).
// The bit-exact comparison with the Python reference is in test_crosscheck.
#include <unity.h>

#include <cmath>
#include <cstdio>
#include <cstring>

#include "edge.h"
#include "py_compat.h"
#include "sensing.h"
#include "streams.h"

using namespace flybot;

// ---------------------------------------------------------------------------- helpers

struct Rng {
    double values[64];
    int n = 0, i = 0;
};
static double rng_next(void* ctx) {
    Rng* r = static_cast<Rng*>(ctx);
    return r->n ? r->values[r->i++ % r->n] : 0.25;
}

struct Rig {
    EdgeConfig cfg;
    Rng rng;
    Edge edge;
    int64_t t = 0;
    uint32_t motor_seq = 0;
    Rig() : edge(cfg, rng_next, &rng) {}
    Rig(const EdgeConfig& c) : cfg(c), edge(cfg, rng_next, &rng) {}
    Wheels run_until(int64_t until) {
        Wheels w{0, 0};
        for (; t <= until; t += 5) w = edge.update(t);
        t -= 5;
        return w;
    }
    void motor(double l, double r, int64_t ttl = 300) {
        CmdMotor m{motor_seq++, 1000 + motor_seq * 50, l, r, ttl, motor_seq};
        edge.on_motor(m, t);
    }
    void mode(Mode m, bool retained = false, const char* src = "dash-3fa2c1", uint32_t seq = 1) {
        CmdMode msg{seq, 5000, m, {}};
        std::strcpy(msg.src, src);
        edge.on_mode(msg, t, retained);
    }
    void boot_to_cloud() {
        edge.set_odour_ready(true);
        edge.on_link(true, t);
        mode(Mode::Auto, true);
        t += 5;
        edge.update(t);
    }
    int count_events(EventType type) {
        int n = 0;
        EdgeEvent ev;
        while (edge.pop_event(ev)) n += ev.type == type;
        return n;
    }
};

static EdgeConfig fast_cfg() {
    EdgeConfig c;
    c.odour_warmup_ms = 5000;  // the simulator's value
    return c;
}

// ---------------------------------------------------------------------------- Python arithmetic

void test_py_round_matches_python() {
    // Values checked with CPython 3.12: round(x, n).
    TEST_ASSERT_EQUAL_DOUBLE(0.12, py_round(0.125, 2));   // exact tie -> even
    TEST_ASSERT_EQUAL_DOUBLE(0.38, py_round(0.375, 2));   // exact tie -> even
    TEST_ASSERT_EQUAL_DOUBLE(2.67, py_round(2.675, 2));   // 2.675 is 2.67499999... in binary
    TEST_ASSERT_EQUAL_DOUBLE(1.0, py_round(1.005, 2));    // 1.00499999...
    TEST_ASSERT_EQUAL_DOUBLE(-0.12, py_round(-0.125, 2));
    TEST_ASSERT_EQUAL_DOUBLE(0.3, py_round(0.25 + 0.05, 1));
    TEST_ASSERT_EQUAL_DOUBLE(38.5, py_round(38.45000000000001, 1));
    TEST_ASSERT_TRUE(std::signbit(py_round(-0.001, 2)));  // -0.0, as Python
    TEST_ASSERT_EQUAL_DOUBLE(0.0, py_round_int(0.5));
    TEST_ASSERT_EQUAL_DOUBLE(2.0, py_round_int(1.5));
    TEST_ASSERT_EQUAL_DOUBLE(2.0, py_round_int(2.5));
    TEST_ASSERT_EQUAL_DOUBLE(-2.0, py_round_int(-2.5));
    TEST_ASSERT_EQUAL_DOUBLE(328.0, py_round_int(400 * 0.82));
}

void test_neumaier_sum_matches_python_312() {
    NeumaierSum s;
    for (int i = 0; i < 10; ++i) s.add(0.1);
    TEST_ASSERT_TRUE(s.result() == 1.0);  // Python 3.12: sum([0.1] * 10) == 1.0 (naive: 0.9999999999999999)
}

// ---------------------------------------------------------------------------- streams (contract section 6)

void test_streams_duplicates_loss_wrap_reboot_idle() {
    StreamTracker st(600000);
    StreamKey k = StreamKey::of(Topic::CmdMotor);
    TEST_ASSERT_TRUE(st.accept(k, 5, 100, 0));          // new sender: any seq
    TEST_ASSERT_FALSE(st.accept(k, 5, 150, 10));        // duplicate
    TEST_ASSERT_FALSE(st.accept(k, 4, 160, 20));        // late
    TEST_ASSERT_TRUE(st.accept(k, 8, 170, 30));         // 2 lost
    TEST_ASSERT_EQUAL_UINT64(2, st.lost);
    TEST_ASSERT_EQUAL_UINT64(2, st.dropped);
    TEST_ASSERT_TRUE(st.accept(k, 0, 50, 40));          // up went down: restart, no loss
    TEST_ASSERT_EQUAL_UINT64(1, st.restarts);
    StreamKey w = StreamKey::of(Topic::CmdSleep);
    TEST_ASSERT_TRUE(st.accept(w, 4294967295u, 10, 0));
    TEST_ASSERT_TRUE(st.accept(w, 0, 20, 1));           // wrap = +1
    TEST_ASSERT_EQUAL_UINT64(2, st.lost);
    StreamKey a = StreamKey::of(Topic::CmdMode, "dash-aaaaaa"), b = StreamKey::of(Topic::CmdMode, "bot");
    TEST_ASSERT_TRUE(st.accept(a, 10, 10, 0));
    TEST_ASSERT_TRUE(st.accept(b, 3, 10, 0));           // other sender: own stream
    TEST_ASSERT_TRUE(st.accept(a, 2, 5, 700000));       // forgotten after 10 min idle: new stream
    TEST_ASSERT_EQUAL_UINT64(1, st.restarts);
}

void test_streams_boot_id_decides_restarts() {
    StreamTracker st;
    StreamKey k = StreamKey::of(Topic::Other);
    TEST_ASSERT_TRUE(st.accept(k, 10, 1000, 0, 77u));
    TEST_ASSERT_FALSE(st.accept(k, 9, 2000, 1, 77u));   // same bid, lower seq: late, up ignored
    TEST_ASSERT_TRUE(st.accept(k, 0, 5000, 2, 78u));    // new bid: reboot even though up rose
    TEST_ASSERT_EQUAL_UINT64(1, st.restarts);
}

void test_streams_fixed_capacity_evicts_least_recent() {
    StreamTracker st;
    char name[16];
    for (int i = 0; i < StreamTracker::kMaxStreams + 3; ++i) {
        std::snprintf(name, sizeof name, "dash-%06x", i);
        TEST_ASSERT_TRUE(st.accept(StreamKey::of(Topic::CmdManual, name), 0, 0, i));
    }
    TEST_ASSERT_EQUAL_INT(StreamTracker::kMaxStreams, st.size());
    TEST_ASSERT_EQUAL_UINT64(3, st.evicted);
}

// ---------------------------------------------------------------------------- state machine (contract section 5)

void test_boot_holds_until_warmup_and_mode() {
    Rig g(fast_cfg());
    g.edge.on_link(true, 0);
    Wheels w = g.run_until(3000);
    TEST_ASSERT_EQUAL_INT((int)State::Boot, (int)g.edge.state);  // odour not ready
    TEST_ASSERT_EQUAL_DOUBLE(0.0, w.l);
    g.edge.set_odour_ready(true);
    g.run_until(3005);
    TEST_ASSERT_EQUAL_INT((int)State::Cloud, (int)g.edge.state);  // 2 s since connect: auto
}

void test_retained_estop_wins_before_moving() {
    Rig g(fast_cfg());
    g.edge.on_link(true, 0);
    g.edge.set_odour_ready(true);
    g.mode(Mode::Estop, true);
    g.run_until(100);
    TEST_ASSERT_EQUAL_INT((int)State::Estop, (int)g.edge.state);
    g.motor(0.8, 0.8);
    Wheels w = g.run_until(400);
    TEST_ASSERT_EQUAL_DOUBLE(0.0, w.l);
    TEST_ASSERT_EQUAL_INT((int)State::Estop, (int)g.edge.state);  // latched; no fallback either
    g.run_until(5000);
    TEST_ASSERT_EQUAL_INT((int)State::Estop, (int)g.edge.state);
    g.mode(Mode::Auto, false, "bot", 2);
    g.run_until(5005);
    TEST_ASSERT_EQUAL_INT((int)State::Cloud, (int)g.edge.state);
}

void test_watchdog_fallback_and_leaving_after_three_good_commands() {
    Rig g(fast_cfg());
    g.boot_to_cloud();
    g.motor(0.5, 0.5);
    g.run_until(g.t + 400);
    TEST_ASSERT_EQUAL_INT((int)State::Cloud, (int)g.edge.state);
    g.run_until(g.t + 200);  // > 500 ms since the last valid command
    TEST_ASSERT_EQUAL_INT((int)State::Fallback, (int)g.edge.state);
    TEST_ASSERT_EQUAL_INT(1, g.count_events(EventType::FallbackOn));
    g.motor(0.3, 0.3); g.run_until(g.t + 50);
    g.motor(0.3, 0.3); g.run_until(g.t + 50);
    g.edge.on_invalid(Topic::CmdMotor);  // resets the count
    g.motor(0.3, 0.3); g.run_until(g.t + 50);
    TEST_ASSERT_EQUAL_INT((int)State::Fallback, (int)g.edge.state);
    g.motor(0.3, 0.3); g.run_until(g.t + 50);
    g.motor(0.3, 0.3); g.run_until(g.t + 50);
    TEST_ASSERT_EQUAL_INT((int)State::Cloud, (int)g.edge.state);
    TEST_ASSERT_EQUAL_INT(1, g.count_events(EventType::FallbackOff));
}

void test_ttl_expiry_stops_the_motors() {
    Rig g(fast_cfg());
    g.boot_to_cloud();
    g.motor(0.6, 0.6, 100);
    Wheels w = g.run_until(g.t + 90);
    TEST_ASSERT_TRUE(w.l > 0.0);
    w = g.run_until(g.t + 250);  // expired: ramps down to 0 (6 per second)
    TEST_ASSERT_EQUAL_DOUBLE(0.0, w.l);
}

void test_link_loss_enters_fallback_and_wanders() {
    Rig g(fast_cfg());
    g.boot_to_cloud();
    g.edge.on_link(false, g.t);
    TEST_ASSERT_EQUAL_INT((int)State::Fallback, (int)g.edge.state);
    Wheels w = g.run_until(g.t + 1000);
    TEST_ASSERT_TRUE(w.l > 0.2 && w.r > 0.2);  // fallback_speed 0.4 +- curve
}

void test_sleep_and_its_timeout() {
    Rig g(fast_cfg());
    g.boot_to_cloud();
    CmdSleep s{0, 100, true, "dark"};
    g.edge.on_sleep(s, g.t);
    TEST_ASSERT_EQUAL_INT((int)State::Sleep, (int)g.edge.state);
    g.run_until(g.t + 15000);
    TEST_ASSERT_EQUAL_INT((int)State::Sleep, (int)g.edge.state);
    g.run_until(g.t + 10);
    TEST_ASSERT_NOT_EQUAL((int)State::Sleep, (int)g.edge.state);  // woke: brain gone
}

void test_obstacle_stop_blocks_forward_motion_only() {
    Rig g(fast_cfg());
    g.boot_to_cloud();
    g.edge.on_distance(none, 10.0, g.t);  // filtered 10 cm < 12 cm
    g.motor(0.5, 0.3);
    g.run_until(g.t + 200);
    // forward part removed, turning kept: (turn, -turn) with turn = 0.1 (Python: (0.1, -0.1))
    TEST_ASSERT_DOUBLE_WITHIN(1e-12, 0.1, g.edge.out.l);
    TEST_ASSERT_DOUBLE_WITHIN(1e-12, -0.1, g.edge.out.r);
    TEST_ASSERT_EQUAL_INT(1, g.count_events(EventType::Obstacle));
}

static void approach(Rig& g, double from_cm, double speed_cm_s, int steps) {
    double d = from_cm;
    for (int i = 0; i < steps; ++i) {
        g.t += 40;
        d -= speed_cm_s * 0.040;
        g.edge.on_distance(d, d, g.t);
        g.edge.update(g.t);
    }
}

void test_looming_escape_reverse_then_turn_with_latency() {
    Rig g(fast_cfg());
    g.boot_to_cloud();
    g.edge.on_distance(60.0, 60.0, g.t);
    approach(g, 60.0, 100.0, 6);  // 1 m/s toward us
    TEST_ASSERT_EQUAL_INT((int)State::Escape, (int)g.edge.state);
    TEST_ASSERT_TRUE(g.edge.out.l < 0 && g.edge.out.r < 0);  // reversing
    EdgeEvent ev;
    bool found = false;
    while (g.edge.pop_event(ev))
        if (ev.type == EventType::Escape) { found = true; break; }
    TEST_ASSERT_TRUE(found);
    TEST_ASSERT_TRUE(ev.has_lat);
    TEST_ASSERT_EQUAL_DOUBLE(1.0, ev.resp);
}

void test_an_object_appearing_is_not_looming() {
    Rig g(fast_cfg());
    g.boot_to_cloud();
    g.t += 40; g.edge.on_distance(200.0, 200.0, g.t); g.edge.update(g.t);
    g.t += 40; g.edge.on_distance(30.0, 30.0, g.t); g.edge.update(g.t);   // jump: 4250 cm/s
    g.t += 40; g.edge.on_distance(29.0, 29.0, g.t); g.edge.update(g.t);   // 25 cm/s: too slow
    TEST_ASSERT_NOT_EQUAL((int)State::Escape, (int)g.edge.state);
}

void test_habituation_weakens_then_stops_the_response() {
    Rig g(fast_cfg());
    g.boot_to_cloud();
    double last_resp = 2.0;
    int zero = 0;
    for (int k = 0; k < 8; ++k) {
        g.edge.on_distance(none, none, g.t);
        g.t += 1500;
        g.edge.update(g.t);
        g.edge.on_distance(60.0, 60.0, g.t);
        approach(g, 60.0, 100.0, 6);
        g.run_until(g.t + 1200);
        EdgeEvent ev;
        while (g.edge.pop_event(ev)) {
            if (ev.type != EventType::Escape) continue;
            TEST_ASSERT_TRUE(ev.resp <= last_resp);
            last_resp = ev.resp;
            if (ev.resp == 0.0) { ++zero; TEST_ASSERT_FALSE(ev.has_lat); }
        }
    }
    TEST_ASSERT_TRUE(zero > 0);
    TEST_ASSERT_TRUE(g.edge.hab_level() > 0.5);
}

void test_battery_cut_disables_the_motors() {
    Rig g(fast_cfg());
    g.boot_to_cloud();
    g.edge.on_battery(6.7, g.t);
    g.edge.on_battery(6.3, g.t);
    g.motor(0.6, 0.6);
    Wheels w = g.run_until(g.t + 100);
    TEST_ASSERT_EQUAL_DOUBLE(0.0, w.l);
    TEST_ASSERT_EQUAL_INT(2, g.count_events(EventType::LowBattery));
}

// ---------------------------------------------------------------------------- sensing

void test_odour_channel_baseline_and_scaling() {
    EdgeConfig c = fast_cfg();
    OdourChannel ch(c);
    int64_t t = 0;
    for (; t < 5000; t += 50) TEST_ASSERT_FALSE(ch.feed(0.5, t).has_value());
    TEST_ASSERT_TRUE(ch.feed(0.5, t).has_value());
    TEST_ASSERT_EQUAL_DOUBLE(0.5, *ch.baseline());
    Opt<double> v;
    for (int i = 0; i < 40; ++i) v = ch.feed(1.1, t += 50);  // +0.6 V -> 0.5
    TEST_ASSERT_DOUBLE_WITHIN(0.01, 0.5, *v);
}

void test_distance_filter_rejects_a_single_bad_reading() {
    EdgeConfig c;
    DistanceFilter f(c);
    f.feed(50.0, 0);
    f.feed(51.0, 40);
    TEST_ASSERT_EQUAL_DOUBLE(50.0, *f.feed(5.0, 80));      // median of 50, 51, 5
    TEST_ASSERT_FALSE(f.feed(none, 400).has_value());  // all older than 200 ms
}

void test_config_has_every_simulator_field() {
    TEST_ASSERT_EQUAL_INT(49, EdgeConfig::field_count());
    EdgeConfig c;
    TEST_ASSERT_TRUE(c.set("loom_ttc_ms", 650.0));
    TEST_ASSERT_EQUAL_DOUBLE(650.0, c.loom_ttc_ms);
    TEST_ASSERT_FALSE(c.set("no_such_field", 1.0));
}

void setUp() {}
void tearDown() {}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_py_round_matches_python);
    RUN_TEST(test_neumaier_sum_matches_python_312);
    RUN_TEST(test_streams_duplicates_loss_wrap_reboot_idle);
    RUN_TEST(test_streams_boot_id_decides_restarts);
    RUN_TEST(test_streams_fixed_capacity_evicts_least_recent);
    RUN_TEST(test_boot_holds_until_warmup_and_mode);
    RUN_TEST(test_retained_estop_wins_before_moving);
    RUN_TEST(test_watchdog_fallback_and_leaving_after_three_good_commands);
    RUN_TEST(test_ttl_expiry_stops_the_motors);
    RUN_TEST(test_link_loss_enters_fallback_and_wanders);
    RUN_TEST(test_sleep_and_its_timeout);
    RUN_TEST(test_obstacle_stop_blocks_forward_motion_only);
    RUN_TEST(test_looming_escape_reverse_then_turn_with_latency);
    RUN_TEST(test_an_object_appearing_is_not_looming);
    RUN_TEST(test_habituation_weakens_then_stops_the_response);
    RUN_TEST(test_battery_cut_disables_the_motors);
    RUN_TEST(test_odour_channel_baseline_and_scaling);
    RUN_TEST(test_distance_filter_rejects_a_single_bad_reading);
    RUN_TEST(test_config_has_every_simulator_field);
    return UNITY_END();
}
