// Comms task (core 0): Wi-Fi with reconnect and backoff, the setup portal, NTP, MQTT through ESP-IDF
// esp-mqtt (QoS 1, last will, TLS-ready), and everything the robot publishes except sensors:
// status (on change and every 10 s), events (queued while offline), rewards, ping (1 Hz).
// Incoming commands are parsed and validated in the MQTT task and posted to the reflex task.
#include <WiFi.h>
#include <esp_task_wdt.h>
#include <mqtt_client.h>

#include <cstring>
#include <ctime>

#include "app.h"
#include "config.h"
#include "portal.h"
#include "settings.h"

using namespace flybot;
using namespace flybot::cfg;

namespace app {

namespace {

Settings g_set;
esp_mqtt_client_handle_t g_client = nullptr;
esp_mqtt_client_config_t g_mcfg = {};
char g_uri[96];
char g_client_id[32];
char g_lwt_topic[48];
char g_lwt[96];
char g_prefix[16];          // "flybot/01/"
volatile bool g_mqtt_up = false;
bool g_mqtt_started = false;
bool g_boot_event_sent = false;
msg::SeqCounter g_seq;      // events, status, reward, ping (sensors has its own, in the sensor task)

// Events (and rewards) kept until they are published: at least 20 while offline (contract 3.2).
char g_ring[EVENT_QUEUE_LEN][msg::kMaxPayload];
uint8_t g_ring_topic[EVENT_QUEUE_LEN];
int g_ring_head = 0, g_ring_n = 0;

void topic(char* out, size_t cap, const char* suffix) { snprintf(out, cap, "%s%s", g_prefix, suffix); }

bool publish(const char* suffix, const char* payload, size_t len, int qos, bool retain) {
    if (!g_client || !g_mqtt_up || len == 0) return false;
    char t[48];
    topic(t, sizeof t, suffix);
    // enqueue: never blocks on the network; the esp-mqtt task sends it.
    return esp_mqtt_client_enqueue(g_client, t, payload, static_cast<int>(len), qos, retain ? 1 : 0, true) >= 0;
}

void ring_push(msg::Out t, const char* payload) {
    if (g_ring_n == EVENT_QUEUE_LEN) {  // full: the oldest goes (receivers see a seq gap)
        g_ring_head = (g_ring_head + 1) % EVENT_QUEUE_LEN;
        --g_ring_n;
    }
    int i = (g_ring_head + g_ring_n) % EVENT_QUEUE_LEN;
    strncpy(g_ring[i], payload, msg::kMaxPayload - 1);
    g_ring[i][msg::kMaxPayload - 1] = '\0';
    g_ring_topic[i] = static_cast<uint8_t>(t);
    ++g_ring_n;
}

void ring_flush() {
    while (g_ring_n && g_mqtt_up) {
        int i = g_ring_head;
        if (!publish(msg::topic_suffix(static_cast<msg::Out>(g_ring_topic[i])), g_ring[i], strlen(g_ring[i]), 1, false))
            return;
        g_ring_head = (g_ring_head + 1) % EVENT_QUEUE_LEN;
        --g_ring_n;
    }
}

void refresh_lwt() {
    // The last will is fixed at connect time: seq 0, up = uptime at connect, this boot's bid (3.3).
    msg::Header h{0, now_ms(), g_bid};
    size_t n = msg::build_offline(g_lwt, sizeof g_lwt, h);
    g_mcfg.lwt_msg = g_lwt;
    g_mcfg.lwt_msg_len = static_cast<int>(n);
}

void post_link(bool up, FallbackWhy why) {
    Input in{};
    in.kind = Input::Link;
    in.flag = up;
    in.why = why;
    post(in);
}

void handle_data(esp_mqtt_event_handle_t e) {
    if (e->current_data_offset != 0 || e->data_len != e->total_data_len) return;  // fragments: never this big
    size_t plen = strlen(g_prefix);
    if (e->topic_len <= static_cast<int>(plen) || strncmp(e->topic, g_prefix, plen) != 0) return;
    char suffix[24];
    size_t sl = static_cast<size_t>(e->topic_len) - plen;
    if (sl >= sizeof suffix) return;
    memcpy(suffix, e->topic + plen, sl);
    suffix[sl] = '\0';
    msg::In which;
    if (!msg::in_topic_from_suffix(suffix, which)) return;
    int64_t now = now_ms();
    if (e->data_len == 0) return;  // an empty retained message clears a topic: not a command
    Input in{};
    msg::Parse p = msg::Parse::Invalid;
    switch (which) {
        case msg::In::CmdMotor:
            p = msg::parse_motor(e->data, e->data_len, in.motor);
            if (p == msg::Parse::Ok) {
                in.kind = Input::Motor;
                portENTER_CRITICAL(&g_lock);
                Opt<int64_t> sent = g_sent.answered(in.motor.rs);   // sensor -> command round trip
                if (sent) g_crtt.add(now, static_cast<double>(now - *sent));
                portEXIT_CRITICAL(&g_lock);
            }
            break;
        case msg::In::CmdManual:
            p = msg::parse_manual(e->data, e->data_len, in.manual);
            in.kind = Input::Manual;
            break;
        case msg::In::CmdMode:
            p = msg::parse_mode(e->data, e->data_len, in.mode);
            in.kind = Input::Mode;
            in.flag = e->retain;  // rule 10: retained = current state
            break;
        case msg::In::CmdSleep:
            p = msg::parse_sleep(e->data, e->data_len, in.sleep);
            in.kind = Input::Sleep;
            break;
        case msg::In::Pong: {
            msg::Pong pong;
            p = msg::parse_pong(e->data, e->data_len, pong);
            if (p == msg::Parse::Ok) {
                portENTER_CRITICAL(&g_lock);
                if (g_pings.answered(pong.ps)) g_rtt.add(now, static_cast<double>(now - pong.pu));  // contract 3.15
                portEXIT_CRITICAL(&g_lock);
                return;
            }
            break;
        }
        default: return;
    }
    if (p != msg::Parse::Ok) {
        portENTER_CRITICAL(&g_lock);
        ++(p == msg::Parse::UnknownVersion ? g_shared.unknown_version : g_shared.invalid_in);
        portEXIT_CRITICAL(&g_lock);
        Input bad{};
        bad.kind = Input::Invalid;
        bad.topic = which == msg::In::CmdMotor ? Topic::CmdMotor : Topic::Other;
        post(bad);
        return;
    }
    post(in);
}

void mqtt_event(void*, esp_event_base_t, int32_t id, void* data) {
    auto* e = static_cast<esp_mqtt_event_handle_t>(data);
    char t[48];
    switch (static_cast<esp_mqtt_event_id_t>(id)) {
        case MQTT_EVENT_BEFORE_CONNECT:
            refresh_lwt();
            esp_mqtt_set_config(g_client, &g_mcfg);  // the new last will is used for this connection
            break;
        case MQTT_EVENT_CONNECTED:
            for (int i = 0; i < static_cast<int>(msg::In::kCount); ++i) {
                msg::In which = static_cast<msg::In>(i);
                topic(t, sizeof t, msg::topic_suffix(which));
                bool q1 = which == msg::In::CmdMode || which == msg::In::CmdSleep;
                esp_mqtt_client_subscribe(g_client, t, q1 ? 1 : 0);
            }
            g_mqtt_up = true;
            post_link(true, FallbackWhy::MqttLost);
            g_status_request = true;
            Serial.println("[mqtt] connected");
            break;
        case MQTT_EVENT_DISCONNECTED:
            if (g_mqtt_up) {
                g_mqtt_up = false;
                post_link(false, WiFi.status() == WL_CONNECTED ? FallbackWhy::MqttLost : FallbackWhy::WifiLost);
                Serial.println("[mqtt] disconnected");
            }
            break;
        case MQTT_EVENT_DATA:
            handle_data(e);
            break;
        case MQTT_EVENT_ERROR:
            if (e->error_handle && e->error_handle->error_type == MQTT_ERROR_TYPE_CONNECTION_REFUSED)
                Serial.printf("[mqtt] broker refused the connection (code %d): user/password?\n",
                              e->error_handle->connect_return_code);
            break;
        default:
            break;
    }
}

bool mqtt_start() {
    const char* ca = settings_ca_pem();
    if (g_set.tls && (!ca || !ca[0])) {
        Serial.println("[mqtt] TLS is on but SECRET_MQTT_CA_PEM is empty: not connecting");
        return false;
    }
    if (g_set.tls && time(nullptr) < 1700000000) return false;  // certificate dates need the clock (NTP)
    snprintf(g_uri, sizeof g_uri, "%s://%s:%u", g_set.tls ? "mqtts" : "mqtt", g_set.host, g_set.port);
    snprintf(g_client_id, sizeof g_client_id, "flybot-%s-esp32", ROBOT_ID);
    topic(g_lwt_topic, sizeof g_lwt_topic, "status");
    g_mcfg = {};
    g_mcfg.uri = g_uri;
    g_mcfg.client_id = g_client_id;
    g_mcfg.username = g_set.user;
    g_mcfg.password = g_set.mqtt_pass;
    g_mcfg.keepalive = MQTT_KEEPALIVE_S;   // contract 4.2: 5 s, last will after about 8 s
    g_mcfg.disable_clean_session = false;
    g_mcfg.lwt_topic = g_lwt_topic;
    g_mcfg.lwt_qos = 1;
    g_mcfg.lwt_retain = 1;
    g_mcfg.reconnect_timeout_ms = 2000;
    g_mcfg.network_timeout_ms = 5000;
    g_mcfg.task_prio = 5;
    g_mcfg.buffer_size = 1024;
    if (g_set.tls) g_mcfg.cert_pem = ca;
    refresh_lwt();
    g_client = esp_mqtt_client_init(&g_mcfg);
    if (!g_client) return false;
    esp_mqtt_client_register_event(g_client, MQTT_EVENT_ANY, mqtt_event, nullptr);
    if (esp_mqtt_client_start(g_client) != ESP_OK) return false;
    Serial.printf("[mqtt] connecting to %s as %s\n", g_uri, g_set.user);
    return true;
}

void publish_status(int64_t now) {
    Shared s;
    Opt<double> rtt, crtt;
    portENTER_CRITICAL(&g_lock);
    s = g_shared;
    rtt = g_rtt.median(now);
    crtt = g_crtt.median(now);
    portEXIT_CRITICAL(&g_lock);
    msg::StatusInfo info{s.st, s.m, WiFi.RSSI(), esp_get_free_heap_size(), s.bat, rtt, crtt, FIRMWARE_VERSION, s.hab};
    char buf[msg::kMaxPayload];
    msg::Header h{g_seq.peek(msg::Out::Status), now, g_bid};
    size_t n = msg::build_status(buf, sizeof buf, h, info);
    if (publish("status", buf, n, 1, true)) g_seq.next(msg::Out::Status);
}

void publish_ping(int64_t now) {
    char buf[msg::kMaxPayload];
    msg::Header h{g_seq.peek(msg::Out::Ping), now, g_bid};
    size_t n = msg::build_ping(buf, sizeof buf, h);
    if (publish("ping", buf, n, 0, false)) {
        portENTER_CRITICAL(&g_lock);
        g_pings.sent(h.seq, now);
        portEXIT_CRITICAL(&g_lock);
        g_seq.next(msg::Out::Ping);
    }
}

void queue_outgoing(const Outgoing& o) {
    char buf[msg::kMaxPayload];
    if (o.kind == Outgoing::Reward) {
        if (!g_mqtt_up) return;  // a reward pressed while offline means nothing later: dropped (the UI beeps)
        msg::Header h{g_seq.next(msg::Out::Reward), o.up, g_bid};
        if (msg::build_reward(buf, sizeof buf, h, o.r)) ring_push(msg::Out::Reward, buf);
        return;
    }
    msg::Header h{g_seq.next(msg::Out::Events), o.up, g_bid};  // seq and up fixed when it happened
    if (msg::build_event(buf, sizeof buf, h, o.ev)) ring_push(msg::Out::Events, buf);
}

}  // namespace

void comms_task(void*) {
    esp_task_wdt_add(nullptr);
    settings_load(g_set);
    snprintf(g_prefix, sizeof g_prefix, "flybot/%s/", ROBOT_ID);
    if (g_set.placeholders)
        Serial.println("[wifi] include/secrets.h is missing: placeholder settings, the setup portal will open");
    WiFi.persistent(false);
    WiFi.setAutoReconnect(false);  // reconnects are paced here (backoff)
    WiFi.begin(g_set.ssid, g_set.wifi_pass);
    Serial.printf("[wifi] joining \"%s\"\n", g_set.ssid);

    int64_t boot = now_ms(), wifi_retry_at = 0, wifi_backoff = WIFI_RETRY_MIN_MS;
    int64_t next_ping = 0, next_status = 0;
    bool wifi_up = false, ever_connected = false, ntp_started = false;

    for (;;) {
        esp_task_wdt_reset();
        int64_t now = now_ms();

        if (g_portal_request && !portal::active()) {
            if (g_client) esp_mqtt_client_stop(g_client);
            portal::start();
        }
        if (portal::active()) {
            portENTER_CRITICAL(&g_lock);
            g_shared.portal = true;
            g_shared.wifi_up = g_shared.mqtt_up = false;
            portEXIT_CRITICAL(&g_lock);
            portal::loop();
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;
        }

        // ---- Wi-Fi
        bool up = WiFi.status() == WL_CONNECTED;
        if (up != wifi_up) {
            wifi_up = up;
            if (up) {
                ever_connected = true;
                wifi_backoff = WIFI_RETRY_MIN_MS;
                Serial.printf("[wifi] connected, IP %s, RSSI %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
                if (!ntp_started) {
                    configTime(0, 0, NTP_SERVER);  // UTC; only for logs and TLS certificate dates
                    ntp_started = true;
                }
            } else {
                Serial.println("[wifi] lost");
                if (g_mqtt_up) {  // tell the edge now, not when the keep-alive runs out
                    g_mqtt_up = false;
                    post_link(false, FallbackWhy::WifiLost);
                }
                wifi_retry_at = now + wifi_backoff;
            }
        }
        if (!wifi_up) {
            if (!ever_connected && now - boot > WIFI_CONNECT_TIMEOUT_MS) {
                Serial.println("[wifi] could not join: opening the setup portal");
                g_portal_request = true;
                continue;
            }
            if (ever_connected && now >= wifi_retry_at) {
                WiFi.disconnect();
                WiFi.begin(g_set.ssid, g_set.wifi_pass);
                wifi_retry_at = now + wifi_backoff;
                wifi_backoff = wifi_backoff * 2 > WIFI_RETRY_MAX_MS ? WIFI_RETRY_MAX_MS : wifi_backoff * 2;
            }
        }

        // ---- MQTT (esp-mqtt reconnects by itself while Wi-Fi is up)
        if (wifi_up && !g_mqtt_started) g_mqtt_started = mqtt_start();

        portENTER_CRITICAL(&g_lock);
        g_shared.wifi_up = wifi_up;
        g_shared.mqtt_up = g_mqtt_up;
        portEXIT_CRITICAL(&g_lock);

        // ---- outgoing
        Outgoing o;
        while (xQueueReceive(g_out, &o, 0) == pdTRUE) queue_outgoing(o);
        if (g_mqtt_up) {
            if (!g_boot_event_sent) {  // once per boot, on the first connection
                char buf[msg::kMaxPayload];
                msg::Header h{g_seq.next(msg::Out::Events), now, g_bid};
                if (msg::build_boot_event(buf, sizeof buf, h, reset_reason(), FIRMWARE_VERSION)) ring_push(msg::Out::Events, buf);
                g_boot_event_sent = true;
            }
            ring_flush();
            if (g_status_request || now >= next_status) {
                g_status_request = false;
                publish_status(now);
                next_status = now + STATUS_PERIOD_MS;
            }
            if (now >= next_ping) {
                publish_ping(now);
                next_ping = now + PING_PERIOD_MS;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

// Publishing for the sensor task (sensors) and the console (clean shutdown).
bool publish_now(const char* suffix, const char* payload, size_t len, int qos, bool retain) {
    return publish(suffix, payload, len, qos, retain);
}

bool mqtt_connected() { return g_mqtt_up; }

void announce_offline() {  // a clean shutdown publishes the last will itself (contract 3.3)
    char buf[96];
    msg::Header h{0, now_ms(), g_bid};
    size_t n = msg::build_offline(buf, sizeof buf, h);
    publish("status", buf, n, 1, true);
}

}  // namespace app
