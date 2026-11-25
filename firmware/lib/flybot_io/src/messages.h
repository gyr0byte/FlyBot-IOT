// MQTT payloads per docs/message-contract.md (revision 9): builders for everything the robot
// publishes and strict validating parsers for everything it receives. Pure C++ with no heap use,
// so the PC tests check both against the Python contract (firmware/test/test_messages).
//
// Builders never produce an invalid message: every value is clamped to its contract range and
// rounded to its contract decimals exactly as Python's round() would. They return the length, or 0
// if the buffer is too small (kMaxPayload is always enough).
// Parsers follow the JSON schemas exactly (types, ranges, enums, patterns, required keys, no
// unknown keys) and return Invalid / UnknownVersion for anything else.
#pragma once
#include <cstddef>
#include <cstdint>

#include "edge.h"
#include "opt.h"

namespace flybot {
namespace msg {

constexpr size_t kMaxPayload = 256;

// Topic names relative to flybot/<robot>/
enum class Out : uint8_t { Sensors, Events, Status, Reward, Ping, kCount };
enum class In : uint8_t { CmdMotor, CmdManual, CmdMode, CmdSleep, Pong, kCount };
const char* topic_suffix(Out t);
const char* topic_suffix(In t);
bool in_topic_from_suffix(const char* suffix, In& out);

// Per-topic sequence numbers for what the robot sends (contract section 2: from 0, +1, wrap).
class SeqCounter {
public:
    uint32_t next(Out t) { return seq_[static_cast<int>(t)]++; }
    uint32_t peek(Out t) const { return seq_[static_cast<int>(t)]; }

private:
    uint32_t seq_[static_cast<int>(Out::kCount)] = {};
};

struct Header {
    uint32_t seq;
    int64_t up;
    uint32_t bid;  // 1 .. 4294967295
};

size_t build_sensors(char* buf, size_t cap, const Header& h, Opt<double> ol, Opt<double> orr, Opt<double> d,
                     Opt<double> lt, double bat);
size_t build_event(char* buf, size_t cap, const Header& h, const EdgeEvent& ev);
size_t build_boot_event(char* buf, size_t cap, const Header& h, const char* rst, const char* fw);

struct StatusInfo {
    State st;
    Mode m;
    int rssi;
    uint32_t heap;
    double bat;
    Opt<double> rtt, crtt;
    const char* fw;
    double hab;
};
size_t build_status(char* buf, size_t cap, const Header& h, const StatusInfo& s);
size_t build_offline(char* buf, size_t cap, const Header& h);  // last will: seq 0, up at connect
size_t build_reward(char* buf, size_t cap, const Header& h, int r);  // src "btn", with bid
size_t build_ping(char* buf, size_t cap, const Header& h);

enum class Parse : uint8_t { Ok, Invalid, UnknownVersion };
struct Pong { uint32_t seq; int64_t up; uint32_t ps; int64_t pu; };

Parse parse_motor(const char* json, size_t len, CmdMotor& out);
Parse parse_manual(const char* json, size_t len, CmdManual& out);
Parse parse_mode(const char* json, size_t len, CmdMode& out);
Parse parse_sleep(const char* json, size_t len, CmdSleep& out);
Parse parse_pong(const char* json, size_t len, Pong& out);

// Value formatting used by the builders (exposed for tests): Python round(v, dec), clamped to
// [lo, hi], printed with at least one decimal ("1.0", "0.214").
void append_fixed(char* buf, size_t cap, size_t& pos, double v, int dec, double lo, double hi);

}  // namespace msg
}  // namespace flybot
