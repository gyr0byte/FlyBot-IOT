// Cross-check: replay vectors recorded from the Python reference (firmware/tools/gen_vectors.py) through
// the C++ port and require the same results. Doubles are compared exactly (bit for bit, apart from the
// sign of zero), except values that go through exp(), which may differ in the last bit between C
// libraries; those must agree within 1e-12 relative, and are counted (should be 0 or very few).
#include <unity.h>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <memory>
#include <string>
#include <vector>

#include "edge.h"
#include "py_compat.h"
#include "sensing.h"
#include "streams.h"

using namespace flybot;

static const char* kDirs[] = {"test/vectors/", "firmware/test/vectors/", "../test/vectors/"};
static std::string g_dir;
static int g_near = 0;  // exp()-dependent values that matched within tolerance but not exactly

// ---------------------------------------------------------------------------- reading

static bool read_lines(const std::string& name, std::vector<std::string>& out) {
    FILE* f = std::fopen((g_dir + name).c_str(), "rb");
    if (!f) return false;
    char buf[2048];
    while (std::fgets(buf, sizeof buf, f)) {
        size_t n = std::strlen(buf);
        while (n && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) buf[--n] = '\0';
        out.emplace_back(buf);
    }
    std::fclose(f);
    return true;
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

static double hexd(const std::string& s) {
    uint64_t u = std::strtoull(s.c_str(), nullptr, 16);
    double v;
    std::memcpy(&v, &u, sizeof v);
    return v;
}
static Opt<double> hexo(const std::string& s) { return s == "-" ? Opt<double>() : Opt<double>(hexd(s)); }
static int64_t num(const std::string& s) { return std::strtoll(s.c_str(), nullptr, 10); }

static std::string str(long long v) {  // (old MinGW printf has no %lld)
    std::string s;
    unsigned long long u = v < 0 ? 0ULL - static_cast<unsigned long long>(v) : static_cast<unsigned long long>(v);
    do { s.insert(s.begin(), static_cast<char>('0' + u % 10)); u /= 10; } while (u);
    return v < 0 ? "-" + s : s;
}

static bool same(double a, double b) { return a == b || (std::isnan(a) && std::isnan(b)); }
static bool near(double a, double b) {
    if (same(a, b)) return true;
    double scale = std::fmax(std::fabs(a), std::fabs(b));
    if (std::fabs(a - b) <= 1e-12 * (scale > 1.0 ? scale : 1.0)) { ++g_near; return true; }
    return false;
}
static std::string hexstr(double v) {
    char b[64];
    std::snprintf(b, sizeof b, "%.17g", v);
    return b;
}

static bool load_index(std::vector<std::string>& files) {
    for (const char* d : kDirs) {
        g_dir = d;
        files.clear();
        if (read_lines("index.txt", files)) return true;
    }
    return false;
}

static Mode parse_mode(const std::string& s) {
    return s == "manual" ? Mode::Manual : s == "estop" ? Mode::Estop : Mode::Auto;
}

static EdgeConfig load_cfg(const std::vector<std::string>& lines, std::string& err) {
    EdgeConfig c;
    int n = 0;
    for (const auto& l : lines) {
        if (l.compare(0, 4, "cfg ") != 0) continue;
        auto t = split(l);
        if (!c.set(t[1].c_str(), hexd(t[2]))) err = "unknown config field " + t[1];
        ++n;
    }
    if (n != EdgeConfig::field_count()) err = "vector has " + str(n) + " config fields, C++ has " +
                                               str(EdgeConfig::field_count());
    return c;
}

// ---------------------------------------------------------------------------- edge replay

struct RandQueue {
    std::deque<double> q;
    int overdrawn = 0;
};
static double rand_pop(void* ctx) {
    RandQueue* r = static_cast<RandQueue*>(ctx);
    if (r->q.empty()) { ++r->overdrawn; return 0.5; }
    double v = r->q.front();
    r->q.pop_front();
    return v;
}

static bool compare_event(const EdgeEvent& ev, const std::vector<std::string>& t, std::string& why) {
    if (t[1] != to_str(ev.type)) { why = std::string("type ") + to_str(ev.type) + " != " + t[1]; return false; }
    int keys = 0;
    for (size_t i = 2; i < t.size(); ++i) {
        size_t eq = t[i].find('=');
        std::string k = t[i].substr(0, eq), v = t[i].substr(eq + 1);
        ++keys;
        bool ok = true;
        if (k == "ttc") ok = ev.ttc == num(v);
        else if (k == "d") ok = same(ev.d, hexd(v));
        else if (k == "lat") ok = ev.has_lat && same(ev.lat, hexd(v));
        else if (k == "resp") ok = same(ev.resp, hexd(v));
        else if (k == "hab") ok = near(ev.hab, hexd(v));
        else if (k == "why") ok = v == to_str(ev.why);
        else if (k == "dur") ok = ev.dur == num(v);
        else if (k == "bat") ok = same(ev.bat, hexd(v));
        else if (k == "cut") ok = ev.cut == (v == "1");
        else { why = "unknown key " + k; return false; }
        if (!ok) { why = "field " + k + " differs (Python " + v + ")"; return false; }
    }
    bool lat_key = false;
    for (size_t i = 2; i < t.size(); ++i) lat_key = lat_key || t[i].compare(0, 4, "lat=") == 0;
    if (ev.type == EventType::Escape && ev.has_lat != lat_key) {
        why = "lat presence differs";
        return false;
    }
    (void)keys;
    return true;
}

// Returns "" on success, else the first difference.
static std::string replay_edge(const std::string& file, int& updates) {
    std::vector<std::string> lines;
    if (!read_lines(file, lines)) return "cannot read " + file;
    std::string err;
    EdgeConfig cfg = load_cfg(lines, err);
    if (!err.empty()) return err;
    RandQueue rq;
    std::unique_ptr<Edge> e(new Edge(cfg, rand_pop, &rq));
    double last_l = 0, last_r = 0;
    std::string last_st = "boot", last_m = "auto";
    bool last_dirty = true;
    auto where = [&](size_t i) { return file + ":" + str(i + 1) + ": "; };

    for (size_t i = 0; i < lines.size(); ++i) {
        const std::string& line = lines[i];
        if (line.empty() || line[0] == '#' || line.compare(0, 4, "cfg ") == 0) continue;
        auto t = split(line);
        const std::string& op = t[0];
        bool is_call = true;
        if (op == "r") { rq.q.push_back(hexd(t[1])); continue; }
        if (op == "link") e->on_link(t[2] == "1", num(t[1]));
        else if (op == "mode") {
            CmdMode m{static_cast<uint32_t>(num(t[5])), num(t[6]), parse_mode(t[3]), {}};
            std::strncpy(m.src, t[4].c_str(), sizeof m.src - 1);
            e->on_mode(m, num(t[1]), t[2] == "1");
        } else if (op == "motor") {
            CmdMotor m{static_cast<uint32_t>(num(t[2])), num(t[3]), hexd(t[4]), hexd(t[5]), num(t[6]),
                       static_cast<uint32_t>(num(t[7]))};
            e->on_motor(m, num(t[1]));
        } else if (op == "manual") {
            CmdManual m{static_cast<uint32_t>(num(t[2])), num(t[3]), hexd(t[4]), hexd(t[5]), num(t[6]),
                        static_cast<uint32_t>(num(t[7])), {}};
            std::strncpy(m.src, t[8].c_str(), sizeof m.src - 1);
            e->on_manual(m, num(t[1]));
        } else if (op == "sleep") {
            CmdSleep m{static_cast<uint32_t>(num(t[2])), num(t[3]), t[4] == "1", {}};
            std::strncpy(m.why, t[5].c_str(), sizeof m.why - 1);
            e->on_sleep(m, num(t[1]));
        } else if (op == "inv") e->on_invalid(t[1] == "cmd/motor" ? Topic::CmdMotor : Topic::Other);
        else if (op == "dist") e->on_distance(hexo(t[2]), hexo(t[3]), num(t[1]));
        else if (op == "bat") e->on_battery(hexd(t[2]), num(t[1]));
        else if (op == "odr") e->set_odour_ready(t[1] == "1");
        else if (op == "clr") e->status_dirty = false;
        else if (op == "U" || op == "u") {
            int64_t now = num(t[1]);
            Wheels w = e->update(now);
            ++updates;
            if (op == "U") {
                last_l = hexd(t[2]); last_r = hexd(t[3]); last_st = t[4]; last_m = t[5]; last_dirty = t[6] == "1";
                if (!near(e->hab_level(now), hexd(t[7])))
                    return where(i) + "hab " + hexstr(e->hab_level(now)) + " != Python " + hexstr(hexd(t[7]));
            }
            if (!same(w.l, last_l) || !same(w.r, last_r))
                return where(i) + "motors (" + hexstr(w.l) + ", " + hexstr(w.r) + ") != Python (" + hexstr(last_l) +
                       ", " + hexstr(last_r) + ")";
            if (last_st != to_str(e->state) || last_m != to_str(e->mode) || last_dirty != e->status_dirty)
                return where(i) + "state " + to_str(e->state) + "/" + to_str(e->mode) + " != Python " + last_st + "/" +
                       last_m;
        } else if (op == "pop") {
            is_call = false;
            int n = static_cast<int>(num(t[1]));
            for (int k = 0; k < n; ++k) {
                EdgeEvent ev;
                if (!e->pop_event(ev)) return where(i) + "C++ has fewer events than Python";
                auto et = split(lines[i + 1 + k]);
                std::string why;
                if (!compare_event(ev, et, why)) return where(i + 1 + k) + "event: " + why;
            }
            if (e->pending_events() != 0) return where(i) + "C++ has more events than Python";
            i += n;
            continue;
        } else if (op == "chk") {
            is_call = false;
            if (t[1] != to_str(e->state) || t[2] != to_str(e->mode) || (t[3] == "1") != e->status_dirty)
                return where(i) + "state " + to_str(e->state) + "/" + to_str(e->mode) + " != Python " + t[1] + "/" + t[2];
        } else if (op == "end") {
            is_call = false;
            const EdgeStats& s = e->stats;
            int64_t got[] = {s.escapes, s.fallback_ms, s.obstacle_stops, s.looms, s.habituated,
                             (int64_t)e->streams.lost, (int64_t)e->streams.dropped, (int64_t)e->streams.restarts};
            for (int k = 0; k < 8; ++k)
                if (got[k] != num(t[1 + k])) return where(i) + "end counter " + str(k) + " differs";
        } else {
            return where(i) + "unknown line " + op;
        }
        if (is_call) {
            if (rq.overdrawn) return where(i) + "C++ drew more random numbers than Python";
            if (!rq.q.empty()) return where(i) + "C++ drew fewer random numbers than Python";
        }
    }
    return "";
}

// ---------------------------------------------------------------------------- tests

void test_arithmetic_vectors() {
    std::vector<std::string> lines;
    TEST_ASSERT_TRUE_MESSAGE(read_lines("arith.txt", lines), "arith.txt missing");
    int n = 0;
    for (size_t i = 0; i < lines.size(); ++i) {
        auto t = split(lines[i]);
        if (t[0] == "R") {
            double x = hexd(t[1]), want = hexd(t[3]);
            int digits = static_cast<int>(num(t[2]));
            double got = digits == 0 ? py_round_int(x) : py_round(x, digits);
            if (!same(got, want)) {
                std::string m = "arith.txt:" + str(i + 1) + ": round(" + hexstr(x) + ", " + t[2] + ") = " +
                                hexstr(got) + ", Python " + hexstr(want);
                TEST_FAIL_MESSAGE(m.c_str());
            }
        } else if (t[0] == "S") {
            NeumaierSum s;
            size_t k = 1;
            for (; t[k] != "="; ++k) s.add(hexd(t[k]));
            if (!same(s.result(), hexd(t[k + 1]))) {
                std::string m = "arith.txt:" + str(i + 1) + ": sum differs";
                TEST_FAIL_MESSAGE(m.c_str());
            }
        }
        ++n;
    }
    char msg[64];
    std::snprintf(msg, sizeof msg, "%d round/sum vectors identical", n);
    TEST_MESSAGE(msg);
}

void test_stream_vectors() {
    std::vector<std::string> lines;
    TEST_ASSERT_TRUE_MESSAGE(read_lines("streams.txt", lines), "streams.txt missing");
    StreamTracker st(600000);
    for (size_t i = 0; i < lines.size(); ++i) {
        auto t = split(lines[i]);
        StreamKey key = StreamKey::of(static_cast<Topic>(num(t[1])), t[2] == "-" ? "" : t[2].c_str());
        std::string where = "streams.txt:" + str(i + 1);
        if (t[0] == "X") { st.reset(key); continue; }
        Opt<uint32_t> bid;
        if (t[6] != "-") bid = static_cast<uint32_t>(num(t[6]));
        uint32_t seq = static_cast<uint32_t>(std::strtoull(t[3].c_str(), nullptr, 10));
        if (t[0] == "M") { st.remember(key, seq, num(t[4]), num(t[5]), bid); continue; }
        bool ok = st.accept(key, seq, num(t[4]), num(t[5]), bid);
        TEST_ASSERT_EQUAL_MESSAGE(t[7] == "1", ok, where.c_str());
        TEST_ASSERT_EQUAL_UINT64_MESSAGE((uint64_t)num(t[8]), st.lost, where.c_str());
        TEST_ASSERT_EQUAL_UINT64_MESSAGE((uint64_t)num(t[9]), st.dropped, where.c_str());
        TEST_ASSERT_EQUAL_UINT64_MESSAGE((uint64_t)num(t[10]), st.restarts, where.c_str());
        TEST_ASSERT_EQUAL_MESSAGE(t[11] == "1", st.last_restarted, where.c_str());
    }
    char msg[64];
    std::snprintf(msg, sizeof msg, "%u stream operations identical", (unsigned)lines.size());
    TEST_MESSAGE(msg);
}

void test_sensing_vectors() {
    std::vector<std::string> files;
    TEST_ASSERT_TRUE_MESSAGE(load_index(files), "test/vectors/index.txt not found (run gen_vectors.py)");
    int checked = 0, nfiles = 0;
    for (const auto& file : files) {
        if (file.compare(0, 8, "sensing_") != 0) continue;
        ++nfiles;
        std::vector<std::string> lines;
        TEST_ASSERT_TRUE(read_lines(file, lines));
        std::string err;
        EdgeConfig cfg = load_cfg(lines, err);
        TEST_ASSERT_TRUE_MESSAGE(err.empty(), err.c_str());
        OdourChannel ch0(cfg), ch1(cfg);
        OdourChannel* ch[2] = {&ch0, &ch1};
        DistanceFilter f(cfg);
        BatteryMonitor bm(cfg);
        for (size_t i = 0; i < lines.size(); ++i) {
            auto t = split(lines[i]);
            std::string where = file + ":" + str(i + 1);
            bool ok = true;
            if (t[0] == "o") {
                Opt<double> got = ch[num(t[1])]->feed(hexd(t[3]), num(t[2])), want = hexo(t[4]);
                ok = got.has_value() == want.has_value() && (!got || same(*got, *want));  // exact (all are)
            } else if (t[0] == "f") {
                Opt<double> got = f.feed(hexo(t[2]), num(t[1])), want = hexo(t[3]);
                ok = got.has_value() == want.has_value() && (!got || same(*got, *want));
            } else if (t[0] == "b") {
                ok = same(bm.feed(hexd(t[1])), hexd(t[2]));
            } else {
                continue;
            }
            TEST_ASSERT_TRUE_MESSAGE(ok, where.c_str());
            ++checked;
        }
    }
    TEST_ASSERT_TRUE(nfiles > 0);
    char msg[96];
    std::snprintf(msg, sizeof msg, "%d sensing steps in %d files identical (%d within 1e-12 only)", checked, nfiles, g_near);
    TEST_MESSAGE(msg);
}

void test_edge_vectors() {
    std::vector<std::string> files;
    TEST_ASSERT_TRUE_MESSAGE(load_index(files), "test/vectors/index.txt not found (run gen_vectors.py)");
    int nfiles = 0, updates = 0;
    std::string failures;
    for (const auto& file : files) {
        if (file.compare(0, 5, "edge_") != 0) continue;
        ++nfiles;
        int u = 0;
        std::string r = replay_edge(file, u);
        updates += u;
        if (!r.empty()) failures += r + "\n";
        std::printf("  %-28s %6d updates  %s\n", file.c_str(), u, r.empty() ? "identical" : "DIFFERENT");
    }
    if (!failures.empty()) TEST_FAIL_MESSAGE(failures.c_str());
    TEST_ASSERT_TRUE(nfiles > 0);
    char msg[128];
    std::snprintf(msg, sizeof msg, "%d edge vectors, %d control steps: all identical (%d exp()-values within 1e-12 only)",
                  nfiles, updates, g_near);
    TEST_MESSAGE(msg);
}

void setUp() {}
void tearDown() {}

int main() {
    std::vector<std::string> files;
    load_index(files);  // sets g_dir
    UNITY_BEGIN();
    RUN_TEST(test_arithmetic_vectors);
    RUN_TEST(test_stream_vectors);
    RUN_TEST(test_sensing_vectors);
    RUN_TEST(test_edge_vectors);
    return UNITY_END();
}
