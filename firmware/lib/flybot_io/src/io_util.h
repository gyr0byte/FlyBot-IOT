// Small hardware-independent pieces of the robot's I/O, unit-tested on the PC:
//   RttWindow   median round-trip time over the last 10 s (status.rtt / status.crtt), as the simulator
//   SentLog     sensors seq -> robot time sent, for the sensor -> cmd/motor round trip (rs echo)
//   Debouncer   a push button: one press event per stable press
//   Buzzer      non-blocking tone patterns
//   mood_color  the RGB LED colour for the robot's state
#pragma once
#include <cstdint>

#include "edge.h"
#include "opt.h"

namespace flybot {

// sim.py _median: sorted values of the last window_ms, the element at len // 2, min(.., 60000),
// rounded to 1 decimal; empty -> none.
class RttWindow {
public:
    static constexpr int kCap = 256;  // 12.8 s of crtt at 20 Hz
    explicit RttWindow(int64_t window_ms = 10000) : window_ms_(window_ms) {}
    void add(int64_t t_ms, double rtt_ms);
    Opt<double> median(int64_t now_ms) const;
    int size() const { return n_; }

private:
    int64_t window_ms_;
    int64_t t_[kCap]{};
    double v_[kCap]{};
    int head_ = 0, n_ = 0;
};

// Remembers when each sensors message was sent; the first cmd/motor echoing its seq in rs gives the
// round trip (sim.py: the first answer only).
class SentLog {
public:
    static constexpr int kCap = 64;  // 3.2 s at 20 Hz: older answers are not counted
    void sent(uint32_t seq, int64_t t_ms);
    Opt<int64_t> answered(uint32_t rs);  // the send time, once; none if unknown or already used

private:
    uint32_t seq_[kCap]{};
    int64_t t_[kCap]{};
    bool used_[kCap]{};
    int head_ = 0, n_ = 0;
};

class Debouncer {
public:
    explicit Debouncer(int64_t debounce_ms = 30) : debounce_ms_(debounce_ms) {}
    // raw_pressed sampled at any rate; returns true once when a press has been stable for debounce_ms.
    bool update(bool raw_pressed, int64_t now_ms);
    bool pressed() const { return stable_; }
    int64_t pressed_since() const { return stable_since_; }

private:
    int64_t debounce_ms_;
    bool raw_ = false, stable_ = false;
    int64_t raw_since_ = 0, stable_since_ = 0;
};

enum class Tune : uint8_t { None, Boot, Connected, Escape, WifiLost, LowBattery, Cutoff, Reward, Punish, Error, Portal };

class Buzzer {
public:
    void play(Tune t, int64_t now_ms);
    // The frequency to sound now (0 = silent). Call often (every 20 ms is enough).
    int freq(int64_t now_ms);
    bool busy() const { return tune_ != Tune::None; }

private:
    Tune tune_ = Tune::None;
    int64_t start_ = 0;
};

struct Rgb { uint8_t r, g, b; };

struct MoodInput {
    State st;
    Mode m;
    bool connected;     // MQTT up
    bool battery_low;   // below the warning level
    bool battery_cut;   // motors disabled
    bool portal;        // setup portal open
};
// Colours (plan step 6.9): boot white pulse (warming up), cloud green (calm), manual cyan,
// escape red (scared), fallback amber (wandering; purple when also offline), sleep dim blue pulse,
// estop red blink, portal blue blink, battery cut dim red; low battery adds an orange flash every 2 s.
Rgb mood_color(const MoodInput& in, int64_t now_ms);

}  // namespace flybot
