// EdgeConfig: the run-time copy of config.h section B (= sim/flybot_sim/edge_config.py).
// Defaults come from config.h; tests (and later a settings store) may change individual fields.
#pragma once
#include <cstdint>

#include "config.h"

namespace flybot {

struct EdgeConfig {
    // boot and mode
    int64_t boot_mode_wait_ms = cfg::BOOT_MODE_WAIT_MS;
    int64_t boot_link_timeout_ms = cfg::BOOT_LINK_TIMEOUT_MS;
    int64_t odour_warmup_ms = cfg::ODOUR_WARMUP_MS;
    // commands and watchdog
    int64_t watchdog_ms = cfg::WATCHDOG_MS;
    int64_t leave_fallback_good = cfg::LEAVE_FALLBACK_GOOD;
    int64_t sleep_timeout_ms = cfg::SLEEP_TIMEOUT_MS;
    int64_t stream_idle_forget_ms = cfg::STREAM_IDLE_FORGET_MS;
    // motor mixer
    double motor_dead_zone = cfg::MOTOR_DEAD_ZONE;
    double motor_duty_cap = cfg::MOTOR_DUTY_CAP;
    double motor_ramp_per_s = cfg::MOTOR_RAMP_PER_S;
    // looming escape
    double loom_range_cm = cfg::LOOM_RANGE_CM;
    double loom_ttc_ms = cfg::LOOM_TTC_MS;
    double loom_min_speed_cm_s = cfg::LOOM_MIN_SPEED_CM_S;
    double loom_max_speed_cm_s = cfg::LOOM_MAX_SPEED_CM_S;
    int64_t loom_agree = cfg::LOOM_AGREE;
    double ego_speed_cm_s = cfg::EGO_SPEED_CM_S;
    double loom_max_turn = cfg::LOOM_MAX_TURN;
    int64_t loom_turn_holdoff_ms = cfg::LOOM_TURN_HOLDOFF_MS;
    int64_t escape_reverse_ms = cfg::ESCAPE_REVERSE_MS;
    int64_t escape_turn_ms = cfg::ESCAPE_TURN_MS;
    double escape_speed = cfg::ESCAPE_SPEED;
    int64_t escape_cooldown_ms = cfg::ESCAPE_COOLDOWN_MS;
    // habituation
    bool hab_enabled = cfg::HAB_ENABLED;
    double hab_step = cfg::HAB_STEP;
    int64_t hab_tau_ms = cfg::HAB_TAU_MS;
    double hab_min_resp = cfg::HAB_MIN_RESP;
    double hab_dishab_ratio = cfg::HAB_DISHAB_RATIO;
    double hab_dishab_min = cfg::HAB_DISHAB_MIN;
    double hab_ref_alpha = cfg::HAB_REF_ALPHA;
    // obstacle stop
    double obstacle_stop_cm = cfg::OBSTACLE_STOP_CM;
    // fallback wander
    double fallback_speed = cfg::FALLBACK_SPEED;
    double fallback_turn_cm = cfg::FALLBACK_TURN_CM;
    double fallback_clear_cm = cfg::FALLBACK_CLEAR_CM;
    double fallback_turn_speed = cfg::FALLBACK_TURN_SPEED;
    int64_t fallback_turn_min_ms = cfg::FALLBACK_TURN_MIN_MS;
    int64_t fallback_turn_max_ms = cfg::FALLBACK_TURN_MAX_MS;
    int64_t fallback_wander_every_ms = cfg::FALLBACK_WANDER_EVERY_MS;
    double fallback_wander_curve = cfg::FALLBACK_WANDER_CURVE;
    // odour channels
    double odour_full_scale_v = cfg::ODOUR_FULL_SCALE_V;
    double odour_smooth_alpha = cfg::ODOUR_SMOOTH_ALPHA;
    int64_t odour_baseline_window_ms = cfg::ODOUR_BASELINE_WINDOW_MS;
    double odour_clean_threshold = cfg::ODOUR_CLEAN_THRESHOLD;
    int64_t odour_baseline_tau_ms = cfg::ODOUR_BASELINE_TAU_MS;
    int64_t odour_baseline_down_tau_ms = cfg::ODOUR_BASELINE_DOWN_TAU_MS;
    // distance filter
    int64_t dist_median_n = cfg::DIST_MEDIAN_N;
    int64_t dist_valid_ms = cfg::DIST_VALID_MS;
    // battery
    double battery_smooth_alpha = cfg::BATTERY_SMOOTH_ALPHA;
    double battery_low_v = cfg::BATTERY_LOW_V;
    double battery_cutoff_v = cfg::BATTERY_CUTOFF_V;

    // Set a field by its edge_config.py name (tests load the simulator's values this way).
    // Returns false for an unknown name.
    bool set(const char* name, double value);
    static int field_count();
    static const char* field_name(int i);
};

}  // namespace flybot
