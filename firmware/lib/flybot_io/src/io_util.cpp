#include "io_util.h"

#include <algorithm>

#include "py_compat.h"

namespace flybot {

// ---------------------------------------------------------------------------- RttWindow

void RttWindow::add(int64_t t_ms, double rtt_ms) {
    int i = (head_ + n_) % kCap;
    if (n_ == kCap) {
        i = head_;
        head_ = (head_ + 1) % kCap;
    } else {
        ++n_;
    }
    t_[i] = t_ms;
    v_[i] = rtt_ms;
}

Opt<double> RttWindow::median(int64_t now_ms) const {
    double vals[kCap];
    int k = 0;
    for (int j = 0; j < n_; ++j) {
        int i = (head_ + j) % kCap;
        if (now_ms - t_[i] <= window_ms_) vals[k++] = v_[i];
    }
    if (k == 0) return none;
    std::sort(vals, vals + k);
    return py_round(py_min(vals[k / 2], 60000.0), 1);
}

// ---------------------------------------------------------------------------- SentLog

void SentLog::sent(uint32_t seq, int64_t t_ms) {
    int i = (head_ + n_) % kCap;
    if (n_ == kCap) {
        i = head_;
        head_ = (head_ + 1) % kCap;
    } else {
        ++n_;
    }
    seq_[i] = seq;
    t_[i] = t_ms;
    used_[i] = false;
}

Opt<int64_t> SentLog::answered(uint32_t rs) {
    for (int j = n_ - 1; j >= 0; --j) {
        int i = (head_ + j) % kCap;
        if (seq_[i] == rs) {
            if (used_[i]) return none;
            used_[i] = true;
            return t_[i];
        }
    }
    return none;
}

// ---------------------------------------------------------------------------- Debouncer

bool Debouncer::update(bool raw_pressed, int64_t now_ms) {
    if (raw_pressed != raw_) {
        raw_ = raw_pressed;
        raw_since_ = now_ms;
    }
    if (raw_ != stable_ && now_ms - raw_since_ >= debounce_ms_) {
        stable_ = raw_;
        stable_since_ = now_ms;
        return stable_;  // an event only for a new press
    }
    return false;
}

// ---------------------------------------------------------------------------- Buzzer

namespace {
struct Note { int16_t hz; int16_t ms; };
const Note kBoot[] = {{880, 80}, {0, 40}, {1175, 80}, {0, 40}, {1760, 120}, {0, 0}};
const Note kConnected[] = {{1760, 60}, {0, 50}, {2349, 80}, {0, 0}};
const Note kEscape[] = {{2637, 50}, {0, 25}, {2637, 50}, {0, 0}};
const Note kWifiLost[] = {{440, 150}, {0, 100}, {330, 250}, {0, 0}};
const Note kLowBattery[] = {{1976, 70}, {0, 70}, {1976, 70}, {0, 70}, {1976, 70}, {0, 0}};
const Note kCutoff[] = {{262, 800}, {0, 0}};
const Note kReward[] = {{1568, 60}, {2093, 90}, {0, 0}};
const Note kPunish[] = {{392, 90}, {262, 120}, {0, 0}};
const Note kError[] = {{196, 200}, {0, 0}};
const Note kPortal[] = {{988, 100}, {0, 100}, {988, 100}, {0, 0}};

const Note* notes(Tune t) {
    switch (t) {
        case Tune::Boot: return kBoot;
        case Tune::Connected: return kConnected;
        case Tune::Escape: return kEscape;
        case Tune::WifiLost: return kWifiLost;
        case Tune::LowBattery: return kLowBattery;
        case Tune::Cutoff: return kCutoff;
        case Tune::Reward: return kReward;
        case Tune::Punish: return kPunish;
        case Tune::Error: return kError;
        case Tune::Portal: return kPortal;
        default: return nullptr;
    }
}
}  // namespace

void Buzzer::play(Tune t, int64_t now_ms) {
    tune_ = t;
    start_ = now_ms;
}

int Buzzer::freq(int64_t now_ms) {
    const Note* n = notes(tune_);
    if (!n) return 0;
    int64_t t = now_ms - start_;
    for (; n->ms > 0; ++n) {
        if (t < n->ms) return n->hz;
        t -= n->ms;
    }
    tune_ = Tune::None;
    return 0;
}

// ---------------------------------------------------------------------------- mood colours

namespace {
Rgb scale(Rgb c, int level) {  // level 0..255
    return {static_cast<uint8_t>(c.r * level / 255), static_cast<uint8_t>(c.g * level / 255),
            static_cast<uint8_t>(c.b * level / 255)};
}
int pulse(int64_t now_ms, int64_t period_ms, int lo) {  // triangle wave lo..255
    int64_t ph = now_ms % period_ms;
    int64_t half = period_ms / 2;
    int64_t x = ph < half ? ph : period_ms - ph;
    return lo + static_cast<int>((255 - lo) * x / half);
}
bool blink(int64_t now_ms, int64_t period_ms) { return (now_ms % period_ms) < period_ms / 2; }
}  // namespace

Rgb mood_color(const MoodInput& in, int64_t now_ms) {
    const Rgb off{0, 0, 0};
    if (in.portal) return blink(now_ms, 1000) ? Rgb{0, 0, 255} : off;
    if (in.battery_cut) return {60, 0, 0};
    if (in.battery_low && (now_ms % 2000) < 120) return {255, 80, 0};
    switch (in.st) {
        case State::Boot: return scale({255, 255, 255}, pulse(now_ms, 2000, 20));
        case State::Cloud: return {0, 255, 0};
        case State::Manual: return {0, 200, 255};
        case State::Escape: return {255, 0, 0};
        case State::Fallback: return in.connected ? Rgb{255, 140, 0} : Rgb{160, 0, 255};
        case State::Sleep: return scale({0, 0, 120}, pulse(now_ms, 4000, 40));
        case State::Estop: return blink(now_ms, 500) ? Rgb{255, 0, 0} : off;
    }
    return off;
}

}  // namespace flybot
