// FlyBot firmware entry point (skeleton; filled in by the later Phase 6 steps).
#include <Arduino.h>

#include "config.h"

void setup() {
    Serial.begin(115200);
    Serial.printf("FlyBot firmware %s\n", flybot::cfg::FIRMWARE_VERSION);
}

void loop() {
    delay(1000);
}
