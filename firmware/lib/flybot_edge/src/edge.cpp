// Port of sim/flybot_sim/edge.py. Every function keeps the Python name and statement order, and
// every floating-point expression keeps Python's operation order, so the results are bit-identical
// (checked against vectors recorded from the Python module: firmware/test/test_crosscheck).
#include "edge.h"

#include <cmath>

#include "py_compat.h"

namespace flybot {

const char* to_str(State s) {
    switch (s) {
        case State::Boot: return "boot";
        case State::Cloud: return "cloud";
        case State::Manual: return "manual";
        case State::Escape: return "escape";
        case State::Fallback: return "fallback";
        case State::Sleep: return "sleep";
        case State::Estop: return "estop";
    }
    return "?";
}

const char* to_str(Mode m) {
    switch (m) {
        case Mode::Auto: return "auto";
        case Mode::Manual: return "manual";
        case Mode::Estop: return "estop";
    }
    return "?";
}

const char* to_str(EventType t) {
    switch (t) {
        case EventType::Escape: return "escape";
        case EventType::Obstacle: return "obstacle";
        case EventType::FallbackOn: return "fallback_on";
        case EventType::FallbackOff: return "fallback_off";
        case EventType::LowBattery: return "low_battery";
    }
    return "?";
}

const char* to_str(FallbackWhy w) {
    switch (w) {
        case FallbackWhy::CmdTimeout: return "cmd_timeout";
        case FallbackWhy::TtlExpired: return "ttl_expired";
        case FallbackWhy::WifiLost: return "wifi_lost";
        case FallbackWhy::MqttLost: return "mqtt_lost";
    }
    return "?";
}

// ---------------------------------------------------------------------------- MotorMixer

double MotorMixer::shape(double v) const {
    v = py_max(-1.0, py_min(1.0, v));
    if (std::fabs(v) < cfg_.motor_dead_zone) return 0.0;
    return py_max(-cfg_.motor_duty_cap, py_min(cfg_.motor_duty_cap, v));
}

Wheels MotorMixer::apply(double lv, double rv, int64_t now, bool immediate) {
    double tl = shape(lv), tr = shape(rv);
    int64_t dt = !last_ms_ ? 0 : py_max(int64_t(0), now - *last_ms_);
    last_ms_ = now;
    if (immediate) {
        l = tl;
        r = tr;
    } else {
        double step = cfg_.motor_ramp_per_s * static_cast<double>(dt) / 1000.0;
        l += py_max(-step, py_min(step, tl - l));
        r += py_max(-step, py_min(step, tr - r));
    }
    return {l, r};
}

// ---------------------------------------------------------------------------- Edge

Edge::Edge(const EdgeConfig& cfg, RandFn rand, void* rand_ctx)
    : streams(cfg.stream_idle_forget_ms), cfg_(cfg), rand_(rand), rand_ctx_(rand_ctx), mixer_(cfg),
      wander_{cfg.fallback_speed, cfg.fallback_speed} {}

void Edge::set_state(State s) {
    if (s != state) {
        state = s;
        status_dirty = true;
    }
}

void Edge::emit(const EdgeEvent& ev) {
    if (ev_count_ == kMaxEvents) {  // never seen in practice (a few events per step at most)
        ev_head_ = (ev_head_ + 1) % kMaxEvents;
        --ev_count_;
        ++events_overflowed;
    }
    events_[(ev_head_ + ev_count_) % kMaxEvents] = ev;
    ++ev_count_;
}

bool Edge::pop_event(EdgeEvent& out) {
    if (ev_count_ == 0) return false;
    out = events_[ev_head_];
    ev_head_ = (ev_head_ + 1) % kMaxEvents;
    --ev_count_;
    return true;
}

bool Edge::in_fallback() const {
    return state == State::Fallback || (state == State::Escape && escape_return_ == State::Fallback);
}

void Edge::enter_fallback(FallbackWhy why, int64_t now) {
    if (in_fallback()) return;
    set_state(State::Fallback);
    fallback_since_ = now;
    good_count_ = 0;
    last_good_ms_.reset();
    next_wander_ms_ = now;
    turning_until_ = 0;
    EdgeEvent ev;
    ev.type = EventType::FallbackOn;
    ev.why = why;
    emit(ev);
}

void Edge::leave_fallback(State to, int64_t now) {
    if (!in_fallback()) return;
    EdgeEvent ev;
    ev.type = EventType::FallbackOff;
    // Python: max(0, now - (self.fallback_since or now)); "or" also treats a start time of 0 as
    // missing (dur 0 then). Reproduced exactly; on the robot fallback never starts at up = 0.
    int64_t since = (fallback_since_ && *fallback_since_ != 0) ? *fallback_since_ : now;
    ev.dur = py_max(int64_t(0), now - since);
    emit(ev);
    fallback_since_.reset();
    if (state == State::Escape)
        escape_return_ = to;
    else
        set_state(to);
}

void Edge::enter_mode_state(int64_t now) {
    escape_return_.reset();
    if (mode == Mode::Estop) {
        leave_fallback(State::Estop, now);
        set_state(State::Estop);
    } else if (mode == Mode::Manual) {
        leave_fallback(State::Manual, now);
        set_state(State::Manual);
    } else if (!connected_) {
        set_state(State::Cloud);
        enter_fallback(FallbackWhy::MqttLost, now);
    } else {
        leave_fallback(State::Cloud, now);
        set_state(State::Cloud);
    }
    watchdog_ref_ = now;
}

// ---------------------------------------------------------------------------- inputs

void Edge::on_link(bool up, int64_t now, FallbackWhy why_lost) {
    connected_ = up;
    if (up && !connected_at_) connected_at_ = now;
    if (!up && state == State::Cloud) enter_fallback(why_lost, now);
}

void Edge::on_mode(const CmdMode& msg, int64_t now, bool retained) {
    StreamKey key = StreamKey::of(Topic::CmdMode, msg.src);
    if (retained)  // rule 10: retained = current state; (re)start the stream, never drop it
        streams.remember(key, msg.seq, msg.up, now);
    else if (!streams.accept(key, msg.seq, msg.up, now))
        return;
    bool changed = msg.m != mode;
    mode = msg.m;
    mode_known = true;
    if (state == State::Boot) {
        status_dirty = true;
        return;
    }
    if (changed) {
        enter_mode_state(now);
        status_dirty = true;
    }
}

void Edge::on_motor(const CmdMotor& msg, int64_t now) {
    if (!streams.accept(StreamKey::of(Topic::CmdMotor), msg.seq, msg.up, now)) return;
    last_valid_motor_ = now;
    motor_cmd_ = Command{msg.l, msg.r, now + msg.ttl};
    if (in_fallback() && mode == Mode::Auto) {
        // Section 5.1 (4): 3 consecutive valid commands, each within the previous one's ttl.
        if (last_good_ms_ && now - *last_good_ms_ <= last_good_ttl_)
            good_count_ += 1;
        else
            good_count_ = 1;
        last_good_ms_ = now;
        last_good_ttl_ = msg.ttl;
        if (good_count_ >= cfg_.leave_fallback_good) {
            leave_fallback(State::Cloud, now);
            watchdog_ref_ = now;
        }
    }
}

void Edge::on_manual(const CmdManual& msg, int64_t now) {
    if (!streams.accept(StreamKey::of(Topic::CmdManual, msg.src), msg.seq, msg.up, now)) return;
    last_valid_manual_ = now;
    manual_cmd_ = Command{msg.l, msg.r, now + msg.ttl};
}

void Edge::on_sleep(const CmdSleep& msg, int64_t now) {
    if (!streams.accept(StreamKey::of(Topic::CmdSleep), msg.seq, msg.up, now)) return;
    if (mode != Mode::Auto || state == State::Boot || state == State::Estop || state == State::Manual)
        return;  // ignored in estop / manual (section 3.7)
    if (msg.s) {
        last_sleep_true_ = now;
        if (state != State::Sleep) {
            leave_fallback(State::Sleep, now);
            escape_return_.reset();
            set_state(State::Sleep);
        }
    } else if (state == State::Sleep) {
        set_state(State::Cloud);
        watchdog_ref_ = now;
    }
}

void Edge::on_invalid(Topic topic) {
    if (topic == Topic::CmdMotor) good_count_ = 0;  // section 5.1 (4): an invalid message resets
}

void Edge::approach_push(const ApproachPoint& p) {
    if (approach_n_ == kApproachLen) {  // keep the newest 8: (approach + [p])[-8:]
        for (int i = 1; i < kApproachLen; ++i) approach_[i - 1] = approach_[i];
        --approach_n_;
    }
    approach_[approach_n_++] = p;
}

void Edge::on_distance(Opt<double> raw_cm, Opt<double> filtered_cm, int64_t now) {
    dist_cm_ = filtered_cm;
    bool reflexes = (state == State::Cloud || state == State::Manual || state == State::Fallback) && !battery_cut;
    if (std::fabs(out.l - out.r) > cfg_.loom_max_turn) last_turn_ms_ = now;
    bool turning = now - last_turn_ms_ < cfg_.loom_turn_holdoff_ms;
    if (!raw_cm || !has_prev_reading_ || turning) {
        // While we turn, the cone sweeps across walls and the range shrinks fast: that is our
        // own rotation, not something approaching (a fly suppresses looming during saccades too).
        loom_hits_ = 0;
        approach_n_ = 0;
    } else {
        int64_t pt = prev_t_;
        double pd = prev_cm_;
        int64_t dt = now - pt;
        if (dt > 0) {
            double raw = *raw_cm;
            double closing = (pd - raw) * 1000.0 / static_cast<double>(dt);  // cm/s, + = closer
            double own = cfg_.ego_speed_cm_s * (out.l + out.r) / 2.0;
            double obj = closing - own;  // the object's own approach speed
            Opt<double> ttc;
            if (obj > cfg_.loom_max_speed_cm_s) {
                loom_hits_ = 0;  // a jump: something appeared
            } else if (raw < cfg_.loom_range_cm && obj > cfg_.loom_min_speed_cm_s) {
                ttc = raw / obj * 1000.0;
                loom_hits_ = *ttc < cfg_.loom_ttc_ms ? loom_hits_ + 1 : 0;
                if (approach_n_ == 0) approach_push({pt, pd, own});
                approach_push({now, raw, own});
            } else {
                loom_hits_ = 0;
                approach_n_ = 0;
            }
            if (!ttc) approach_n_ = 0;
            if (reflexes && loom_hits_ >= cfg_.loom_agree && now - escape_end_ms_ >= cfg_.escape_cooldown_ms && ttc)
                on_loom(raw, *ttc, approach_speed(obj), now);
        }
    }
    has_prev_reading_ = raw_cm.has_value();
    if (raw_cm) {
        prev_t_ = now;
        prev_cm_ = *raw_cm;
    }
}

void Edge::on_battery(double volts, int64_t /*now*/) {
    if (volts < cfg_.battery_cutoff_v && !battery_cut) {
        battery_cut = true;
        EdgeEvent ev;
        ev.type = EventType::LowBattery;
        ev.bat = py_round(volts, 2);
        ev.cut = true;
        emit(ev);
    } else if (volts < cfg_.battery_low_v && !battery_low_sent_) {
        battery_low_sent_ = true;
        EdgeEvent ev;
        ev.type = EventType::LowBattery;
        ev.bat = py_round(volts, 2);
        ev.cut = false;
        emit(ev);
    }
}

// ---------------------------------------------------------------------------- escape

// Habituation (plan step 5.8): one level h (0 = full response) that rises with every looming
// stimulus and recovers with rest. A stimulus clearly faster than the recent ones (by
// hab_dishab_ratio) gets the full response and resets h: dishabituation.

double Edge::approach_speed(double last_estimate) const {
    // The object's approach speed over the whole approach run (up to 8 readings): one pair of
    // readings 50 ms apart is too noisy to classify a stimulus.
    if (approach_n_ < 3) return last_estimate;
    const ApproachPoint& a0 = approach_[0];
    const ApproachPoint& a1 = approach_[approach_n_ - 1];
    NeumaierSum s;
    for (int i = 0; i < approach_n_; ++i) s.add(approach_[i].own);
    double own = s.result() / static_cast<double>(approach_n_);
    if (a1.t > a0.t) return (a0.cm - a1.cm) * 1000.0 / static_cast<double>(a1.t - a0.t) - own;
    return last_estimate;
}

double Edge::hab_level(Opt<int64_t> now) const {
    int64_t n = now ? *now : last_update_.value_or(0);
    if (hab_level_ == 0.0) return 0.0;
    int64_t elapsed = py_max(int64_t(0), n - hab_at_);
    return hab_level_ * std::exp(static_cast<double>(-elapsed) / static_cast<double>(cfg_.hab_tau_ms));
}

void Edge::on_loom(double d_cm, double ttc, double obj_cm_s, int64_t now) {
    double h = cfg_.hab_enabled ? hab_level(now) : 0.0;
    bool dishab = cfg_.hab_enabled && h >= cfg_.hab_dishab_min && hab_ref_ &&
                  obj_cm_s >= cfg_.hab_dishab_ratio * *hab_ref_;
    double resp = dishab ? 1.0 : py_round(1.0 - h, 2);  // reported exactly as used (2 dp)
    if (resp < cfg_.hab_min_resp) resp = 0.0;
    if (cfg_.hab_enabled) {
        if (dishab) {
            hab_level_ = 0.0;  // dishabituation: start afresh
            hab_at_ = now;
            hab_ref_ = obj_cm_s;
        } else {
            hab_level_ = h + cfg_.hab_step * (1.0 - h);
            hab_at_ = now;
            hab_ref_ = !hab_ref_ ? obj_cm_s : *hab_ref_ + cfg_.hab_ref_alpha * (obj_cm_s - *hab_ref_);
        }
    }
    stats.looms += 1;
    loom_hits_ = 0;
    EdgeEvent ev;
    ev.type = EventType::Escape;
    ev.ttc = static_cast<int64_t>(py_min(py_max(py_round_int(ttc), 0.0), 10000.0));
    ev.d = py_round(py_min(py_max(d_cm, 2.0), 400.0), 1);
    ev.resp = resp;
    ev.hab = py_round(py_min(py_max(h, 0.0), 1.0), 2);
    if (resp > 0) {
        start_escape(ev, resp, now);
    } else {
        stats.habituated += 1;
        escape_end_ms_ = now;  // cooldown: one stimulus counts once
        emit(ev);              // seen, not acted on (no lat)
    }
}

void Edge::start_escape(const EdgeEvent& ev, double resp, int64_t now) {
    escape_return_ = state;
    set_state(State::Escape);
    escape_reverse_until_ = now + static_cast<int64_t>(py_round_int(static_cast<double>(cfg_.escape_reverse_ms) * resp));
    escape_turn_until_ = escape_reverse_until_ + static_cast<int64_t>(py_round_int(static_cast<double>(cfg_.escape_turn_ms) * resp));
    escape_speed_now_ = cfg_.escape_speed * resp;
    escape_turn_dir_ = rand() < 0.5 ? 1.0 : -1.0;
    stats.escapes += 1;
    escape_pending_ = true;
    escape_pending_ev_ = ev;
    escape_pending_ev_.detected_ms = now;
}

// ---------------------------------------------------------------------------- main step

Wheels Edge::update(int64_t now) {
    int64_t dt = !last_update_ ? 0 : now - *last_update_;
    last_update_ = now;
    if (in_fallback()) stats.fallback_ms += dt;

    // Boot: wait for odour warm-up and the retained mode (or 2 s after connecting).
    if (state == State::Boot) {
        if (!boot_t0_) boot_t0_ = now;
        bool mode_decided = mode_known || (connected_at_ && now - *connected_at_ >= cfg_.boot_mode_wait_ms);
        bool timed_out = now - *boot_t0_ >= cfg_.boot_link_timeout_ms;
        if (odour_ready_ && (mode_decided || timed_out)) enter_mode_state(now);
    }

    if (state == State::Sleep && now - last_sleep_true_ > cfg_.sleep_timeout_ms) {
        set_state(State::Cloud);  // brain gone while we slept
        watchdog_ref_ = now;
    }

    if (state == State::Cloud) {
        int64_t ref = py_max(watchdog_ref_, last_valid_motor_.value_or(0));
        if (now - ref > cfg_.watchdog_ms) enter_fallback(FallbackWhy::CmdTimeout, now);
    }

    bool immediate = false;
    Wheels tgt{0.0, 0.0};
    if (state == State::Escape) {
        immediate = true;
        if (now < escape_reverse_until_) {
            tgt = {-escape_speed_now_, -escape_speed_now_};
        } else if (now < escape_turn_until_) {
            double s = escape_speed_now_ * escape_turn_dir_;
            tgt = {-s, s};
        } else {
            escape_end_ms_ = now;
            State ret = escape_return_.value_or(State::Cloud);
            escape_return_.reset();
            set_state(ret);
            if (ret == State::Cloud) watchdog_ref_ = py_max(watchdog_ref_, now - cfg_.watchdog_ms / 2);
            tgt = {0.0, 0.0};
        }
    }
    if (state != State::Escape) tgt = target(now);

    if (state == State::Estop || state == State::Boot || state == State::Sleep || battery_cut) {
        tgt = {0.0, 0.0};
        immediate = true;
    }
    out = mixer_.apply(tgt.l, tgt.r, now, immediate);

    if (escape_pending_ && state == State::Escape) {
        escape_pending_ = false;
        EdgeEvent ev = escape_pending_ev_;
        ev.has_lat = true;
        ev.lat = py_round(static_cast<double>(now - ev.detected_ms), 1);
        emit(ev);
    }
    return out;
}

Wheels Edge::target(int64_t now) {
    if (state == State::Cloud) return obstacle_stop(live(motor_cmd_, now));
    if (state == State::Manual) return obstacle_stop(live(manual_cmd_, now));  // section 5.1 (2)
    if (state == State::Fallback) return wander(now);
    return {0.0, 0.0};
}

Wheels Edge::live(const Opt<Command>& cmd, int64_t now) {
    if (!cmd || now >= cmd->expires_ms) return {0.0, 0.0};  // time-to-live expired: stop
    return {cmd->l, cmd->r};
}

Wheels Edge::obstacle_stop(Wheels lr) {
    double fwd = (lr.l + lr.r) / 2.0, turn = (lr.l - lr.r) / 2.0;
    bool blocking = dist_cm_ && *dist_cm_ < cfg_.obstacle_stop_cm && fwd > 0;
    if (blocking && !obstacle_blocking_) {
        stats.obstacle_stops += 1;
        EdgeEvent ev;
        ev.type = EventType::Obstacle;
        ev.d = py_round(py_max(*dist_cm_, 2.0), 1);
        emit(ev);
    }
    obstacle_blocking_ = blocking;
    if (blocking) return {turn, -turn};
    return lr;
}

Wheels Edge::wander(int64_t now) {
    // Fallback: slow wandering with obstacle avoidance on local sensors only (plan 6.8).
    const Opt<double>& d = dist_cm_;
    if (now < turning_until_) {
        bool clear = !d || *d >= cfg_.fallback_clear_cm;
        if (clear && now - turn_started_ >= cfg_.fallback_turn_min_ms) {
            turning_until_ = now;  // way is clear: stop turning early
        } else {
            double s = cfg_.fallback_turn_speed * turn_dir_;
            return {-s, s};
        }
    }
    if (d && *d < cfg_.fallback_turn_cm) {
        turn_dir_ = rand() < 0.5 ? 1.0 : -1.0;
        int64_t span = cfg_.fallback_turn_max_ms - cfg_.fallback_turn_min_ms;
        turn_started_ = now;
        turning_until_ = now + cfg_.fallback_turn_min_ms + static_cast<int64_t>(rand() * static_cast<double>(span));
        double s = cfg_.fallback_turn_speed * turn_dir_;
        return {-s, s};
    }
    if (now >= next_wander_ms_) {
        double c = (rand() * 2.0 - 1.0) * cfg_.fallback_wander_curve;
        wander_ = {cfg_.fallback_speed - c, cfg_.fallback_speed + c};
        next_wander_ms_ = now + cfg_.fallback_wander_every_ms;
    }
    return wander_;
}

}  // namespace flybot
