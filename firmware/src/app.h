// Shared state between the FreeRTOS tasks (plan step 6.5):
//   reflex task  core 1, highest priority, 5 ms: owns the Edge, the ultrasonic sensor and the motors
//   sensor task  core 1, 50 ms (20 Hz): odour, light, battery; publishes sensors
//   ui task      core 1, 20 ms: buttons, RGB LED, buzzer, on-board LED
//   comms task   core 0, 20 ms: Wi-Fi, MQTT, setup portal, status, events, ping
//   console      Arduino loop(): serial commands for bring-up
// The reflex task never waits on the network: inputs reach it through a queue it drains without
// blocking, and everything it publishes leaves through another queue.
#pragma once
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include "edge.h"
#include "io_util.h"
#include "messages.h"

namespace app {

// Inputs for the reflex task.
struct Input {
    enum Kind : uint8_t { Link, Motor, Manual, Mode, Sleep, Invalid, OdourReady, Battery };
    Kind kind;
    bool flag;                 // Link: up; Mode: retained; OdourReady: ready
    flybot::FallbackWhy why;   // Link down: wifi_lost or mqtt_lost
    flybot::Topic topic;       // Invalid
    double volts;              // Battery (smoothed pack voltage)
    union {
        flybot::CmdMotor motor;
        flybot::CmdManual manual;
        flybot::CmdMode mode;
        flybot::CmdSleep sleep;
    };
};

// Things to publish, for the comms task.
struct Outgoing {
    enum Kind : uint8_t { Event, Reward };
    Kind kind;
    flybot::EdgeEvent ev;
    int64_t up;   // robot time the event happened
    int r;        // Reward: +1 / -1
};

// A snapshot of everything the other tasks show or publish (guarded by g_lock).
struct Shared {
    flybot::State st = flybot::State::Boot;
    flybot::Mode m = flybot::Mode::Auto;
    double hab = 0.0;
    bool battery_cut = false;
    flybot::Opt<double> dist_raw, dist;   // last raw and filtered ultrasonic distance (cm)
    flybot::Opt<double> ol, orr, lt;      // sensors message values
    double bat = 0.0;                     // smoothed pack voltage
    double mq3_v[2] = {0, 0};             // MQ-3 output volts (console)
    double baseline[2] = {0, 0};          // MQ-3 baselines (console)
    bool odour_ready = false;
    bool wifi_up = false, mqtt_up = false, portal = false;
    flybot::Wheels out{0, 0};             // motor outputs
    uint32_t loops = 0, overruns = 0;     // reflex task timing
    uint32_t loop_max_us = 0, period_max_us = 0;
    uint32_t invalid_in = 0, unknown_version = 0;
    uint32_t escapes = 0;
    double last_lat_ms = 0;               // last escape: echo -> motors, measured
};

extern portMUX_TYPE g_lock;
extern Shared g_shared;
extern QueueHandle_t g_in;     // Input, depth 64
extern QueueHandle_t g_out;    // Outgoing, depth 32
extern volatile bool g_status_request;
extern uint32_t g_bid;         // boot ID, drawn after the radio is on (esp_random is then truly random)
extern flybot::EdgeConfig g_cfg;
extern flybot::RttWindow g_rtt, g_crtt;   // guarded by g_lock
extern flybot::SentLog g_sent, g_pings;   // guarded by g_lock

// Console test hooks.
extern volatile bool g_freeze;                  // stop feeding the watchdog (restart test)
extern volatile int64_t g_override_until;      // motor test: drive these outputs until then
extern volatile double g_override_l, g_override_r;
extern volatile bool g_recalibrate;             // restart the odour warm-up and baseline
extern volatile bool g_portal_request;          // open the setup portal

int64_t now_ms();
const char* reset_reason();
bool post(const Input& in);            // to the reflex task, never blocks
bool post(const Outgoing& out);        // to the comms task, never blocks
void start_tasks();

void reflex_task(void*);
void sensor_task(void*);
void ui_task(void*);
void comms_task(void*);                // net.cpp
void console_poll();                   // console.cpp, from loop()
bool publish_now(const char* suffix, const char* payload, size_t len, int qos, bool retain);  // net.cpp
bool mqtt_connected();
void announce_offline();               // publish the offline status before a clean restart

}  // namespace app
