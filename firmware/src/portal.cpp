#include "portal.h"

#include <DNSServer.h>
#include <WebServer.h>
#include <WiFi.h>
#include <esp_task_wdt.h>

#include "app.h"
#include "config.h"
#include "settings.h"

namespace portal {

namespace {

bool g_active = false;
int64_t g_started = 0;
DNSServer g_dns;
WebServer g_web(80);
String g_networks;   // <option> list from the scan at start
int64_t g_restart_at = 0;

String esc(const char* s) {
    String o;
    for (; *s; ++s) {
        switch (*s) {
            case '&': o += "&amp;"; break;
            case '<': o += "&lt;"; break;
            case '>': o += "&gt;"; break;
            case '"': o += "&quot;"; break;
            case '\'': o += "&#39;"; break;
            default: o += *s;
        }
    }
    return o;
}

const char kHead[] PROGMEM =
    "<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>FlyBot setup</title><style>body{font-family:system-ui,sans-serif;max-width:28rem;margin:1rem auto;"
    "padding:0 1rem;background:#111;color:#eee}label{display:block;margin:.8rem 0 .2rem}input,select{width:100%;"
    "padding:.5rem;font-size:1rem;box-sizing:border-box}button{margin-top:1.2rem;padding:.7rem;width:100%;"
    "font-size:1rem}small{color:#aaa}</style></head><body>";

void page_root() {
    Settings s;
    settings_load(s);
    String h = FPSTR(kHead);
    h += "<h1>FlyBot setup</h1><p><small>Robot ";
    h += flybot::cfg::ROBOT_ID;
    h += ", firmware ";
    h += flybot::cfg::FIRMWARE_VERSION;
    h += "</small></p><form method=post action=/save>";
    h += "<label>Wi-Fi network</label><select name=ssid_pick><option value=''>(type below)</option>";
    h += g_networks;
    h += "</select><input name=ssid placeholder='or type the name' value='";
    h += esc(s.ssid);
    h += "'><label>Wi-Fi password</label><input name=wpass type=password placeholder='unchanged if empty'>";
    h += "<label>Broker address</label><input name=host value='";
    h += esc(s.host);
    h += "'><label>Broker port</label><input name=port type=number min=1 max=65535 value='";
    h += String(s.port);
    h += "'><label><input name=tls type=checkbox style='width:auto'";
    h += s.tls ? " checked" : "";
    h += "> TLS (mqtts, needs the CA certificate in secrets.h)</label>";
    h += "<label>MQTT password for user ";
    h += esc(s.user);
    h += "</label><input name=mpass type=password placeholder='unchanged if empty'>";
    h += "<button>Save and restart</button></form>";
    h += "<form method=post action=/forget><button>Forget saved settings (use the built-in ones)</button></form>";
    h += "</body></html>";
    g_web.send(200, "text/html", h);
}

void page_save() {
    Settings s;
    settings_load(s);
    String ssid = g_web.arg("ssid_pick").length() ? g_web.arg("ssid_pick") : g_web.arg("ssid");
    String host = g_web.arg("host");
    long port = g_web.arg("port").toInt();
    if (ssid.length() < 1 || ssid.length() > 32 || host.length() < 1 || host.length() > 63 || port < 1 || port > 65535 ||
        g_web.arg("wpass").length() > 64 || g_web.arg("mpass").length() > 64) {
        g_web.send(400, "text/html", String(FPSTR(kHead)) + "<p>Please check the values.</p><a href=/>Back</a>");
        return;
    }
    strncpy(s.ssid, ssid.c_str(), sizeof s.ssid - 1);
    s.ssid[sizeof s.ssid - 1] = '\0';
    if (g_web.arg("wpass").length()) {
        strncpy(s.wifi_pass, g_web.arg("wpass").c_str(), sizeof s.wifi_pass - 1);
        s.wifi_pass[sizeof s.wifi_pass - 1] = '\0';
    }
    strncpy(s.host, host.c_str(), sizeof s.host - 1);
    s.host[sizeof s.host - 1] = '\0';
    s.port = static_cast<uint16_t>(port);
    s.tls = g_web.hasArg("tls");
    s.mqtt_pass[0] = '\0';  // empty = keep the stored / built-in one
    if (g_web.arg("mpass").length()) {
        strncpy(s.mqtt_pass, g_web.arg("mpass").c_str(), sizeof s.mqtt_pass - 1);
        s.mqtt_pass[sizeof s.mqtt_pass - 1] = '\0';
    }
    bool ok = settings_save(s);
    g_web.send(200, "text/html",
               String(FPSTR(kHead)) + (ok ? "<p>Saved. The robot restarts and joins <b>" + esc(s.ssid) + "</b>.</p>"
                                          : "<p>Saving failed.</p>"));
    g_restart_at = app::now_ms() + 1500;
}

void page_forget() {
    settings_forget();
    g_web.send(200, "text/html", String(FPSTR(kHead)) + "<p>Forgotten. Restarting with the built-in settings.</p>");
    g_restart_at = app::now_ms() + 1500;
}

}  // namespace

void start() {
    if (g_active) return;
    g_active = true;
    g_started = app::now_ms();
    WiFi.disconnect(true);
    WiFi.mode(WIFI_AP_STA);
    esp_task_wdt_reset();
    int n = WiFi.scanNetworks();  // about 2 s (the watchdog allows 5)
    esp_task_wdt_reset();
    g_networks = "";
    for (int i = 0; i < n && i < 20; ++i) {
        String name = WiFi.SSID(i);
        if (!name.length()) continue;
        g_networks += "<option>" + esc(name.c_str()) + "</option>";
    }
    WiFi.scanDelete();
    char ap[32];
    snprintf(ap, sizeof ap, "FlyBot-Setup-%04X", static_cast<unsigned>(app::g_bid & 0xFFFF));
    WiFi.softAP(ap, settings_portal_password());
    g_dns.start(53, "*", WiFi.softAPIP());
    g_web.on("/", HTTP_GET, page_root);
    g_web.on("/save", HTTP_POST, page_save);
    g_web.on("/forget", HTTP_POST, page_forget);
    g_web.onNotFound([] {  // captive portal: every page leads to the form
        g_web.sendHeader("Location", String("http://") + WiFi.softAPIP().toString() + "/", true);
        g_web.send(302, "text/plain", "");
    });
    g_web.begin();
    Serial.printf("[portal] open: join Wi-Fi \"%s\" (password in secrets.h), then open http://%s/\n", ap,
                  WiFi.softAPIP().toString().c_str());
}

bool active() { return g_active; }

void loop() {
    if (!g_active) return;
    g_dns.processNextRequest();
    g_web.handleClient();
    int64_t now = app::now_ms();
    if ((g_restart_at && now >= g_restart_at) || now - g_started > flybot::cfg::PORTAL_TIMEOUT_MS) {
        Serial.println("[portal] restarting");
        delay(100);
        ESP.restart();
    }
}

}  // namespace portal
