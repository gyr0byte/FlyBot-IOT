#include "edge_config.h"

#include <cstring>

namespace flybot {

// Every field once: name, member. Keep in the order of edge_config.py.
#define FLYBOT_EDGE_FIELDS(X)                                                                    \
    X(boot_mode_wait_ms) X(boot_link_timeout_ms) X(odour_warmup_ms) X(watchdog_ms)             \
    X(leave_fallback_good) X(sleep_timeout_ms) X(stream_idle_forget_ms) X(motor_dead_zone)      \
    X(motor_duty_cap) X(motor_ramp_per_s) X(loom_range_cm) X(loom_ttc_ms) X(loom_min_speed_cm_s) \
    X(loom_max_speed_cm_s) X(loom_agree) X(ego_speed_cm_s) X(loom_max_turn)                      \
    X(loom_turn_holdoff_ms) X(escape_reverse_ms) X(escape_turn_ms) X(escape_speed)               \
    X(escape_cooldown_ms) X(hab_enabled) X(hab_step) X(hab_tau_ms) X(hab_min_resp)               \
    X(hab_dishab_ratio) X(hab_dishab_min) X(hab_ref_alpha) X(obstacle_stop_cm) X(fallback_speed) \
    X(fallback_turn_cm) X(fallback_clear_cm) X(fallback_turn_speed) X(fallback_turn_min_ms)      \
    X(fallback_turn_max_ms) X(fallback_wander_every_ms) X(fallback_wander_curve)                 \
    X(odour_full_scale_v) X(odour_smooth_alpha) X(odour_baseline_window_ms)                      \
    X(odour_clean_threshold) X(odour_baseline_tau_ms) X(odour_baseline_down_tau_ms)              \
    X(dist_median_n) X(dist_valid_ms) X(battery_smooth_alpha) X(battery_low_v) X(battery_cutoff_v)

namespace {
template <typename T>
void assign(T& member, double v) { member = static_cast<T>(v); }
inline void assign(bool& member, double v) { member = v != 0.0; }

#define FLYBOT_NAME(f) #f,
const char* const kNames[] = {FLYBOT_EDGE_FIELDS(FLYBOT_NAME)};
#undef FLYBOT_NAME
}  // namespace

bool EdgeConfig::set(const char* name, double value) {
#define FLYBOT_SET(f)                     \
    if (std::strcmp(name, #f) == 0) {     \
        assign(f, value);                 \
        return true;                      \
    }
    FLYBOT_EDGE_FIELDS(FLYBOT_SET)
#undef FLYBOT_SET
    return false;
}

int EdgeConfig::field_count() { return static_cast<int>(sizeof(kNames) / sizeof(kNames[0])); }

const char* EdgeConfig::field_name(int i) { return (i >= 0 && i < field_count()) ? kNames[i] : nullptr; }

}  // namespace flybot
