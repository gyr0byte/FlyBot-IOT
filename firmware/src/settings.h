// Network settings: compiled-in defaults from secrets.h, overridden by values saved from the setup
// portal (NVS flash, namespace "flybot"), so Wi-Fi and the broker change without reflashing.
#pragma once
#include <cstdint>

struct Settings {
    char ssid[33];
    char wifi_pass[65];
    char host[64];
    uint16_t port;
    bool tls;
    char user[32];
    char mqtt_pass[65];
    bool saved;          // true if any value came from the portal (NVS)
    bool placeholders;   // true if secrets.h was missing (example values compiled in)
};

void settings_load(Settings& s);
bool settings_save(const Settings& s);
void settings_forget();   // back to the compiled-in defaults
const char* settings_ca_pem();
const char* settings_portal_password();
