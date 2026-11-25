// The reflex, sensor and UI tasks (plan step 6.5) and the shared state.
#include <esp_random.h>
#include <esp_system.h>
#include <esp_task_wdt.h>
#include <esp_timer.h>

#include "app.h"
#include "config.h"
#include "hw.h"
#include "py_compat.h"
#include "sensing.h"

using namespace flybot;
using namespace flybot::cfg;

namespace app {

portMUX_TYPE g_lock = portMUX_INITIALIZER_UNLOCKED;
Shared g_shared;
QueueHandle_t g_in = nullptr;
QueueHandle_t g_out = nullptr;
volatile bool g_status_request = true;
uint32_t g_bid = 1;
EdgeConfig g_cfg;
RttWindow g_rtt(RTT_WINDOW_MS), g_crtt(RTT_WINDOW_MS);
SentLog g_sent, g_pings;
volatile bool g_freeze = false;
volatile int64_t g_override_until = 0;
volatile double g_override_l = 0, g_override_r = 0;
volatile bool g_recalibrate = false;
volatile bool g_portal_request = false;

int64_t now_ms() { return esp_timer_get_time() / 1000; }

const char* reset_reason() {
    switch (esp_reset_reason()) {
        case ESP_RST_POWERON: return "power_on";
        case ESP_RST_TASK_WDT:
        case ESP_RST_INT_WDT:
        case ESP_RST_WDT: return "watchdog";
        case ESP_RST_BROWNOUT: return "brownout";
        case ESP_RST_SW: return "software";
        case ESP_RST_PANIC: return "panic";
        default: return "other";
    }
}

bool post(const Input& in) { return g_in && xQueueSend(g_in, &in, 0) == pdTRUE; }
bool post(const Outgoing& out) { return g_out && xQueueSend(g_out, &out, 0) == pdTRUE; }

// ============================================================================ reflex task

namespace {

double rand01(void*) { return esp_random() / 4294967296.0; }  // [0, 1)

void apply(Edge& e, const Input& in, int64_t now) {
    switch (in.kind) {
        case Input::Link: e.on_link(in.flag, now, in.why); break;
        case Input::Motor: e.on_motor(in.motor, now); break;
        case Input::Manual: e.on_manual(in.manual, now); break;
        case Input::Mode: e.on_mode(in.mode, now, in.flag); break;
        case Input::Sleep: e.on_sleep(in.sleep, now); break;
        case Input::Invalid: e.on_invalid(in.topic); break;
        case Input::OdourReady: e.set_odour_ready(in.flag); break;
        case Input::Battery: e.on_battery(in.volts, now); break;
    }
}

}  // namespace

void reflex_task(void*) {
    esp_task_wdt_add(nullptr);
    static Edge edge(g_cfg, rand01);
    static DistanceFilter dist(g_cfg);
    TickType_t wake = xTaskGetTickCount();
    int64_t next_trigger = 0, last_echo_end_us = 0, last_start_us = 0;
    uint32_t loop_max = 0, period_max = 0, overruns = 0, loops = 0, escapes = 0;
    double last_lat = 0;
    Opt<double> raw_last, filt_last;

    for (;;) {
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(CONTROL_PERIOD_MS));
        int64_t t0 = esp_timer_get_time();
        if (last_start_us) {
            uint32_t period = static_cast<uint32_t>(t0 - last_start_us);
            if (period > period_max) period_max = period;
            if (period > 2000u * CONTROL_PERIOD_MS) ++overruns;   // a step more than twice late
        }
        last_start_us = t0;
        int64_t now = t0 / 1000;

        Input in;
        while (xQueueReceive(g_in, &in, 0) == pdTRUE) apply(edge, in, now);

        Opt<double> raw;
        int64_t echo_end = 0;
        if (hw::us_poll(raw, echo_end)) {           // the reflex owns the ultrasonic sensor
            Opt<double> filt = dist.feed(raw, now);
            edge.on_distance(raw, filt, now);
            last_echo_end_us = echo_end;
            raw_last = raw;
            filt_last = filt;
        }
        if (now >= next_trigger) {
            hw::us_trigger();
            next_trigger = now + US_PERIOD_MS;
        }

        Wheels w = edge.update(now);
        bool portal = false;
        portENTER_CRITICAL(&g_lock);
        portal = g_shared.portal;
        portEXIT_CRITICAL(&g_lock);
        bool test = now < g_override_until && !edge.battery_cut && !portal;
        bool enable = !portal && !edge.battery_cut &&
                      (test || !(edge.state == State::Boot || edge.state == State::Estop || edge.state == State::Sleep));
        if (test) hw::motors(g_override_l, g_override_r, true);
        else hw::motors(w.l, w.r, enable);
        int64_t motors_us = esp_timer_get_time();

        EdgeEvent ev;
        while (edge.pop_event(ev)) {
            if (ev.type == EventType::Escape && ev.has_lat) {
                // Measured on the robot: from the end of the echo that completed the detection to the
                // motor outputs being set (plan 6.6). The edge's own value is in whole milliseconds.
                ev.lat = py_round((motors_us - last_echo_end_us) / 1000.0, 1);
                last_lat = ev.lat;
                ++escapes;
            }
            Outgoing o{};
            o.kind = Outgoing::Event;
            o.ev = ev;
            o.up = now;
            post(o);
        }
        if (edge.status_dirty) {   // state or mode changed: the comms task publishes status
            edge.status_dirty = false;
            g_status_request = true;
        }

        uint32_t dt = static_cast<uint32_t>(esp_timer_get_time() - t0);
        if (dt > loop_max) loop_max = dt;
        ++loops;
        portENTER_CRITICAL(&g_lock);
        g_shared.st = edge.state;
        g_shared.m = edge.mode;
        g_shared.hab = edge.hab_level(now);
        g_shared.battery_cut = edge.battery_cut;
        g_shared.dist_raw = raw_last;
        g_shared.dist = filt_last;
        g_shared.out = w;
        g_shared.loops = loops;
        g_shared.loop_max_us = loop_max;
        g_shared.period_max_us = period_max;
        g_shared.overruns = overruns;
        g_shared.escapes = escapes;
        g_shared.last_lat_ms = last_lat;
        portEXIT_CRITICAL(&g_lock);

        if (!g_freeze) esp_task_wdt_reset();
    }
}

// ============================================================================ sensor task

void sensor_task(void*) {
    esp_task_wdt_add(nullptr);
    static OdourChannel odour0(g_cfg), odour1(g_cfg);
    OdourChannel* odour[2] = {&odour0, &odour1};
    static BatteryMonitor battery(g_cfg);
    msg::SeqCounter seq;
    bool ready_sent = false;
    TickType_t wake = xTaskGetTickCount();
    for (;;) {
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(SENSORS_PERIOD_MS));
        int64_t now = now_ms();
        if (g_recalibrate) {
            odour0.restart();
            odour1.restart();
            g_recalibrate = false;
            ready_sent = false;
        }
        double v[2] = {hw::mq3_volts(0), hw::mq3_volts(1)};
        Opt<double> ol = odour[0]->feed(v[0], now), orr = odour[1]->feed(v[1], now);
        bool ready = odour0.ready() && odour1.ready();
        if (ready != ready_sent) {
            Input in{};
            in.kind = Input::OdourReady;
            in.flag = ready;
            if (post(in)) ready_sent = ready;
        }
        Opt<double> lt = hw::light();
        double bat = battery.feed(hw::battery_volts());
        Input b{};
        b.kind = Input::Battery;
        b.volts = bat;
        post(b);

        Opt<double> d;
        portENTER_CRITICAL(&g_lock);
        g_shared.ol = ol;
        g_shared.orr = orr;
        g_shared.lt = lt;
        g_shared.bat = bat;
        g_shared.mq3_v[0] = v[0];
        g_shared.mq3_v[1] = v[1];
        g_shared.baseline[0] = odour0.baseline().value_or(0.0);
        g_shared.baseline[1] = odour1.baseline().value_or(0.0);
        g_shared.odour_ready = ready;
        d = g_shared.dist;
        portEXIT_CRITICAL(&g_lock);

        if (mqtt_connected()) {
            char buf[msg::kMaxPayload];
            msg::Header h{seq.peek(msg::Out::Sensors), now, g_bid};
            size_t n = msg::build_sensors(buf, sizeof buf, h, ol, orr, d, lt, bat);
            if (publish_now("sensors", buf, n, 0, false)) {
                portENTER_CRITICAL(&g_lock);
                g_sent.sent(h.seq, now);
                portEXIT_CRITICAL(&g_lock);
                seq.next(msg::Out::Sensors);
            }
        }
        esp_task_wdt_reset();
    }
}

// ============================================================================ UI task

void ui_task(void*) {
    esp_task_wdt_add(nullptr);
    Debouncer reward(BUTTON_DEBOUNCE_MS), punish(BUTTON_DEBOUNCE_MS);
    Buzzer buzzer;
    buzzer.play(Tune::Boot, now_ms());
    State last_st = State::Boot;
    bool last_mqtt = false, last_low = false, last_cut = false, last_portal = false;
    int64_t next_low_beep = 0, chord_since = 0;
    bool chord = false;
    for (;;) {
        int64_t now = now_ms();
        Shared s;
        portENTER_CRITICAL(&g_lock);
        s = g_shared;
        portEXIT_CRITICAL(&g_lock);
        bool low = s.bat > 0.5 && s.bat < g_cfg.battery_low_v;   // 0 = not measured yet

        // sounds: escape, Wi-Fi/MQTT lost and back, low battery (repeated every 30 s), cut-off, portal
        if (s.st == State::Escape && last_st != State::Escape) buzzer.play(Tune::Escape, now);
        if (s.mqtt_up != last_mqtt && !s.portal) buzzer.play(s.mqtt_up ? Tune::Connected : Tune::WifiLost, now);
        if (s.battery_cut && !last_cut) buzzer.play(Tune::Cutoff, now);
        else if (low && !s.battery_cut && (!last_low || now >= next_low_beep)) {
            buzzer.play(Tune::LowBattery, now);
            next_low_beep = now + 30000;
        }
        if (s.portal && !last_portal) buzzer.play(Tune::Portal, now);
        last_st = s.st;
        last_mqtt = s.mqtt_up;
        last_low = low;
        last_cut = s.battery_cut;
        last_portal = s.portal;

        // buttons -> reward topic (src "btn"), sent on release. Both buttons together are a chord, not a
        // reward: held for 3 s within the first minute after boot it opens the setup portal. (The buttons
        // are on GPIO 14 / 15, which must not be held low while the chip boots, so no power-on gesture.)
        bool was_good = reward.pressed(), was_bad = punish.pressed();
        reward.update(hw::reward_pressed(), now);
        punish.update(hw::punish_pressed(), now);
        bool both = reward.pressed() && punish.pressed();
        if (both) {
            chord = true;
            if (!chord_since) chord_since = now;
            if (now - chord_since >= PORTAL_HOLD_MS && now < PORTAL_HOLD_WINDOW_MS && !s.portal) g_portal_request = true;
        } else {
            chord_since = 0;
        }
        bool released_good = was_good && !reward.pressed(), released_bad = was_bad && !punish.pressed();
        if ((released_good || released_bad) && !chord) {
            Outgoing o{};
            o.kind = Outgoing::Reward;
            o.r = released_good ? 1 : -1;
            o.up = now;
            if (s.mqtt_up && post(o)) buzzer.play(released_good ? Tune::Reward : Tune::Punish, now);
            else buzzer.play(Tune::Error, now);
        }
        if (!reward.pressed() && !punish.pressed()) chord = false;

        hw::led(mood_color(MoodInput{s.st, s.m, s.mqtt_up, low, s.battery_cut, s.portal}, now));
        // on-board LED: on = MQTT connected, slow blink = Wi-Fi only, fast blink = no Wi-Fi
        bool on = s.mqtt_up ? true : s.wifi_up ? (now % 1000) < 500 : (now % 250) < 125;
        hw::status_led(s.portal ? (now % 1000) < 100 || ((now % 1000) > 200 && (now % 1000) < 300) : on);
        hw::tone(buzzer.freq(now));
        esp_task_wdt_reset();
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

// ============================================================================ start

void start_tasks() {
    g_in = xQueueCreate(64, sizeof(Input));
    g_out = xQueueCreate(32, sizeof(Outgoing));
    // core 1: the robot; core 0: the radio (Wi-Fi, lwIP and esp-mqtt run there too)
    xTaskCreatePinnedToCore(reflex_task, "reflex", 6144, nullptr, configMAX_PRIORITIES - 2, nullptr, 1);
    xTaskCreatePinnedToCore(sensor_task, "sensor", 6144, nullptr, 10, nullptr, 1);
    xTaskCreatePinnedToCore(ui_task, "ui", 4096, nullptr, 4, nullptr, 1);
    xTaskCreatePinnedToCore(comms_task, "comms", 8192, nullptr, 6, nullptr, 0);
}

}  // namespace app
