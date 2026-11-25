// FIRMWARE sensor processing: port of sim/flybot_sim/edge_sensing.py (plan step 6.2).
//   OdourChannel:   MQ-3 voltage -> change from this sensor's own baseline, scaled 0-1 (empty until
//                   warm-up and calibration finish), smoothing and slow baseline-drift correction.
//   DistanceFilter: raw echo distances -> median of recent valid readings, or empty.
//   BatteryMonitor: smoothed pack voltage.
// Pure C++, fixed memory.
#pragma once
#include <cstdint>
#include "opt.h"

#include "edge_config.h"

namespace flybot {

class OdourChannel {
public:
    explicit OdourChannel(const EdgeConfig& cfg) : cfg_(cfg) {}
    // Process one reading (sensor volts); the 0-1 value, or empty while warming up.
    Opt<double> feed(double volts, int64_t now_ms);
    bool ready() const { return baseline_.has_value(); }
    Opt<double> baseline() const { return baseline_; }
    Opt<double> smoothed() const { return smoothed_; }
    void restart();  // start warm-up and calibration again (console "cal")
    // Window capacity: 1 s at up to 128 Hz; older readings beyond it are dropped first.
    static constexpr int kWindow = 128;

private:
    const EdgeConfig& cfg_;
    Opt<int64_t> start_ms_, last_ms_;
    Opt<double> baseline_, smoothed_;
    int64_t win_t_[kWindow]{};
    double win_v_[kWindow]{};
    int win_n_ = 0;
};

class DistanceFilter {
public:
    explicit DistanceFilter(const EdgeConfig& cfg) : cfg_(cfg) {}
    Opt<double> feed(Opt<double> cm, int64_t now_ms);
    static constexpr int kMax = 16;  // dist_median_n may be at most this

private:
    const EdgeConfig& cfg_;
    int64_t t_[kMax + 1]{};
    double d_[kMax + 1]{};
    int n_ = 0;
};

class BatteryMonitor {
public:
    explicit BatteryMonitor(const EdgeConfig& cfg) : cfg_(cfg) {}
    double feed(double volts);
    Opt<double> volts() const { return volts_; }

private:
    const EdgeConfig& cfg_;
    Opt<double> volts_;
};

}  // namespace flybot
