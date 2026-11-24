"""Firmware constants: every threshold the edge logic uses, in one place.

This is the simulator's copy of the future firmware/include/config.h (Phase 6): keep names and
values in step when porting. Times are integer milliseconds, distances centimetres, motor values
-1.0 .. +1.0, voltages volts. A scenario can override any field under its `edge:` key.
"""
from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class EdgeConfig:
    # --- boot and mode (contract section 5.1) ---
    boot_mode_wait_ms: int = 2000        # wait this long for the retained cmd/mode, else auto
    boot_link_timeout_ms: int = 10000    # no broker connection by then: leave boot anyway
    odour_warmup_ms: int = 5000          # real MQ-3: minutes; shortened for development

    # --- commands and watchdog (contract sections 5, 5.1, 7) ---
    watchdog_ms: int = 500               # no valid command for this long -> fallback (auto) / stop (manual)
    leave_fallback_good: int = 3         # consecutive valid cmd/motor needed to leave fallback
    sleep_timeout_ms: int = 15000        # asleep without a repeated cmd/sleep s:true -> wake
    stream_idle_forget_ms: int = 600000  # contract section 6 rule 13

    # --- motor mixer (plan step 6.3) ---
    motor_dead_zone: float = 0.08        # |command| below this -> 0
    motor_duty_cap: float = 0.85         # protect the 6 V motors
    motor_ramp_per_s: float = 6.0        # max change of the output per second

    # --- looming escape (plan step 6.6) ---
    loom_range_cm: float = 80.0          # only react to objects closer than this
    loom_ttc_ms: float = 700.0           # time-to-contact threshold
    loom_min_speed_cm_s: float = 40.0    # object's own approach speed (after removing our motion)
    loom_max_speed_cm_s: float = 400.0   # faster than this = an object appearing, not approaching
    loom_agree: int = 2                  # consecutive readings that must agree
    ego_speed_cm_s: float = 30.0         # our forward speed at motor output 1.0 (calibrate on the robot)
    loom_max_turn: float = 0.3           # |left - right| output above this = turning: ignore looming ...
    loom_turn_holdoff_ms: int = 200      # ... and for this long after (the cone sweeping walls looks like looming)
    escape_reverse_ms: int = 400
    escape_turn_ms: int = 450
    escape_speed: float = 0.7
    escape_cooldown_ms: int = 1000
    hab_enabled: bool = True             # looming habituation (plan step 5.8); off = always full response

    # --- obstacle stop ---
    obstacle_stop_cm: float = 12.0       # block forward motion closer than this

    # --- fallback wander (plan step 6.8) ---
    fallback_speed: float = 0.40
    fallback_turn_cm: float = 30.0       # obstacle closer than this -> turn away
    fallback_clear_cm: float = 45.0      # ... until the way ahead is this clear
    fallback_turn_speed: float = 0.45
    fallback_turn_min_ms: int = 400
    fallback_turn_max_ms: int = 1200
    fallback_wander_every_ms: int = 2000  # pick a new gentle curve this often
    fallback_wander_curve: float = 0.15   # max left/right difference while wandering

    # --- odour channels (plan step 6.2) ---
    odour_full_scale_v: float = 1.2      # rise above baseline that maps to 1.0
    odour_smooth_alpha: float = 0.3      # EMA at the sensor rate
    odour_baseline_window_ms: int = 1000  # average this long at the end of warm-up
    odour_clean_threshold: float = 0.03  # below this the air counts as clean ...
    odour_baseline_tau_ms: int = 60000   # ... and the baseline slowly follows the drift
    odour_baseline_down_tau_ms: int = 5000  # below baseline: follow faster

    # --- distance filter ---
    dist_median_n: int = 3               # median of the last n valid readings ...
    dist_valid_ms: int = 200             # ... within this window, else "no echo" (null)

    # --- battery (2S Li-ion) ---
    battery_smooth_alpha: float = 0.1
    battery_low_v: float = 6.8           # warning
    battery_cutoff_v: float = 6.4        # motors disabled

    @classmethod
    def with_overrides(cls, overrides: dict | None) -> "EdgeConfig":
        names = {f.name for f in fields(cls)}
        unknown = set(overrides or {}) - names
        if unknown:
            raise ValueError(f"edge: unknown keys {sorted(unknown)}")
        return cls(**(overrides or {}))
