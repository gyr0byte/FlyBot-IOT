// Hardware layer: the only code that touches pins. Pins and calibration live in config.h.
#pragma once
#include <cstdint>

#include "io_util.h"
#include "opt.h"

namespace hw {

void begin();

// --- analog (ADC1, 11 dB attenuation, eFuse-calibrated millivolts, oversampled) ---
double pin_volts(int pin);
double mq3_volts(int index);          // 0 = left, 1 = right: the sensor's output voltage (before the divider)
double battery_volts();               // pack voltage (before the divider), unsmoothed
flybot::Opt<double> light();          // 0 (dark) .. 1 (bright), none if no LDR is fitted

// --- ultrasonic HC-SR04: interrupt-timed echo, never blocks ---
void us_trigger();                    // start a measurement (skipped if the last echo is still high)
// True when a measurement has finished: cm is none for no echo (timeout or out of range);
// echo_end_us is when the echo ended (for the detection-to-motor latency).
bool us_poll(flybot::Opt<double>& cm, int64_t& echo_end_us);

// --- motors: TB6612, -1 .. +1 per wheel (already ramped and capped by the edge mixer) ---
void motors(double left, double right, bool enable);   // enable = false: standby (STBY low)

// --- RGB LED (WS2812B), on-board LED, buzzer ---
void led(flybot::Rgb c);
void status_led(bool on);
void tone(int hz);                    // 0 = silent

// --- buttons (active low) and optional encoders ---
bool reward_pressed();
bool punish_pressed();
void encoder_counts(long& left, long& right);

}  // namespace hw
