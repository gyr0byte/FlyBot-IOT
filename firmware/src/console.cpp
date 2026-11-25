// Serial console (115200 baud) for bring-up: type "help". Runs in the Arduino loop task, the lowest
// priority, so it never delays the robot.
#include <WiFi.h>

#include <cstdlib>
#include <cstring>

#include "app.h"
#include "config.h"
#include "hw.h"
#include "settings.h"

using namespace flybot;

namespace app {

namespace {

char g_line[96];
size_t g_len = 0;
int64_t g_stream_until = 0, g_next_print = 0;
bool g_raw = false;

void print_opt(const char* name, Opt<double> v, int dec) {
    if (v) Serial.printf(" %s=%.*f", name, dec, *v);
    else Serial.printf(" %s=null", name);
}

void print_sensors() {
    Shared s;
    portENTER_CRITICAL(&g_lock);
    s = g_shared;
    portEXIT_CRITICAL(&g_lock);
    Serial.printf("[%8lld] %-8s", static_cast<long long>(now_ms()), to_str(s.st));
    print_opt("ol", s.ol, 3);
    print_opt("or", s.orr, 3);
    print_opt("d", s.dist, 1);
    print_opt("lt", s.lt, 2);
    Serial.printf(" bat=%.2f", s.bat);
    if (g_raw)
        Serial.printf(" | mq3 L=%.3fV R=%.3fV base L=%.3f R=%.3f raw_d=%s", s.mq3_v[0], s.mq3_v[1], s.baseline[0],
                      s.baseline[1], s.dist_raw ? String(*s.dist_raw, 1).c_str() : "none");
    Serial.printf(" | motors %.2f %.2f\n", s.out.l, s.out.r);
}

void print_status() {
    Shared s;
    portENTER_CRITICAL(&g_lock);
    s = g_shared;
    portEXIT_CRITICAL(&g_lock);
    Settings set;
    settings_load(set);
    Serial.printf("FlyBot %s, robot %s, boot id %lu, reset: %s\n", cfg::FIRMWARE_VERSION, cfg::ROBOT_ID,
                  static_cast<unsigned long>(g_bid), reset_reason());
    Serial.printf("state %s, mode %s, odour %s, battery %.2f V%s, habituation %.2f\n", to_str(s.st), to_str(s.m),
                  s.odour_ready ? "ready" : "warming up", s.bat, s.battery_cut ? " (CUT)" : "", s.hab);
    Serial.printf("Wi-Fi %s (\"%s\", RSSI %d), MQTT %s (%s:%u%s), portal %s\n", s.wifi_up ? "up" : "down", set.ssid,
                  s.wifi_up ? WiFi.RSSI() : 0, s.mqtt_up ? "up" : "down", set.host, set.port, set.tls ? " TLS" : "",
                  s.portal ? "OPEN" : "closed");
    Serial.printf("reflex: %lu steps, longest step %lu us, longest period %lu us, late steps %lu\n",
                  static_cast<unsigned long>(s.loops), static_cast<unsigned long>(s.loop_max_us),
                  static_cast<unsigned long>(s.period_max_us), static_cast<unsigned long>(s.overruns));
    Serial.printf("escapes %lu (last detection->motors %.1f ms), invalid messages %lu, unknown versions %lu, heap %u\n",
                  static_cast<unsigned long>(s.escapes), s.last_lat_ms, static_cast<unsigned long>(s.invalid_in),
                  static_cast<unsigned long>(s.unknown_version), static_cast<unsigned>(esp_get_free_heap_size()));
}

void help() {
    Serial.println(
        "commands:\n"
        "  status             state, connection, reflex timing\n"
        "  s [seconds]        print the sensors twice a second (default 30 s; 's 0' stops)\n"
        "  raw                toggle raw MQ-3 volts and baselines in 's'\n"
        "  m <l> <r> [ms]     drive the motors (-1..1) for ms (default 1000, max 5000); WHEELS OFF THE GROUND\n"
        "  stop               stop a motor test\n"
        "  buzz <name>        boot connected escape wifi low cutoff reward punish error portal\n"
        "  cal                restart the MQ-3 warm-up and baseline (clean air, robot still)\n"
        "  enc                encoder counts (if fitted)\n"
        "  portal             open the Wi-Fi setup portal\n"
        "  forget             forget the portal settings (back to secrets.h) and restart\n"
        "  reboot             publish offline and restart\n"
        "  freeze             stop feeding the watchdog: the robot must restart within 5 s");
}

void command(char* line) {
    char* cmd = strtok(line, " ");
    if (!cmd) return;
    if (!strcmp(cmd, "help") || !strcmp(cmd, "?")) help();
    else if (!strcmp(cmd, "status")) print_status();
    else if (!strcmp(cmd, "s")) {
        char* a = strtok(nullptr, " ");
        long secs = a ? atol(a) : 30;
        g_stream_until = now_ms() + secs * 1000;
    } else if (!strcmp(cmd, "raw")) {
        g_raw = !g_raw;
        Serial.printf("raw %s\n", g_raw ? "on" : "off");
    } else if (!strcmp(cmd, "m")) {
        char* a = strtok(nullptr, " ");
        char* b = strtok(nullptr, " ");
        char* c = strtok(nullptr, " ");
        if (!a || !b) { Serial.println("m <l> <r> [ms]"); return; }
        double l = atof(a), r = atof(b);
        long ms = c ? atol(c) : 1000;
        l = l < -1 ? -1 : l > 1 ? 1 : l;
        r = r < -1 ? -1 : r > 1 ? 1 : r;
        ms = ms < 0 ? 0 : ms > 5000 ? 5000 : ms;
        g_override_l = l;
        g_override_r = r;
        g_override_until = now_ms() + ms;
        Serial.printf("motors %.2f %.2f for %ld ms\n", l, r, ms);
    } else if (!strcmp(cmd, "stop")) {
        g_override_until = 0;
    } else if (!strcmp(cmd, "buzz")) {
        static const char* names[] = {"boot", "connected", "escape", "wifi", "low", "cutoff", "reward", "punish", "error", "portal"};
        static const Tune tunes[] = {Tune::Boot, Tune::Connected, Tune::Escape, Tune::WifiLost, Tune::LowBattery,
                                     Tune::Cutoff, Tune::Reward, Tune::Punish, Tune::Error, Tune::Portal};
        char* a = strtok(nullptr, " ");
        for (int i = 0; a && i < 10; ++i) {
            if (strcmp(a, names[i])) continue;
            Buzzer b;
            b.play(tunes[i], now_ms());
            while (b.busy()) { hw::tone(b.freq(now_ms())); delay(5); }
            hw::tone(0);
            return;
        }
        Serial.println("buzz boot|connected|escape|wifi|low|cutoff|reward|punish|error|portal");
    } else if (!strcmp(cmd, "cal")) {
        g_recalibrate = true;
        Serial.printf("odour warm-up restarted (%ld s)\n", static_cast<long>(cfg::ODOUR_WARMUP_MS / 1000));
    } else if (!strcmp(cmd, "enc")) {
        long l, r;
        hw::encoder_counts(l, r);
        Serial.printf("encoders %s: left %ld right %ld\n", cfg::ENCODERS_ENABLED ? "on" : "off (config.h)", l, r);
    } else if (!strcmp(cmd, "portal")) {
        g_portal_request = true;
    } else if (!strcmp(cmd, "forget")) {
        settings_forget();
        announce_offline();
        delay(300);
        ESP.restart();
    } else if (!strcmp(cmd, "reboot")) {
        announce_offline();
        delay(300);
        ESP.restart();
    } else if (!strcmp(cmd, "freeze")) {
        g_freeze = true;
        Serial.println("watchdog no longer fed: expect a restart (reset reason: watchdog)");
    } else {
        Serial.println("unknown command; type help");
    }
}

}  // namespace

void console_poll() {
    while (Serial.available()) {
        int c = Serial.read();
        if (c == '\r') continue;
        if (c == '\n') {
            g_line[g_len] = '\0';
            command(g_line);
            g_len = 0;
        } else if (g_len < sizeof g_line - 1) {
            g_line[g_len++] = static_cast<char>(c);
        }
    }
    int64_t now = now_ms();
    if (now < g_stream_until && now >= g_next_print) {
        print_sensors();
        g_next_print = now + 500;
    }
}

}  // namespace app
