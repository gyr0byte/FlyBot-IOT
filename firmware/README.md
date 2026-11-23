# firmware/

ESP32 firmware for the robot body — PlatformIO project, Arduino framework (not yet created).

Will contain:

- Sensor reading: two MQ-3 odour sensors (reported as change from baseline, 0–1),
  HC-SR04 ultrasonic distance, optional LDR, battery voltage.
- Motor mixer for the TB6612/L298N (−1.0 to +1.0 per wheel, duty cap, ramping, dead zone).
- Edge reflexes: looming escape, obstacle stop, Wi-Fi-loss fallback wandering.
- Safety state machine (boot, cloud, escape, fallback, sleep, estop) and command watchdog.
- MQTT via ESP-IDF `esp-mqtt` (QoS 1, last-will, TLS) following
  [`docs/message-contract.md`](../docs/message-contract.md).

Planned layout: `platformio.ini`, `include/config.h` (all pins and thresholds),
`include/secrets.example.h` (committed) and `include/secrets.h` (git-ignored: Wi-Fi and MQTT
credentials), `src/`.

Keep PlatformIO on D: — set `PLATFORMIO_CORE_DIR` to a D: folder before installing (see
[`CLAUDE.md`](../CLAUDE.md)).
