#include "messages.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "py_compat.h"

namespace flybot {
namespace msg {

// ============================================================================ topics

const char* topic_suffix(Out t) {
    switch (t) {
        case Out::Sensors: return "sensors";
        case Out::Events: return "events";
        case Out::Status: return "status";
        case Out::Reward: return "reward";
        case Out::Ping: return "ping";
        default: return "";
    }
}

const char* topic_suffix(In t) {
    switch (t) {
        case In::CmdMotor: return "cmd/motor";
        case In::CmdManual: return "cmd/manual";
        case In::CmdMode: return "cmd/mode";
        case In::CmdSleep: return "cmd/sleep";
        case In::Pong: return "pong";
        default: return "";
    }
}

bool in_topic_from_suffix(const char* suffix, In& out) {
    for (int i = 0; i < static_cast<int>(In::kCount); ++i) {
        if (std::strcmp(suffix, topic_suffix(static_cast<In>(i))) == 0) {
            out = static_cast<In>(i);
            return true;
        }
    }
    return false;
}

// ============================================================================ writing

namespace {

constexpr uint32_t kSeqMax = 4294967295u;
constexpr int64_t kUpMax = 9007199254740991LL;

struct W {
    char* buf;
    size_t cap;
    size_t pos = 0;
    bool ok = true;
    void raw(const char* s) {
        size_t n = std::strlen(s);
        if (pos + n >= cap) { ok = false; return; }
        std::memcpy(buf + pos, s, n);
        pos += n;
        buf[pos] = '\0';
    }
    void u64(uint64_t v) {
        char t[24];
        int n = 0;
        do { t[n++] = static_cast<char>('0' + v % 10); v /= 10; } while (v);
        char s[24];
        for (int i = 0; i < n; ++i) s[i] = t[n - 1 - i];
        s[n] = '\0';
        raw(s);
    }
    void i64(int64_t v) {
        if (v < 0) { raw("-"); u64(0ULL - static_cast<uint64_t>(v)); }
        else u64(static_cast<uint64_t>(v));
    }
    void key(const char* k) { raw(pos > 1 ? ",\"" : "\""); raw(k); raw("\":"); }
    void str(const char* k, const char* v) { key(k); raw("\""); raw(v); raw("\""); }
    void num(const char* k, double v, int dec, double lo, double hi) {
        key(k);
        if (pos >= cap) { ok = false; return; }
        append_fixed(buf, cap, pos, v, dec, lo, hi);
        if (pos >= cap - 1) ok = false;
    }
    void opt(const char* k, Opt<double> v, int dec, double lo, double hi) {
        if (!v) { key(k); raw("null"); }
        else num(k, *v, dec, lo, hi);
    }
    void integer(const char* k, int64_t v, int64_t lo, int64_t hi) {
        key(k);
        i64(v < lo ? lo : v > hi ? hi : v);
    }
    void boolean(const char* k, bool v) { key(k); raw(v ? "true" : "false"); }
    void header(const Header& h, int v) {
        raw("{");
        integer("v", v, v, v);
        integer("seq", h.seq, 0, kSeqMax);
        integer("up", h.up, 0, kUpMax);
        integer("bid", h.bid == 0 ? 1 : h.bid, 1, kSeqMax);
    }
    size_t done() {
        raw("}");
        return ok ? pos : 0;
    }
};

}  // namespace

void append_fixed(char* buf, size_t cap, size_t& pos, double v, int dec, double lo, double hi) {
    if (!std::isfinite(v)) v = lo;
    v = py_max(lo, py_min(hi, v));
    double r = py_round(v, dec);  // the nearest double to a decimal with dec digits: no printf tie
    if (r == 0.0) r = 0.0;        // never "-0.0"
    char t[48];
    int n = std::snprintf(t, sizeof t, "%.*f", dec, r);
    if (n <= 0 || n >= static_cast<int>(sizeof t)) n = std::snprintf(t, sizeof t, "%.1f", lo);
    // Python repr style: strip trailing zeros, keep one decimal ("1.0", "0.25").
    char* dot = std::strchr(t, '.');
    if (dot) {
        int end = n - 1;
        while (end > (dot - t) + 1 && t[end] == '0') t[end--] = '\0';
        n = end + 1;
    }
    if (pos + static_cast<size_t>(n) >= cap) { pos = cap; return; }
    std::memcpy(buf + pos, t, static_cast<size_t>(n));
    pos += static_cast<size_t>(n);
    buf[pos] = '\0';
}

size_t build_sensors(char* buf, size_t cap, const Header& h, Opt<double> ol, Opt<double> orr, Opt<double> d,
                     Opt<double> lt, double bat) {
    W w{buf, cap};
    w.header(h, 2);
    w.opt("ol", ol, 3, 0.0, 1.0);
    w.opt("or", orr, 3, 0.0, 1.0);
    w.opt("d", d, 1, 2.0, 400.0);
    w.opt("lt", lt, 2, 0.0, 1.0);
    w.num("bat", bat, 2, 0.0, 10.0);
    return w.done();
}

size_t build_event(char* buf, size_t cap, const Header& h, const EdgeEvent& ev) {
    W w{buf, cap};
    w.header(h, 3);
    w.str("e", to_str(ev.type));
    switch (ev.type) {
        case EventType::Escape:
            w.integer("ttc", ev.ttc, 0, 10000);
            w.num("d", ev.d, 1, 2.0, 400.0);
            if (ev.has_lat && ev.resp > 0) w.num("lat", ev.lat, 1, 0.0, 1000.0);
            w.num("resp", ev.resp, 2, 0.0, 1.0);
            w.num("hab", ev.hab, 2, 0.0, 1.0);
            break;
        case EventType::Obstacle: w.num("d", ev.d, 1, 2.0, 400.0); break;
        case EventType::FallbackOn: w.str("why", to_str(ev.why)); break;
        case EventType::FallbackOff: w.integer("dur", ev.dur, 0, 86400000); break;
        case EventType::LowBattery:
            w.num("bat", ev.bat, 2, 0.0, 10.0);
            w.boolean("cut", ev.cut);
            break;
    }
    return w.done();
}

static bool fw_ok(const char* fw) {
    size_t n = std::strlen(fw);
    if (n < 1 || n > 16) return false;
    for (size_t i = 0; i < n; ++i) {
        char c = fw[i];
        if (!((c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '.' || c == '+' ||
              c == '-'))
            return false;
    }
    return true;
}

size_t build_boot_event(char* buf, size_t cap, const Header& h, const char* rst, const char* fw) {
    static const char* const kRst[] = {"power_on", "watchdog", "brownout", "software", "panic", "other"};
    bool known = false;
    for (const char* r : kRst) known = known || std::strcmp(r, rst) == 0;
    W w{buf, cap};
    w.header(h, 3);
    w.str("e", "boot");
    w.str("rst", known ? rst : "other");
    w.str("fw", fw_ok(fw) ? fw : "0");
    return w.done();
}

size_t build_status(char* buf, size_t cap, const Header& h, const StatusInfo& s) {
    W w{buf, cap};
    w.header(h, 3);
    w.str("st", to_str(s.st));
    w.str("m", to_str(s.m));
    w.integer("rssi", s.rssi, -127, 0);
    w.integer("heap", s.heap, 0, 16777216);
    w.num("bat", s.bat, 2, 0.0, 10.0);
    w.opt("rtt", s.rtt, 1, 0.0, 60000.0);
    w.opt("crtt", s.crtt, 1, 0.0, 60000.0);
    w.str("fw", fw_ok(s.fw) ? s.fw : "0");
    w.num("hab", s.hab, 2, 0.0, 1.0);
    return w.done();
}

size_t build_offline(char* buf, size_t cap, const Header& h) {
    W w{buf, cap};
    Header h0 = h;
    h0.seq = 0;  // fixed at connect time (contract 3.3)
    w.header(h0, 3);
    w.str("st", "offline");
    return w.done();
}

size_t build_reward(char* buf, size_t cap, const Header& h, int r) {
    W w{buf, cap};
    w.header(h, 3);
    w.integer("r", r >= 0 ? 1 : -1, -1, 1);
    w.str("src", "btn");
    return w.done();
}

size_t build_ping(char* buf, size_t cap, const Header& h) {
    W w{buf, cap};
    w.header(h, 2);
    return w.done();
}

// ============================================================================ strict JSON parsing
// Python's json module (what the contract code uses) is strict: exact grammar, UTF-8, no trailing
// text. This parser checks the whole document the same way and extracts the top-level members of the
// object (every incoming message is a flat object). Numbers: integer tokens exactly (64-bit), others
// with strtod (correctly rounded, like Python's float()). Duplicate keys: the last one wins, as in
// Python. No heap. NaN / Infinity are not JSON numbers and are rejected (contract revision 10).

namespace {

struct Member {
    enum Kind : uint8_t { Str, Int, BigInt, Dbl, Bool, Null, Other };
    char key[24];
    size_t key_len;
    bool key_long;   // longer than any allowed key
    Kind kind;
    int64_t i;
    double d;
    bool b;
    char s[32];
    size_t s_len;
    bool s_long;     // longer than any allowed string value
};

struct Flat {
    static constexpr int kMax = 16;
    Member m[kMax];
    int n = 0;
    bool too_many = false;
    const Member* find(const char* key) const {
        size_t kl = std::strlen(key);
        for (int i = n - 1; i >= 0; --i)  // the last occurrence wins
            if (!m[i].key_long && m[i].key_len == kl && std::memcmp(m[i].key, key, kl) == 0) return &m[i];
        return nullptr;
    }
};

void put_utf8(uint32_t cp, char* out, size_t cap, size_t& n, bool& overflow) {
    char t[4];
    int k;
    if (cp < 0x80) { t[0] = static_cast<char>(cp); k = 1; }
    else if (cp < 0x800) { t[0] = static_cast<char>(0xC0 | (cp >> 6)); t[1] = static_cast<char>(0x80 | (cp & 0x3F)); k = 2; }
    else if (cp < 0x10000) {
        t[0] = static_cast<char>(0xE0 | (cp >> 12)); t[1] = static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
        t[2] = static_cast<char>(0x80 | (cp & 0x3F)); k = 3;
    } else {
        t[0] = static_cast<char>(0xF0 | (cp >> 18)); t[1] = static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
        t[2] = static_cast<char>(0x80 | ((cp >> 6) & 0x3F)); t[3] = static_cast<char>(0x80 | (cp & 0x3F)); k = 4;
    }
    for (int i = 0; i < k; ++i) {
        if (n + 1 >= cap) { overflow = true; return; }
        out[n++] = t[i];
    }
    out[n] = '\0';
}

struct Scan {
    const char* p;
    const char* end;
    int depth = 0;
    void ws() { while (p < end && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) ++p; }
    bool lit(const char* s) {
        size_t n = std::strlen(s);
        if (static_cast<size_t>(end - p) < n || std::memcmp(p, s, n) != 0) return false;
        p += n;
        return true;
    }
    bool digits() {
        const char* s = p;
        while (p < end && *p >= '0' && *p <= '9') ++p;
        return p > s;
    }
    static int hexval(char c) {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    }
    bool hex4(uint32_t& v) {
        v = 0;
        for (int i = 0; i < 4; ++i) {
            if (p >= end) return false;
            int h = hexval(*p++);
            if (h < 0) return false;
            v = v * 16 + static_cast<uint32_t>(h);
        }
        return true;
    }
    // A string; decoded into out (if not null), truncation flagged in overflow.
    bool string(char* out, size_t cap, size_t& n, bool& overflow) {
        n = 0;
        overflow = false;
        if (out) out[0] = '\0';
        char dummy[1];
        if (!out) { out = dummy; cap = 0; }
        ++p;  // opening quote
        while (p < end) {
            unsigned char c = static_cast<unsigned char>(*p);
            if (c == '"') { ++p; return true; }
            if (c < 0x20) return false;  // control characters must be escaped
            if (c == '\\') {
                ++p;
                if (p >= end) return false;
                char e = *p++;
                uint32_t cp;
                switch (e) {
                    case '"': cp = '"'; break;
                    case '\\': cp = '\\'; break;
                    case '/': cp = '/'; break;
                    case 'b': cp = 8; break;
                    case 'f': cp = 12; break;
                    case 'n': cp = 10; break;
                    case 'r': cp = 13; break;
                    case 't': cp = 9; break;
                    case 'u': {
                        if (!hex4(cp)) return false;
                        if (cp >= 0xD800 && cp < 0xDC00 && end - p >= 6 && p[0] == '\\' && p[1] == 'u') {
                            const char* save = p;
                            p += 2;
                            uint32_t lo;
                            if (hex4(lo) && lo >= 0xDC00 && lo < 0xE000) cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                            else p = save;  // a lone surrogate (Python keeps it; it matches nothing here)
                        }
                        break;
                    }
                    default: return false;
                }
                if (cap) put_utf8(cp, out, cap, n, overflow);
                continue;
            }
            int k;  // UTF-8 sequence length, strict (as Python's decoder)
            if (c < 0x80) k = 0;
            else if ((c & 0xE0) == 0xC0 && c >= 0xC2) k = 1;
            else if ((c & 0xF0) == 0xE0) k = 2;
            else if ((c & 0xF8) == 0xF0 && c <= 0xF4) k = 3;
            else return false;
            if (k) {
                unsigned char c1 = end - p > 1 ? static_cast<unsigned char>(p[1]) : 0;
                if ((c == 0xE0 && c1 < 0xA0) || (c == 0xED && c1 > 0x9F) || (c == 0xF0 && c1 < 0x90) ||
                    (c == 0xF4 && c1 > 0x8F))
                    return false;  // overlong, surrogate or beyond U+10FFFF
                for (int i = 1; i <= k; ++i)
                    if (end - p <= i || (static_cast<unsigned char>(p[i]) & 0xC0) != 0x80) return false;
            }
            for (int i = 0; i <= k; ++i) {
                if (cap) {
                    if (n + 1 >= cap) overflow = true;
                    else { out[n++] = p[i]; out[n] = '\0'; }
                }
            }
            p += k + 1;
        }
        return false;
    }
    // A number token; integer tokens exactly, others as double.
    bool number(Member* m) {
        const char* s = p;
        bool integral = true;
        if (p < end && *p == '-') ++p;
        if (p >= end) return false;
        if (*p == '0') ++p;
        else if (*p >= '1' && *p <= '9') digits();
        else return false;
        if (p < end && *p == '.') { ++p; integral = false; if (!digits()) return false; }
        if (p < end && (*p == 'e' || *p == 'E')) {
            ++p;
            integral = false;
            if (p < end && (*p == '+' || *p == '-')) ++p;
            if (!digits()) return false;
        }
        if (!m) return true;
        size_t len = static_cast<size_t>(p - s);
        if (integral) {
            bool neg = *s == '-';
            uint64_t v = 0;
            bool big = false;
            for (const char* q = s + (neg ? 1 : 0); q < p; ++q) {
                uint64_t dgt = static_cast<uint64_t>(*q - '0');
                if (v > (UINT64_C(9223372036854775807) - dgt) / 10) { big = true; break; }
                v = v * 10 + dgt;
            }
            m->kind = big ? Member::BigInt : Member::Int;
            m->i = neg ? -static_cast<int64_t>(v) : static_cast<int64_t>(v);
            return true;
        }
        char buf[128];
        if (len >= sizeof buf) { m->kind = Member::Other; return true; }
        std::memcpy(buf, s, len);
        buf[len] = '\0';
        m->kind = Member::Dbl;
        m->d = std::strtod(buf, nullptr);  // correctly rounded (newlib, glibc, UCRT)
        return true;
    }
    bool value(Member* m) {
        ws();
        if (p >= end) return false;
        char c = *p;
        if (c == '{' || c == '[') {
            if (m) m->kind = Member::Other;
            if (++depth > 32) return false;
            char close = c == '{' ? '}' : ']';
            ++p;
            ws();
            if (p < end && *p == close) { ++p; --depth; return true; }
            while (true) {
                if (c == '{') {
                    ws();
                    size_t n;
                    bool ov;
                    if (p >= end || *p != '"' || !string(nullptr, 0, n, ov)) return false;
                    ws();
                    if (p >= end || *p != ':') return false;
                    ++p;
                }
                if (!value(nullptr)) return false;
                ws();
                if (p < end && *p == ',') { ++p; continue; }
                if (p < end && *p == close) { ++p; --depth; return true; }
                return false;
            }
        }
        if (c == '"') {
            if (!m) { size_t n; bool ov; return string(nullptr, 0, n, ov); }
            m->kind = Member::Str;
            return string(m->s, sizeof m->s, m->s_len, m->s_long);
        }
        if (c == 't' || c == 'f') {
            bool t = c == 't';
            if (!lit(t ? "true" : "false")) return false;
            if (m) { m->kind = Member::Bool; m->b = t; }
            return true;
        }
        if (c == 'n') {
            if (!lit("null")) return false;
            if (m) m->kind = Member::Null;
            return true;
        }
        return number(m);
    }
};

// The whole payload must be one JSON object (plus whitespace); its members go into f.
bool parse_flat(const char* json, size_t len, Flat& f) {
    Scan s{json, json + len};
    s.ws();
    if (s.p >= s.end || *s.p != '{') return false;
    ++s.p;
    s.ws();
    if (s.p < s.end && *s.p == '}') {
        ++s.p;
    } else {
        while (true) {
            s.ws();
            Member tmp{};
            Member& m = f.n < Flat::kMax ? f.m[f.n] : tmp;
            m = Member{};
            if (s.p >= s.end || *s.p != '"' || !s.string(m.key, sizeof m.key, m.key_len, m.key_long)) return false;
            s.ws();
            if (s.p >= s.end || *s.p != ':') return false;
            ++s.p;
            if (!s.value(&m)) return false;
            if (f.n < Flat::kMax) ++f.n;
            else f.too_many = true;
            s.ws();
            if (s.p < s.end && *s.p == ',') { ++s.p; continue; }
            if (s.p < s.end && *s.p == '}') { ++s.p; break; }
            return false;
        }
    }
    s.ws();
    return s.p == s.end;
}

// ============================================================================ schema checks

bool str_is(const Member* m, const char* lit) {
    size_t n = std::strlen(lit);
    return m && m->kind == Member::Str && !m->s_long && m->s_len == n && std::memcmp(m->s, lit, n) == 0;
}

// The whole string must match (contract revision 10: "bot\n" is not the sender "bot").
bool src_match(const Member* m, bool allow_bot) {
    if (!m || m->kind != Member::Str || m->s_long) return false;
    size_t n = m->s_len;
    const char* c = m->s;
    if (allow_bot && n == 3 && std::memcmp(c, "bot", 3) == 0) return true;
    if (n != 11 || std::memcmp(c, "dash-", 5) != 0) return false;
    for (size_t i = 5; i < 11; ++i)
        if (!((c[i] >= '0' && c[i] <= '9') || (c[i] >= 'a' && c[i] <= 'f'))) return false;
    return true;
}

// JSON Schema "integer": an integer, or a float with no fraction (1.0, 3e2); never a boolean.
bool get_int(const Member* m, int64_t lo, int64_t hi, int64_t& out) {
    if (!m) return false;
    if (m->kind == Member::Int) {
        if (m->i < lo || m->i > hi) return false;
        out = m->i;
        return true;
    }
    if (m->kind == Member::Dbl) {
        double d = m->d;
        if (!std::isfinite(d) || d != std::floor(d) || d < static_cast<double>(lo) || d > static_cast<double>(hi))
            return false;
        out = static_cast<int64_t>(d);
        return true;
    }
    return false;  // BigInt is beyond every range the contract uses
}

bool get_num(const Member* m, double lo, double hi, double& out) {
    if (!m) return false;
    double d;
    if (m->kind == Member::Int) d = static_cast<double>(m->i);
    else if (m->kind == Member::Dbl) d = m->d;
    else return false;
    if (!std::isfinite(d) || d < lo || d > hi) return false;
    out = d;
    return true;
}

// Checks the version and the keys (unknown keys are rejected; all keys are required).
Parse open(const char* json, size_t len, Flat& f, int version, const char* const* keys, int nkeys) {
    if (!parse_flat(json, len, f) || f.too_many) return Parse::Invalid;
    int64_t v = 0;
    if (!get_int(f.find("v"), -9007199254740991LL, 9007199254740991LL, v)) return Parse::Invalid;
    if (v != version) return Parse::UnknownVersion;
    for (int i = 0; i < f.n; ++i) {
        bool known = false;
        for (int k = 0; k < nkeys && !known; ++k)
            known = !f.m[i].key_long && f.m[i].key_len == std::strlen(keys[k]) &&
                    std::memcmp(f.m[i].key, keys[k], f.m[i].key_len) == 0;
        if (!known) return Parse::Invalid;
    }
    for (int k = 0; k < nkeys; ++k)
        if (!f.find(keys[k])) return Parse::Invalid;
    return Parse::Ok;
}

bool header(const Flat& f, uint32_t& seq, int64_t& up) {
    int64_t s;
    if (!get_int(f.find("seq"), 0, kSeqMax, s) || !get_int(f.find("up"), 0, kUpMax, up)) return false;
    seq = static_cast<uint32_t>(s);
    return true;
}

void copy_str(const Member* m, char* dst, size_t cap) {
    size_t n = m->s_len < cap - 1 ? m->s_len : cap - 1;
    std::memcpy(dst, m->s, n);
    dst[n] = '\0';
}

}  // namespace

Parse parse_motor(const char* json, size_t len, CmdMotor& out) {
    static const char* const keys[] = {"v", "seq", "up", "l", "r", "ttl", "rs"};
    Flat f;
    Parse p = open(json, len, f, 1, keys, 7);
    if (p != Parse::Ok) return p;
    int64_t ttl, rs;
    if (!header(f, out.seq, out.up) || !get_num(f.find("l"), -1.0, 1.0, out.l) || !get_num(f.find("r"), -1.0, 1.0, out.r) ||
        !get_int(f.find("ttl"), 50, 1000, ttl) || !get_int(f.find("rs"), 0, kSeqMax, rs))
        return Parse::Invalid;
    out.ttl = ttl;
    out.rs = static_cast<uint32_t>(rs);
    return Parse::Ok;
}

Parse parse_manual(const char* json, size_t len, CmdManual& out) {
    static const char* const keys[] = {"v", "seq", "up", "l", "r", "ttl", "rs", "src"};
    Flat f;
    Parse p = open(json, len, f, 2, keys, 8);
    if (p != Parse::Ok) return p;
    int64_t ttl, rs;
    if (!header(f, out.seq, out.up) || !get_num(f.find("l"), -1.0, 1.0, out.l) || !get_num(f.find("r"), -1.0, 1.0, out.r) ||
        !get_int(f.find("ttl"), 50, 1000, ttl) || !get_int(f.find("rs"), 0, kSeqMax, rs) || !src_match(f.find("src"), false))
        return Parse::Invalid;
    out.ttl = ttl;
    out.rs = static_cast<uint32_t>(rs);
    copy_str(f.find("src"), out.src, sizeof out.src);
    return Parse::Ok;
}

Parse parse_mode(const char* json, size_t len, CmdMode& out) {
    static const char* const keys[] = {"v", "seq", "up", "m", "src"};
    Flat f;
    Parse p = open(json, len, f, 2, keys, 5);
    if (p != Parse::Ok) return p;
    if (!header(f, out.seq, out.up) || !src_match(f.find("src"), true)) return Parse::Invalid;
    const Member* m = f.find("m");
    if (str_is(m, "auto")) out.m = Mode::Auto;
    else if (str_is(m, "manual")) out.m = Mode::Manual;
    else if (str_is(m, "estop")) out.m = Mode::Estop;
    else return Parse::Invalid;
    copy_str(f.find("src"), out.src, sizeof out.src);
    return Parse::Ok;
}

Parse parse_sleep(const char* json, size_t len, CmdSleep& out) {
    static const char* const keys[] = {"v", "seq", "up", "s", "why"};
    Flat f;
    Parse p = open(json, len, f, 1, keys, 5);
    if (p != Parse::Ok) return p;
    const Member* s = f.find("s");
    const Member* why = f.find("why");
    if (!header(f, out.seq, out.up) || s->kind != Member::Bool) return Parse::Invalid;
    if (!str_is(why, "dark") && !str_is(why, "light") && !str_is(why, "user")) return Parse::Invalid;
    out.s = s->b;
    copy_str(why, out.why, sizeof out.why);
    return Parse::Ok;
}

Parse parse_pong(const char* json, size_t len, Pong& out) {
    static const char* const keys[] = {"v", "seq", "up", "ps", "pu"};
    Flat f;
    Parse p = open(json, len, f, 1, keys, 5);
    if (p != Parse::Ok) return p;
    int64_t ps;
    if (!header(f, out.seq, out.up) || !get_int(f.find("ps"), 0, kSeqMax, ps) || !get_int(f.find("pu"), 0, kUpMax, out.pu))
        return Parse::Invalid;
    out.ps = static_cast<uint32_t>(ps);
    return Parse::Ok;
}

}  // namespace msg
}  // namespace flybot
