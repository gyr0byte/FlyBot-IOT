// FlyBot firmware configuration: every pin and every threshold, in one place.
//
// Section A (pins) and section C (hardware) are firmware-only. Section B mirrors
// sim/flybot_sim/edge_config.py field by field (same names in UPPER_CASE, same values, same units),
// so the edge logic behaves like the simulator's. firmware/tools/check_config.py compares the two
// and fails on any difference that is not listed in its ALLOWED_DIFFERENCES table (with a reason).
//
// Units: times in integer milliseconds, distances in centimetres, motor values -1.0 .. +1.0,
// voltages in volts. Pure constants: no Arduino headers, so the PC unit tests can include it.
#pragma once
#include <cstdint>

namespace flybot {
namespace cfg {

// ============================================================================ A. Pins (ESP32 DevKit, WROOM-32)
// See docs/firmware-decisions.md (decision 1) and docs/hardware-bringup.md for the wiring.
// Analog inputs are on ADC1 only (GPIO 32-39): ADC2 cannot be read while Wi-Fi is on.
// Not used: GPIO 6-11 (flash), 1/3 (USB serial), 0/5/12/15 (strapping pins).
constexpr int PIN_MQ3_LEFT     = 36;  // ADC1_CH0 (VP), input only. MQ-3 AO through a divider
constexpr int PIN_MQ3_RIGHT    = 39;  // ADC1_CH3 (VN), input only. MQ-3 AO through a divider
constexpr int PIN_LDR          = 34;  // ADC1_CH6, input only. LDR divider (optional)
constexpr int PIN_BATTERY      = 35;  // ADC1_CH7, input only. Pack voltage through a divider

constexpr int PIN_US_TRIG      = 18;  // HC-SR04 trigger (3.3 V is enough for the 5 V module)
constexpr int PIN_US_ECHO      = 19;  // HC-SR04 echo, 5 V -> 3.3 V through a divider (see bring-up)

constexpr int PIN_MOTOR_PWMA   = 25;  // TB6612 PWMA (left motor speed)
constexpr int PIN_MOTOR_AIN1   = 26;  // TB6612 AIN1
constexpr int PIN_MOTOR_AIN2   = 27;  // TB6612 AIN2
constexpr int PIN_MOTOR_PWMB   = 32;  // TB6612 PWMB (right motor speed)
constexpr int PIN_MOTOR_BIN1   = 33;  // TB6612 BIN1
constexpr int PIN_MOTOR_BIN2   = 4;   // TB6612 BIN2
constexpr int PIN_MOTOR_STBY   = 23;  // TB6612 STBY (fit a 10 k pull-down: motors off until the firmware runs)

constexpr int PIN_MOOD_LED     = 16;  // WS2812B data in (through a 330 ohm resistor)
constexpr int PIN_BUZZER       = 17;  // buzzer (passive: driven with a tone; active: on/off)
constexpr int PIN_BTN_REWARD   = 21;  // "good" button to GND (internal pull-up)
constexpr int PIN_BTN_PUNISH   = 22;  // "bad" button to GND (internal pull-up)
constexpr int PIN_STATUS_LED   = 2;   // on-board blue LED: connection status

constexpr int PIN_ENC_LEFT     = 13;  // optional wheel encoder, left (see ENCODERS_ENABLED)
constexpr int PIN_ENC_RIGHT    = 14;  // optional wheel encoder, right

// ============================================================================ B. Edge thresholds (= edge_config.py)
// --- boot and mode (contract section 5.1) ---
constexpr int32_t BOOT_MODE_WAIT_MS       = 2000;    // wait this long for the retained cmd/mode, else auto
constexpr int32_t BOOT_LINK_TIMEOUT_MS    = 10000;   // no broker connection by then: leave boot anyway
constexpr int32_t ODOUR_WARMUP_MS         = 60000;   // real MQ-3 (sim: 5000, shortened for development)

// --- commands and watchdog (contract sections 5, 5.1, 7) ---
constexpr int32_t WATCHDOG_MS             = 500;     // no valid command for this long -> fallback / stop
constexpr int32_t LEAVE_FALLBACK_GOOD     = 3;       // consecutive valid cmd/motor needed to leave fallback
constexpr int32_t SLEEP_TIMEOUT_MS        = 15000;   // asleep without a repeated cmd/sleep s:true -> wake
constexpr int32_t STREAM_IDLE_FORGET_MS   = 600000;  // contract section 6 rule 13

// --- motor mixer (plan step 6.3) ---
constexpr double  MOTOR_DEAD_ZONE         = 0.08;    // |command| below this -> 0
constexpr double  MOTOR_DUTY_CAP          = 0.85;    // protect the 6 V motors
constexpr double  MOTOR_RAMP_PER_S        = 6.0;     // max change of the output per second

// --- looming escape (plan step 6.6) ---
constexpr double  LOOM_RANGE_CM           = 80.0;    // only react to objects closer than this
constexpr double  LOOM_TTC_MS             = 700.0;   // time-to-contact threshold
constexpr double  LOOM_MIN_SPEED_CM_S     = 40.0;    // object's own approach speed (after removing ours)
constexpr double  LOOM_MAX_SPEED_CM_S     = 400.0;   // faster = an object appearing, not approaching
constexpr int32_t LOOM_AGREE              = 2;       // consecutive readings that must agree
constexpr double  EGO_SPEED_CM_S          = 30.0;    // our forward speed at output 1.0 (calibrate!)
constexpr double  LOOM_MAX_TURN           = 0.3;     // |left - right| above this = turning: ignore looming ...
constexpr int32_t LOOM_TURN_HOLDOFF_MS    = 200;     // ... and for this long after
constexpr int32_t ESCAPE_REVERSE_MS       = 400;
constexpr int32_t ESCAPE_TURN_MS          = 450;
constexpr double  ESCAPE_SPEED            = 0.7;
constexpr int32_t ESCAPE_COOLDOWN_MS      = 1000;
// --- looming habituation (plan step 5.8) ---
constexpr bool    HAB_ENABLED             = true;
constexpr double  HAB_STEP                = 0.3;     // after each stimulus: h += step * (1 - h)
constexpr int32_t HAB_TAU_MS              = 60000;   // recovery time constant
constexpr double  HAB_MIN_RESP            = 0.2;     // response = 1 - h; below this: no movement
constexpr double  HAB_DISHAB_RATIO        = 1.5;     // this much faster than recent stimuli dishabituates ...
constexpr double  HAB_DISHAB_MIN          = 0.05;    // ... while habituated at least this much
constexpr double  HAB_REF_ALPHA           = 0.3;     // recent-stimulus speed average, weight of the newest

// --- obstacle stop ---
constexpr double  OBSTACLE_STOP_CM        = 12.0;    // block forward motion closer than this

// --- fallback wander (plan step 6.8) ---
constexpr double  FALLBACK_SPEED          = 0.40;
constexpr double  FALLBACK_TURN_CM        = 30.0;    // obstacle closer than this -> turn away ...
constexpr double  FALLBACK_CLEAR_CM       = 45.0;    // ... until the way ahead is this clear
constexpr double  FALLBACK_TURN_SPEED     = 0.45;
constexpr int32_t FALLBACK_TURN_MIN_MS    = 400;
constexpr int32_t FALLBACK_TURN_MAX_MS    = 1200;
constexpr int32_t FALLBACK_WANDER_EVERY_MS = 2000;   // pick a new gentle curve this often
constexpr double  FALLBACK_WANDER_CURVE   = 0.15;    // max left/right difference while wandering

// --- odour channels (plan step 6.2) ---
constexpr double  ODOUR_FULL_SCALE_V      = 1.2;     // rise above baseline (sensor volts) that maps to 1.0
constexpr double  ODOUR_SMOOTH_ALPHA      = 0.3;     // EMA at the sensor rate
constexpr int32_t ODOUR_BASELINE_WINDOW_MS = 1000;   // average this long at the end of warm-up
constexpr double  ODOUR_CLEAN_THRESHOLD   = 0.03;    // below this the air counts as clean ...
constexpr int32_t ODOUR_BASELINE_TAU_MS   = 60000;   // ... and the baseline slowly follows the drift
constexpr int32_t ODOUR_BASELINE_DOWN_TAU_MS = 5000; // below baseline: follow faster

// --- distance filter ---
constexpr int32_t DIST_MEDIAN_N           = 3;       // median of the last n valid readings ...
constexpr int32_t DIST_VALID_MS           = 200;     // ... within this window, else "no echo" (null)

// --- battery (2S Li-ion) ---
constexpr double  BATTERY_SMOOTH_ALPHA    = 0.1;
constexpr double  BATTERY_LOW_V           = 6.8;     // warning
constexpr double  BATTERY_CUTOFF_V        = 6.4;     // motors disabled

// ============================================================================ C. Hardware and timing (firmware only)
// --- task rates (plan step 6.5; the same as the simulator's) ---
constexpr int32_t CONTROL_PERIOD_MS       = 5;       // reflex task: edge update + motors (sim PHYSICS_MS)
constexpr int32_t US_PERIOD_MS            = 40;      // ultrasonic: 25 Hz (sim ultrasonic.rate_hz)
constexpr int32_t SENSORS_PERIOD_MS       = 50;      // sensor task + sensors message: 20 Hz
constexpr int32_t PING_PERIOD_MS          = 1000;    // ping: 1 Hz
constexpr int32_t STATUS_PERIOD_MS        = 10000;   // status heartbeat
constexpr int32_t RTT_WINDOW_MS           = 10000;   // status.rtt / crtt: median over this window
constexpr int32_t TASK_WDT_TIMEOUT_S      = 5;       // hardware (task) watchdog: restart if a task hangs

// --- ADC ---
constexpr int     ADC_OVERSAMPLE          = 8;       // readings averaged per sample
// Divider ratio = (R_top + R_bottom) / R_bottom: pin voltage x ratio = sensor (or pack) voltage.
constexpr double  MQ3_DIVIDER             = (10.0 + 20.0) / 20.0;   // 10 k over 20 k: 5 V -> 3.33 V
constexpr double  BATTERY_DIVIDER         = (100.0 + 33.0) / 33.0;  // 100 k over 33 k: 8.4 V -> 2.08 V
constexpr double  BATTERY_CAL             = 1.00;    // trim against a multimeter (bring-up step 3)

// --- light sensor (LDR from 3.3 V, 10 k to GND: brighter = higher voltage) ---
constexpr bool    LDR_FITTED              = true;    // false: sensors.lt is null
constexpr double  LDR_DARK_V              = 0.10;    // pin voltage in darkness   -> lt 0.0
constexpr double  LDR_BRIGHT_V            = 2.80;    // pin voltage in room light -> lt 1.0

// --- ultrasonic (HC-SR04) ---
constexpr int32_t US_TIMEOUT_US           = 25000;   // no echo within this: null (about 4.3 m)
constexpr double  US_CM_PER_US            = 0.01715; // speed of sound 343 m/s, halved (round trip)
constexpr double  US_MIN_CM               = 2.0;     // contract range of sensors.d
constexpr double  US_MAX_CM               = 400.0;

// --- motors (TB6612) ---
constexpr int     MOTOR_PWM_HZ            = 20000;   // above hearing
constexpr int     MOTOR_PWM_BITS          = 10;
constexpr bool    MOTOR_LEFT_INVERT       = false;   // flip if a wheel turns the wrong way (bring-up)
constexpr bool    MOTOR_RIGHT_INVERT      = false;
constexpr double  MOTOR_TRIM_LEFT         = 1.00;    // scale the faster wheel down so it drives straight
constexpr double  MOTOR_TRIM_RIGHT        = 1.00;
constexpr double  MOTOR_MIN_DUTY          = 0.00;    // smallest duty that turns a wheel (stiction), 0 = off

// --- RGB LED and buzzer ---
constexpr int     MOOD_LED_COUNT          = 1;
constexpr uint8_t MOOD_LED_BRIGHTNESS      = 60;      // 0-255
constexpr bool    BUZZER_PASSIVE          = true;    // passive piezo (tone); false: active buzzer (on/off)

// --- buttons ---
constexpr int32_t BUTTON_DEBOUNCE_MS      = 30;
constexpr int32_t PORTAL_HOLD_MS          = 3000;    // both buttons held this long at power-on: setup portal

// --- encoders (optional; not on the wire, console only) ---
constexpr bool    ENCODERS_ENABLED        = false;
constexpr int     ENCODER_PULSES_PER_REV  = 20;

// --- networking ---
constexpr int32_t WIFI_CONNECT_TIMEOUT_MS = 30000;   // no Wi-Fi after this many ms at boot: open the portal
constexpr int32_t WIFI_RETRY_MIN_MS       = 1000;    // reconnect backoff: 1 s, doubling ...
constexpr int32_t WIFI_RETRY_MAX_MS       = 30000;   // ... up to 30 s
constexpr int32_t MQTT_KEEPALIVE_S        = 5;       // contract section 4.2
constexpr int32_t PORTAL_TIMEOUT_MS       = 600000;  // the portal closes (and the robot restarts) after 10 min
constexpr int     EVENT_QUEUE_LEN         = 32;      // events kept while offline (contract: at least 20)
constexpr const char* NTP_SERVER          = "pool.ntp.org";

constexpr const char* FIRMWARE_VERSION    = "0.6.0";
constexpr const char* ROBOT_ID            = "01";

}  // namespace cfg
}  // namespace flybot
