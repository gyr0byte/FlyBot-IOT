// Copy this file to secrets.h (git-ignored) and fill in your values. Never commit secrets.h.
//
// These are only the defaults compiled into the firmware. The Wi-Fi network and the broker
// address can also be changed without reflashing in the setup portal (docs/hardware-bringup.md):
// values saved there (in flash, NVS) take priority over the ones below.
#pragma once

// Wi-Fi
#define SECRET_WIFI_SSID      "your-wifi-name"
#define SECRET_WIFI_PASSWORD  "your-wifi-password"

// MQTT broker (the laptop's IP address on your network during development)
#define SECRET_MQTT_HOST      "192.168.1.10"
#define SECRET_MQTT_PORT      1883
#define SECRET_MQTT_TLS       0          // 1 = mqtts on port 8883 with the CA certificate below (Phase 11)
#define SECRET_MQTT_USER      "esp32"    // contract section 4: the robot connects as user esp32
#define SECRET_MQTT_PASSWORD  "change-me" // MQTT_ESP32_PASS from the repository's .env

// Setup portal: the robot's own Wi-Fi hotspot "FlyBot-Setup-xxxx" (at least 8 characters)
#define SECRET_PORTAL_PASSWORD "flybot-setup"

// Root CA certificate (PEM) of the broker, used only when TLS is on. Empty = TLS off.
#define SECRET_MQTT_CA_PEM ""
