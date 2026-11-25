// FlyBot firmware entry point (skeleton; filled in by the later Phase 6 steps).
#include <Arduino.h>

#include "config.h"
#include "edge.h"
#include "sensing.h"

static flybot::EdgeConfig g_cfg;
static double rand01(void*) { return esp_random() / 4294967296.0; }
static flybot::Edge g_edge(g_cfg, rand01);
static flybot::OdourChannel g_odour(g_cfg);

void setup() {
    Serial.begin(115200);
    Serial.printf("FlyBot firmware %s\n", flybot::cfg::FIRMWARE_VERSION);
}

void loop() {
    flybot::Wheels w = g_edge.update(millis());
    Serial.printf("%s %.2f %.2f\n", flybot::to_str(g_edge.state), w.l, w.r);
    delay(1000);
}
