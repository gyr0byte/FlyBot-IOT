// Port of sim/flybot_sim/edge_sensing.py, statement for statement.
#include "sensing.h"

#include <algorithm>
#include <cmath>

#include "py_compat.h"

namespace flybot {

void OdourChannel::restart() {
    start_ms_.reset();
    last_ms_.reset();
    baseline_.reset();
    smoothed_.reset();
    win_n_ = 0;
}

Opt<double> OdourChannel::feed(double volts, int64_t now_ms) {
    if (!start_ms_) start_ms_ = now_ms;
    int64_t dt = !last_ms_ ? 0 : now_ms - *last_ms_;
    last_ms_ = now_ms;
    smoothed_ = !smoothed_ ? volts : *smoothed_ + cfg_.odour_smooth_alpha * (volts - *smoothed_);

    if (!baseline_) {
        if (win_n_ == kWindow) {  // full: drop the oldest
            for (int i = 1; i < kWindow; ++i) { win_t_[i - 1] = win_t_[i]; win_v_[i - 1] = win_v_[i]; }
            --win_n_;
        }
        win_t_[win_n_] = now_ms;
        win_v_[win_n_] = *smoothed_;
        ++win_n_;
        int drop = 0;  // keep readings within the window (times only increase)
        while (drop < win_n_ && now_ms - win_t_[drop] > cfg_.odour_baseline_window_ms) ++drop;
        if (drop) {
            for (int i = drop; i < win_n_; ++i) { win_t_[i - drop] = win_t_[i]; win_v_[i - drop] = win_v_[i]; }
            win_n_ -= drop;
        }
        if (now_ms - *start_ms_ < cfg_.odour_warmup_ms) return none;
        NeumaierSum s;
        for (int i = 0; i < win_n_; ++i) s.add(win_v_[i]);
        baseline_ = s.result() / static_cast<double>(win_n_);
        win_n_ = 0;
    }

    double value = (*smoothed_ - *baseline_) / cfg_.odour_full_scale_v;
    // Slow drift correction: follow the baseline only in clean air, faster if below it.
    if (dt > 0) {
        if (*smoothed_ < *baseline_) {
            double k = 1.0 - std::exp(static_cast<double>(-dt) / static_cast<double>(cfg_.odour_baseline_down_tau_ms));
            baseline_ = *baseline_ + k * (*smoothed_ - *baseline_);
        } else if (value < cfg_.odour_clean_threshold) {
            double k = 1.0 - std::exp(static_cast<double>(-dt) / static_cast<double>(cfg_.odour_baseline_tau_ms));
            baseline_ = *baseline_ + k * (*smoothed_ - *baseline_);
        }
    }
    return py_min(py_max(value, 0.0), 1.0);
}

Opt<double> DistanceFilter::feed(Opt<double> cm, int64_t now_ms) {
    if (cm) {
        t_[n_] = now_ms;
        d_[n_] = *cm;
        ++n_;
    }
    int keep = 0;  // recent = [(t, d) for (t, d) in recent if now - t <= valid]
    for (int i = 0; i < n_; ++i) {
        if (now_ms - t_[i] <= cfg_.dist_valid_ms) {
            t_[keep] = t_[i];
            d_[keep] = d_[i];
            ++keep;
        }
    }
    n_ = keep;
    int n_max = static_cast<int>(std::min<int64_t>(cfg_.dist_median_n, int64_t(kMax)));
    if (n_ > n_max) {  // recent = recent[-n:]
        int drop = n_ - n_max;
        for (int i = drop; i < n_; ++i) { t_[i - drop] = t_[i]; d_[i - drop] = d_[i]; }
        n_ = n_max;
    }
    if (n_ == 0) return none;
    double vals[kMax + 1];
    std::copy(d_, d_ + n_, vals);
    std::sort(vals, vals + n_);
    return vals[n_ / 2];
}

double BatteryMonitor::feed(double volts) {
    volts_ = !volts_ ? volts : *volts_ + cfg_.battery_smooth_alpha * (volts - *volts_);
    return *volts_;
}

}  // namespace flybot
