// Python-exact arithmetic helpers, so the C++ edge logic reproduces sim/flybot_sim/edge.py bit for bit.
//
//  - py_max / py_min: Python's max(a, b) / min(a, b) (the first argument wins a tie).
//  - py_round_int(x): Python's round(x) for a float: nearest integer, exact halves to even.
//  - py_round(x, n): Python's round(x, n): decided on the exact binary value of x (no double
//    rounding of x * 10^n), exact decimal halves to even, result = nearest double to k / 10^n.
//  - NeumaierSum: Python 3.12+ sum() of floats (compensated summation, CPython bltinmodule.c).
//
// The whole project is built with -ffp-contract=off, so no multiply-add is fused (Python never fuses).
#pragma once
#include <cmath>
#include <cstdint>

namespace flybot {

inline double py_max(double a, double b) { return b > a ? b : a; }
inline double py_min(double a, double b) { return b < a ? b : a; }
inline int64_t py_max(int64_t a, int64_t b) { return b > a ? b : a; }

// round(x) -> int, half to even.
inline double py_round_int(double x) {
    double f = std::floor(x);
    double diff = x - f;  // exact for |x| < 2^52
    if (diff > 0.5) return f + 1.0;
    if (diff < 0.5) return f;
    return std::fmod(f, 2.0) == 0.0 ? f : f + 1.0;
}

namespace detail {
// Dekker / Veltkamp: a * b = p + e exactly (needs no fused multiply-add).
inline void two_prod(double a, double b, double& p, double& e) {
    const double split = 134217729.0;  // 2^27 + 1
    p = a * b;
    double c = split * a, ah = c - (c - a), al = a - ah;
    double d = split * b, bh = d - (d - b), bl = b - bh;
    e = ((ah * bh - p) + ah * bl + al * bh) + al * bl;
}
}  // namespace detail

// round(x, n) for n = 0 .. 15.
inline double py_round(double x, int n) {
    if (!std::isfinite(x)) return x;
    double p = 1.0;
    for (int i = 0; i < n; ++i) p *= 10.0;  // exact up to 10^22
    double hi, lo;
    detail::two_prod(x, p, hi, lo);         // x * p == hi + lo exactly
    double k = std::floor(hi);
    if (hi == k && lo < 0.0) k -= 1.0;      // now k <= x*p < k + 1
    double a = (hi - k) - 0.5;              // exact (see firmware-decisions.md, decision 7)
    bool up;
    if (a > 0.0) up = true;
    else if (a < 0.0) up = false;
    else if (lo > 0.0) up = true;
    else if (lo < 0.0) up = false;
    else up = std::fmod(k, 2.0) != 0.0;      // exact tie: to even
    double r = (up ? k + 1.0 : k) / p;
    return r == 0.0 ? std::copysign(0.0, x) : r;
}

// sum() of floats as CPython 3.12+ computes it (Neumaier compensated summation).
struct NeumaierSum {
    double f = 0.0, c = 0.0;
    bool started = false;
    void add(double x) {
        if (!started) { f = x; started = true; return; }  // 0 + first item, exactly the item
        double t = f + x;
        if (std::fabs(f) >= std::fabs(x)) c += (f - t) + x;
        else c += (x - t) + f;
        f = t;
    }
    double result() const {
        if (!started) return 0.0;
        return (c != 0.0 && std::isfinite(c)) ? f + c : f;
    }
};

}  // namespace flybot
