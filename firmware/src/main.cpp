// FlyBot firmware (plan Phase 6). The behaviour is in lib/flybot_edge (the simulator's edge logic,
// ported and cross-checked); this file starts the hardware, the watchdog and the tasks (app.h).
#include <Arduino.h>
#include <WiFi.h>
#include <esp_random.h>
#include <esp_task_wdt.h>

#include "app.h"
#include "config.h"
#include "hw.h"

using namespace flybot::cfg;

void setup() {
    Serial.begin(115200);
    hw::begin();  // motors in standby first
    Serial.printf("\nFlyBot firmware %s, robot %s, reset: %s\n", FIRMWARE_VERSION, ROBOT_ID, app::reset_reason());

    // Hardware watchdog: any subscribed task that stops for 5 s restarts the robot.
    esp_task_wdt_init(TASK_WDT_TIMEOUT_S, true);

    // Boot ID (contract section 2): drawn after the radio is on, when esp_random() is truly random.
    WiFi.setHostname("flybot-01");
    WiFi.mode(WIFI_STA);
    do {
        app::g_bid = esp_random();
    } while (app::g_bid == 0);
    Serial.printf("boot id %lu\n", static_cast<unsigned long>(app::g_bid));

    // Both buttons held for 3 s at power-on: open the setup portal.
    int64_t t0 = app::now_ms();
    while (hw::reward_pressed() && hw::punish_pressed() && app::now_ms() - t0 < PORTAL_HOLD_MS) delay(10);
    if (app::now_ms() - t0 >= PORTAL_HOLD_MS) app::g_portal_request = true;

    app::start_tasks();
    Serial.println("type help for console commands");
}

void loop() {
    app::console_poll();
    delay(10);
}
