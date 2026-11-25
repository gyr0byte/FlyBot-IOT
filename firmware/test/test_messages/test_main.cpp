// Contract messages on the PC:
//  - the parsers must agree with the Python contract on 1 400+ valid and broken payloads (vectors);
//  - the builders' output is written to .pio/messages_out.txt for firmware/tools/check_messages.py,
//    which validates every message with the Python contract and checks every rounded value;
//  - unit tests of the round-trip window, the sent log, the debouncer, the buzzer and the LED colours.
#include <unity.h>

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "io_util.h"
#include "messages.h"

using namespace flybot;
using namespace flybot::msg;

static const char* kDirs[] = {"test/vectors/", "firmware/test/vectors/", "../test/vectors/"};

static bool read_lines(std::vector<std::string>& out) {
    for (const char* d : kDirs) {
        FILE* f = std::fopen((std::string(d) + "messages.txt").c_str(), "rb");
        if (!f) continue;
        char buf[8192];
        while (std::fgets(buf, sizeof buf, f)) {
            size_t n = std::strlen(buf);
            while (n && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) buf[--n] = '\0';
            out.emplace_back(buf);
        }
        std::fclose(f);
        return true;
    }
    return false;
}

static std::vector<std::string> split(const std::string& s) {
    std::vector<std::string> t;
    size_t i = 0;
    while (i < s.size()) {
        while (i < s.size() && s[i] == ' ') ++i;
        size_t j = i;
        while (j < s.size() && s[j] != ' ') ++j;
        if (j > i) t.push_back(s.substr(i, j - i));
        i = j;
    }
    return t;
}

static std::string unhex(const std::string& h) {
    std::string s;
    for (size_t i = 0; i + 1 < h.size(); i += 2) s.push_back(static_cast<char>(std::strtol(h.substr(i, 2).c_str(), nullptr, 16)));
    return s;
}
static double hexd(const std::string& s) {
    uint64_t u = std::strtoull(s.c_str(), nullptr, 16);
    double v;
    std::memcpy(&v, &u, sizeof v);
    return v;
}
static long long num(const std::string& s) { return std::strtoll(s.c_str(), nullptr, 10); }

void test_parsers_agree_with_the_python_contract() {
    std::vector<std::string> lines;
    TEST_ASSERT_TRUE_MESSAGE(read_lines(lines), "test/vectors/messages.txt missing");
    int n = 0, valid = 0;
    for (size_t i = 0; i < lines.size(); ++i) {
        auto t = split(lines[i]);
        if (t.empty() || t[0] != "P") continue;
        std::string payload = t.size() > 3 ? unhex(t[3]) : std::string();
        bool want_ok = t[2] == "ok";
        char where[96];
        std::snprintf(where, sizeof where, "messages.txt:%u (%s)", (unsigned)i + 1, t[1].c_str());
        Parse p = Parse::Invalid;
        std::vector<std::string> v = (want_ok && i + 1 < lines.size()) ? split(lines[i + 1]) : std::vector<std::string>();
        if (t[1] == "cmd/motor" || t[1] == "cmd/manual") {
            CmdMotor m{};
            CmdManual mm{};
            bool manual = t[1] == "cmd/manual";
            p = manual ? parse_manual(payload.data(), payload.size(), mm) : parse_motor(payload.data(), payload.size(), m);
            if (p == Parse::Ok && want_ok) {
                uint32_t seq = manual ? mm.seq : m.seq;
                long long up = manual ? mm.up : m.up;
                double l = manual ? mm.l : m.l, r = manual ? mm.r : m.r;
                long long ttl = manual ? mm.ttl : m.ttl, rs = manual ? mm.rs : m.rs;
                TEST_ASSERT_EQUAL_UINT32_MESSAGE((uint32_t)num(v[1]), seq, where);
                TEST_ASSERT_TRUE_MESSAGE(num(v[2]) == up, where);
                TEST_ASSERT_DOUBLE_WITHIN_MESSAGE(1e-12, hexd(v[3]), l, where);
                TEST_ASSERT_DOUBLE_WITHIN_MESSAGE(1e-12, hexd(v[4]), r, where);
                TEST_ASSERT_TRUE_MESSAGE(num(v[5]) == ttl && num(v[6]) == rs, where);
                if (manual) TEST_ASSERT_EQUAL_STRING_MESSAGE(unhex(v[7]).c_str(), mm.src, where);
            }
        } else if (t[1] == "cmd/mode") {
            CmdMode m{};
            p = parse_mode(payload.data(), payload.size(), m);
            if (p == Parse::Ok && want_ok) {
                TEST_ASSERT_TRUE_MESSAGE(num(v[1]) == m.seq && num(v[2]) == m.up, where);
                TEST_ASSERT_EQUAL_STRING_MESSAGE(v[3].c_str(), to_str(m.m), where);
                TEST_ASSERT_EQUAL_STRING_MESSAGE(unhex(v[4]).c_str(), m.src, where);
            }
        } else if (t[1] == "cmd/sleep") {
            CmdSleep m{};
            p = parse_sleep(payload.data(), payload.size(), m);
            if (p == Parse::Ok && want_ok) {
                TEST_ASSERT_TRUE_MESSAGE(num(v[1]) == m.seq && num(v[2]) == m.up, where);
                TEST_ASSERT_TRUE_MESSAGE((v[3] == "1") == m.s, where);
                TEST_ASSERT_EQUAL_STRING_MESSAGE(v[4].c_str(), m.why, where);
            }
        } else if (t[1] == "pong") {
            Pong m{};
            p = parse_pong(payload.data(), payload.size(), m);
            if (p == Parse::Ok && want_ok)
                TEST_ASSERT_TRUE_MESSAGE(num(v[1]) == m.seq && num(v[2]) == m.up && num(v[3]) == m.ps && num(v[4]) == m.pu,
                                         where);
        }
        if ((p == Parse::Ok) != want_ok) {
            std::string m = std::string(where) + ": C++ says " + (p == Parse::Ok ? "ok" : "invalid") + ", Python " + t[2] +
                            ": " + payload;
            TEST_FAIL_MESSAGE(m.c_str());
        }
        ++n;
        valid += want_ok;
    }
    char msg[80];
    std::snprintf(msg, sizeof msg, "%d payloads (%d valid): every verdict and value as Python", n, valid);
    TEST_MESSAGE(msg);
}

void test_unknown_version_is_reported_separately() {
    CmdMotor m{};
    const char* p = "{\"v\":7,\"seq\":1,\"up\":1,\"l\":0,\"r\":0,\"ttl\":300,\"rs\":0}";
    TEST_ASSERT_EQUAL_INT((int)Parse::UnknownVersion, (int)parse_motor(p, std::strlen(p), m));
    const char* nan = "{\"v\":1,\"seq\":1,\"up\":1,\"l\":NaN,\"r\":0,\"ttl\":300,\"rs\":0}";
    TEST_ASSERT_EQUAL_INT((int)Parse::Invalid, (int)parse_motor(nan, std::strlen(nan), m));  // as the contract (rev. 10)
}

// ---------------------------------------------------------------------------- builders -> file

struct Lcg {
    uint64_t s = 12345;
    double next() { s = s * 6364136223846793005ULL + 1442695040888963407ULL; return (s >> 11) * (1.0 / 9007199254740992.0); }
};

static std::string hx(double v) {
    uint64_t u;
    std::memcpy(&u, &v, sizeof u);
    char b[20];
    std::snprintf(b, sizeof b, "%08lx%08lx", (unsigned long)(u >> 32), (unsigned long)(u & 0xffffffffu));
    return b;
}
static std::string ox(Opt<double> v) { return v ? hx(*v) : "-"; }
static std::string dec(unsigned long long v) {
    std::string s;
    do { s.insert(s.begin(), static_cast<char>('0' + v % 10)); v /= 10; } while (v);
    return s;
}

void test_builders_write_messages_for_the_python_check() {
    FILE* f = std::fopen(".pio/messages_out.txt", "wb");
    if (!f) f = std::fopen("messages_out.txt", "wb");
    TEST_ASSERT_NOT_NULL(f);
    Lcg rng;
    char buf[kMaxPayload];
    int count = 0;
    auto pick = [&](double lo, double hi) {
        double u = rng.next();
        if (u < 0.05) return lo - 1.0;          // out of range: must be clamped
        if (u < 0.10) return hi + 5.0;
        if (u < 0.15) return lo;
        if (u < 0.20) return hi;
        return lo + (hi - lo) * rng.next();
    };
    auto opt = [&](double lo, double hi) { return rng.next() < 0.15 ? Opt<double>() : Opt<double>(pick(lo, hi)); };
    for (int i = 0; i < 400; ++i) {
        Header h{static_cast<uint32_t>(rng.next() * 4294967295.0), static_cast<int64_t>(rng.next() * 9.0e15),
                 static_cast<uint32_t>(1 + rng.next() * 4294967294.0)};
        std::string hs = dec(h.seq) + " " + dec(static_cast<unsigned long long>(h.up)) + " " + dec(h.bid);
        Opt<double> ol = opt(0, 1), orr = opt(0, 1), d = opt(2, 400), lt = opt(0, 1);
        double bat = pick(0, 10);
        TEST_ASSERT_TRUE(build_sensors(buf, sizeof buf, h, ol, orr, d, lt, bat) > 0);
        std::fprintf(f, "sensors %s | %s %s %s %s %s %s\n", buf, hs.c_str(), ox(ol).c_str(), ox(orr).c_str(),
                     ox(d).c_str(), ox(lt).c_str(), hx(bat).c_str());
        EdgeEvent ev;
        ev.type = static_cast<EventType>(i % 5);
        ev.ttc = static_cast<int64_t>(pick(0, 10000));
        ev.d = pick(2, 400);
        ev.resp = rng.next() < 0.3 ? 0.0 : pick(0, 1);
        ev.has_lat = ev.resp > 0;
        ev.lat = pick(0, 1000);
        ev.hab = pick(0, 1);
        ev.why = static_cast<FallbackWhy>(i % 4);
        ev.dur = static_cast<int64_t>(pick(0, 86400000));
        ev.bat = pick(0, 10);
        ev.cut = rng.next() < 0.5;
        TEST_ASSERT_TRUE(build_event(buf, sizeof buf, h, ev) > 0);
        std::fprintf(f, "events %s | %s %s %s %s %s %s\n", buf, hs.c_str(), hx(ev.d).c_str(), hx(ev.lat).c_str(),
                     hx(ev.resp).c_str(), hx(ev.hab).c_str(), hx(ev.bat).c_str());
        StatusInfo s{static_cast<State>(i % 7), static_cast<Mode>(i % 3), static_cast<int>(-140 + rng.next() * 150),
                     static_cast<uint32_t>(rng.next() * 3.0e7), pick(0, 10), opt(0, 60000), opt(0, 60000), "0.6.0",
                     pick(0, 1)};
        TEST_ASSERT_TRUE(build_status(buf, sizeof buf, h, s) > 0);
        std::fprintf(f, "status %s | %s %s %s %s %s\n", buf, hs.c_str(), hx(s.bat).c_str(), ox(s.rtt).c_str(),
                     ox(s.crtt).c_str(), hx(s.hab).c_str());
        count += 3;
        if (i % 20 == 0) {
            build_offline(buf, sizeof buf, h);
            std::fprintf(f, "status %s | %s\n", buf, hs.c_str());
            build_reward(buf, sizeof buf, h, i % 40 == 0 ? 1 : -1);
            std::fprintf(f, "reward %s | %s\n", buf, hs.c_str());
            build_ping(buf, sizeof buf, h);
            std::fprintf(f, "ping %s | %s\n", buf, hs.c_str());
            static const char* rst[] = {"power_on", "watchdog", "brownout", "software", "panic", "other", "bogus"};
            build_boot_event(buf, sizeof buf, h, rst[(i / 20) % 7], i % 40 ? "0.6.0" : "bad version!");
            std::fprintf(f, "events %s | %s\n", buf, hs.c_str());
            count += 4;
        }
    }
    std::fclose(f);
    char msg[96];
    std::snprintf(msg, sizeof msg, "%d messages written for tools/check_messages.py", count);
    TEST_MESSAGE(msg);
}

void test_append_fixed_rounds_like_python_and_never_writes_minus_zero() {
    char b[32];
    size_t pos = 0;
    append_fixed(b, sizeof b, pos, 0.125, 2, 0, 1);  // Python round(0.125, 2) = 0.12
    TEST_ASSERT_EQUAL_STRING("0.12", b);
    pos = 0;
    append_fixed(b, sizeof b, pos, 1.0, 2, 0, 1);
    TEST_ASSERT_EQUAL_STRING("1.0", b);
    pos = 0;
    append_fixed(b, sizeof b, pos, -0.0001, 2, 0, 1);
    TEST_ASSERT_EQUAL_STRING("0.0", b);
    pos = 0;
    append_fixed(b, sizeof b, pos, 38.45, 1, 2, 400);  // 38.45 is 38.4500000000000028... -> 38.5
    TEST_ASSERT_EQUAL_STRING("38.5", b);
}

// ---------------------------------------------------------------------------- helpers

void test_rtt_window_median_like_the_simulator() {
    RttWindow w(10000);
    TEST_ASSERT_FALSE(w.median(0).has_value());
    w.add(0, 50.0); w.add(1000, 10.0); w.add(2000, 30.0); w.add(3000, 20.0);
    TEST_ASSERT_EQUAL_DOUBLE(30.0, *w.median(3000));  // sorted 10 20 30 50 -> index 2
    TEST_ASSERT_EQUAL_DOUBLE(30.0, *w.median(11500)); // 0 and 1000 are older than 10 s: 20 30 -> index 1
    RttWindow big(10000);
    big.add(0, 99999.0);
    TEST_ASSERT_EQUAL_DOUBLE(60000.0, *big.median(0)); // capped at the contract maximum
}

void test_sent_log_counts_the_first_answer_only() {
    SentLog log;
    log.sent(7, 1000);
    log.sent(8, 1050);
    TEST_ASSERT_TRUE(log.answered(7) == int64_t(1000));
    TEST_ASSERT_FALSE(log.answered(7).has_value());
    TEST_ASSERT_FALSE(log.answered(99).has_value());
}

void test_debouncer_ignores_bounce() {
    Debouncer d(30);
    int presses = 0;
    bool pattern[] = {1, 0, 1, 0, 1, 1, 1, 1, 1, 1, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1};
    for (int i = 0; i < 28; ++i) presses += d.update(pattern[i], i * 5);
    TEST_ASSERT_EQUAL_INT(2, presses);
}

void test_buzzer_plays_and_ends() {
    Buzzer b;
    b.play(Tune::Escape, 1000);
    TEST_ASSERT_EQUAL_INT(2637, b.freq(1010));
    TEST_ASSERT_EQUAL_INT(0, b.freq(1060));
    TEST_ASSERT_EQUAL_INT(2637, b.freq(1080));
    TEST_ASSERT_EQUAL_INT(0, b.freq(1200));
    TEST_ASSERT_FALSE(b.busy());
}

void test_mood_colours() {
    MoodInput in{State::Cloud, Mode::Auto, true, false, false, false};
    Rgb c = mood_color(in, 0);
    TEST_ASSERT_TRUE(c.g == 255 && c.r == 0);
    in.st = State::Escape;
    TEST_ASSERT_EQUAL_UINT8(255, mood_color(in, 0).r);
    in.st = State::Fallback; in.connected = false;
    TEST_ASSERT_EQUAL_UINT8(255, mood_color(in, 0).b);  // offline: purple
    in.portal = true;
    TEST_ASSERT_EQUAL_UINT8(255, mood_color(in, 100).b);
    TEST_ASSERT_EQUAL_UINT8(0, mood_color(in, 600).b);
}

void setUp() {}
void tearDown() {}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_parsers_agree_with_the_python_contract);
    RUN_TEST(test_unknown_version_is_reported_separately);
    RUN_TEST(test_builders_write_messages_for_the_python_check);
    RUN_TEST(test_append_fixed_rounds_like_python_and_never_writes_minus_zero);
    RUN_TEST(test_rtt_window_median_like_the_simulator);
    RUN_TEST(test_sent_log_counts_the_first_answer_only);
    RUN_TEST(test_debouncer_ignores_bounce);
    RUN_TEST(test_buzzer_plays_and_ends);
    RUN_TEST(test_mood_colours);
    return UNITY_END();
}
