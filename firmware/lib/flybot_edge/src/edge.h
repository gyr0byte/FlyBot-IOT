// FIRMWARE EDGE LOGIC: the robot's own behaviour, independent of the cloud brain.
//
// A 1:1 port of sim/flybot_sim/edge.py (plan steps 6.6-6.8, contract sections 5, 5.1, 6, 7):
// boot hold, state machine, command time-to-live and 500 ms watchdog, fallback wander with
// obstacle avoidance and leaving after 3 good commands, looming escape from time-to-contact with
// turning suppression and the "appeared" rule, habituation with the relative (dishabituation) rule,
// sleep, e-stop. Pure C++17: no Arduino calls, no heap use after construction, all times are
// integer milliseconds passed in, randomness comes from an injected rand() -> [0, 1).
//
// Inputs (call as things happen):
//   on_link(up, now)                       MQTT connected / lost
//   on_mode(msg, now, retained)            cmd/mode   (already schema-validated)
//   on_motor(msg, now)                     cmd/motor
//   on_manual(msg, now)                    cmd/manual
//   on_sleep(msg, now)                     cmd/sleep
//   on_invalid(topic)                      a message on topic failed validation
//   on_distance(raw_cm, filtered_cm, now)  every ultrasonic reading (raw may be empty)
//   on_battery(volts, now)                 smoothed pack voltage
//   set_odour_ready(ready)                 odour warm-up / calibration finished
// Output:
//   update(now) -> (left, right)           call every control step (5 ms on the robot)
//   pop_event(ev)                          events for flybot/01/events (without v/seq/up/bid)
//   state, mode, status_dirty              for flybot/01/status
#pragma once
#include <cstdint>
#include "opt.h"

#include "edge_config.h"
#include "streams.h"

namespace flybot {

enum class State : uint8_t { Boot, Cloud, Manual, Escape, Fallback, Sleep, Estop };
enum class Mode : uint8_t { Auto, Manual, Estop };
const char* to_str(State s);
const char* to_str(Mode m);

struct CmdMotor { uint32_t seq; int64_t up; double l, r; int64_t ttl; uint32_t rs; };
struct CmdManual { uint32_t seq; int64_t up; double l, r; int64_t ttl; uint32_t rs; char src[16]; };
struct CmdMode { uint32_t seq; int64_t up; Mode m; char src[16]; };
struct CmdSleep { uint32_t seq; int64_t up; bool s; char why[8]; };

enum class EventType : uint8_t { Escape, Obstacle, FallbackOn, FallbackOff, LowBattery };
enum class FallbackWhy : uint8_t { CmdTimeout, TtlExpired, WifiLost, MqttLost };
const char* to_str(EventType t);
const char* to_str(FallbackWhy w);

// One event, with only the fields its type uses (contract section 3.2).
struct EdgeEvent {
    EventType type = EventType::Escape;
    int64_t ttc = 0;          // escape
    double d = 0;             // escape, obstacle
    bool has_lat = false;     // escape: lat only when resp > 0
    double lat = 0;
    double resp = 0, hab = 0; // escape
    int64_t detected_ms = 0;  // escape: robot time of detection (firmware use, not on the wire)
    FallbackWhy why = FallbackWhy::CmdTimeout;  // fallback_on
    int64_t dur = 0;          // fallback_off
    double bat = 0;           // low_battery
    bool cut = false;         // low_battery
};

struct Wheels { double l, r; };

class MotorMixer {
public:
    explicit MotorMixer(const EdgeConfig& cfg) : cfg_(cfg) {}
    Wheels apply(double l, double r, int64_t now, bool immediate);
    double l = 0.0, r = 0.0;

private:
    double shape(double v) const;
    const EdgeConfig& cfg_;
    Opt<int64_t> last_ms_;
};

struct EdgeStats { int64_t escapes = 0, fallback_ms = 0, obstacle_stops = 0, looms = 0, habituated = 0; };

class Edge {
public:
    using RandFn = double (*)(void* ctx);
    Edge(const EdgeConfig& cfg, RandFn rand, void* rand_ctx = nullptr);

    // inputs
    void on_link(bool up, int64_t now, FallbackWhy why_lost = FallbackWhy::MqttLost);
    void set_odour_ready(bool ready) { odour_ready_ = ready; }
    void on_mode(const CmdMode& msg, int64_t now, bool retained = false);
    void on_motor(const CmdMotor& msg, int64_t now);
    void on_manual(const CmdManual& msg, int64_t now);
    void on_sleep(const CmdSleep& msg, int64_t now);
    void on_invalid(Topic topic);
    void on_distance(Opt<double> raw_cm, Opt<double> filtered_cm, int64_t now);
    void on_battery(double volts, int64_t now);

    // outputs
    Wheels update(int64_t now);
    bool pop_event(EdgeEvent& out);
    int pending_events() const { return ev_count_; }
    double hab_level(Opt<int64_t> now = none) const;
    bool in_fallback() const;

    State state = State::Boot;
    Mode mode = Mode::Auto;  // assumed until the retained cmd/mode arrives (section 5.1)
    bool mode_known = false;
    bool status_dirty = true;
    bool battery_cut = false;
    Wheels out{0.0, 0.0};
    StreamTracker streams;
    EdgeStats stats;
    uint32_t events_overflowed = 0;

private:
    struct Command { double l, r; int64_t expires_ms; };
    struct ApproachPoint { int64_t t; double cm; double own; };
    static constexpr int kMaxEvents = 32;
    static constexpr int kApproachLen = 8;

    double rand() { return rand_(rand_ctx_); }
    void set_state(State s);
    void emit(const EdgeEvent& ev);
    void enter_fallback(FallbackWhy why, int64_t now);
    void leave_fallback(State to, int64_t now);
    void enter_mode_state(int64_t now);
    double approach_speed(double last_estimate) const;
    void on_loom(double d_cm, double ttc, double obj_cm_s, int64_t now);
    void start_escape(const EdgeEvent& ev, double resp, int64_t now);
    Wheels target(int64_t now);
    static Wheels live(const Opt<Command>& cmd, int64_t now);
    Wheels obstacle_stop(Wheels lr);
    Wheels wander(int64_t now);
    void approach_push(const ApproachPoint& p);

    const EdgeConfig& cfg_;
    RandFn rand_;
    void* rand_ctx_;
    MotorMixer mixer_;

    bool connected_ = false;
    Opt<int64_t> connected_at_, boot_t0_;
    bool odour_ready_ = false;

    Opt<Command> motor_cmd_, manual_cmd_;
    Opt<int64_t> last_valid_motor_, last_valid_manual_;
    int64_t watchdog_ref_ = 0;

    int64_t good_count_ = 0;
    Opt<int64_t> last_good_ms_;
    int64_t last_good_ttl_ = 0;
    Opt<int64_t> fallback_since_;

    int64_t last_sleep_true_ = 0;

    Opt<State> escape_return_;
    int64_t escape_reverse_until_ = 0, escape_turn_until_ = 0;
    double escape_turn_dir_ = 1.0;
    int64_t escape_end_ms_ = -1000000000;
    bool escape_pending_ = false;
    EdgeEvent escape_pending_ev_{};
    int64_t loom_hits_ = 0;
    double hab_level_ = 0.0;
    int64_t hab_at_ = 0;
    Opt<double> hab_ref_;
    ApproachPoint approach_[kApproachLen]{};
    int approach_n_ = 0;
    double escape_speed_now_ = 0.0;
    bool has_prev_reading_ = false;
    int64_t prev_t_ = 0;
    double prev_cm_ = 0.0;
    int64_t last_turn_ms_ = -1000000000;

    Opt<double> dist_cm_;
    bool obstacle_blocking_ = false;

    Wheels wander_;
    int64_t next_wander_ms_ = 0, turning_until_ = 0, turn_started_ = 0;
    double turn_dir_ = 1.0;

    bool battery_low_sent_ = false;
    Opt<int64_t> last_update_;

    EdgeEvent events_[kMaxEvents]{};
    int ev_head_ = 0, ev_count_ = 0;
};

}  // namespace flybot
