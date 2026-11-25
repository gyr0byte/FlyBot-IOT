#include "settings.h"

#include <Preferences.h>

#include <cstring>

#if __has_include("secrets.h")
#include "secrets.h"
#define FLYBOT_PLACEHOLDERS 0
#else
// No secrets.h yet: build with the template's placeholders; the robot then opens the setup portal
// (it cannot join "your-wifi-name") and the serial console says so.
#include "secrets.example.h"
#define FLYBOT_PLACEHOLDERS 1
#endif

namespace {
void copy(char* dst, size_t cap, const char* src) {
    std::strncpy(dst, src ? src : "", cap - 1);
    dst[cap - 1] = '\0';
}
}  // namespace

void settings_load(Settings& s) {
    copy(s.ssid, sizeof s.ssid, SECRET_WIFI_SSID);
    copy(s.wifi_pass, sizeof s.wifi_pass, SECRET_WIFI_PASSWORD);
    copy(s.host, sizeof s.host, SECRET_MQTT_HOST);
    s.port = SECRET_MQTT_PORT;
    s.tls = SECRET_MQTT_TLS != 0;
    copy(s.user, sizeof s.user, SECRET_MQTT_USER);
    copy(s.mqtt_pass, sizeof s.mqtt_pass, SECRET_MQTT_PASSWORD);
    s.saved = false;
    s.placeholders = FLYBOT_PLACEHOLDERS;
    Preferences p;
    if (!p.begin("flybot", true)) return;
    if (p.isKey("ssid")) {
        p.getString("ssid", s.ssid, sizeof s.ssid);
        p.getString("wpass", s.wifi_pass, sizeof s.wifi_pass);
        s.saved = true;
    }
    if (p.isKey("host")) {
        p.getString("host", s.host, sizeof s.host);
        s.port = p.getUShort("port", s.port);
        s.tls = p.getBool("tls", s.tls);
        s.saved = true;
    }
    if (p.isKey("mpass")) p.getString("mpass", s.mqtt_pass, sizeof s.mqtt_pass);
    p.end();
}

bool settings_save(const Settings& s) {
    Preferences p;
    if (!p.begin("flybot", false)) return false;
    bool ok = p.putString("ssid", s.ssid) > 0 || s.ssid[0] == '\0';
    p.putString("wpass", s.wifi_pass);
    ok = ok && p.putString("host", s.host) > 0;
    p.putUShort("port", s.port);
    p.putBool("tls", s.tls);
    if (s.mqtt_pass[0]) p.putString("mpass", s.mqtt_pass);
    p.end();
    return ok;
}

void settings_forget() {
    Preferences p;
    if (p.begin("flybot", false)) {
        p.clear();
        p.end();
    }
}

const char* settings_ca_pem() { return SECRET_MQTT_CA_PEM; }
const char* settings_portal_password() { return SECRET_PORTAL_PASSWORD; }
