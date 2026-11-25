// Opt<T>: a minimal optional value (Python's "value or None") for trivially copyable T.
// Own type instead of std::optional so the library also builds as C++14 (the PC test compiler may
// be an older GCC); the subset used here behaves like std::optional.
#pragma once

namespace flybot {

struct NoneT {
    constexpr explicit NoneT(int) {}
};
constexpr NoneT none{0};

template <typename T>
class Opt {
public:
    constexpr Opt() : has_(false), v_() {}
    constexpr Opt(NoneT) : has_(false), v_() {}
    constexpr Opt(const T& v) : has_(true), v_(v) {}  // NOLINT: implicit, like std::optional
    Opt& operator=(NoneT) { has_ = false; return *this; }
    Opt& operator=(const T& v) { has_ = true; v_ = v; return *this; }

    constexpr bool has_value() const { return has_; }
    constexpr explicit operator bool() const { return has_; }
    const T& operator*() const { return v_; }
    T& operator*() { return v_; }
    const T* operator->() const { return &v_; }
    T* operator->() { return &v_; }
    constexpr T value_or(const T& d) const { return has_ ? v_ : d; }
    void reset() { has_ = false; }

    friend bool operator==(const Opt& a, const T& b) { return a.has_ && a.v_ == b; }

private:
    bool has_;
    T v_;
};

}  // namespace flybot
