#include "hw.h"

#include <Adafruit_NeoPixel.h>
#include <Arduino.h>
#include <soc/gpio_reg.h>
#include <soc/soc.h>
#include <esp_timer.h>

#include <cmath>

#include "config.h"

using namespace flybot;
using namespace flybot::cfg;

namespace hw {

namespace {

constexpr int kChLeft = 0;    // LEDC channels 0/1 share timer 0 (20 kHz motors)
constexpr int kChRight = 1;
constexpr int kChBuzzer = 4;  // timer 2: tones never change the motor frequency

Adafruit_NeoPixel g_pixels(MOOD_LED_COUNT, PIN_MOOD_LED, NEO_GRB + NEO_KHZ800);
Rgb g_last_led{1, 2, 3};
int g_last_hz = -1;

// ultrasonic, written by the echo interrupt
volatile int64_t g_rise_us = 0, g_fall_us = 0;
volatile bool g_done = false;
bool g_waiting = false;
int64_t g_trig_us = 0;

volatile long g_enc_left = 0, g_enc_right = 0;

static_assert(PIN_US_ECHO < 32, "the echo interrupt reads GPIO_IN_REG (pins 0-31)");

void IRAM_ATTR echo_isr() {
    int64_t t = esp_timer_get_time();  // IRAM-safe, like the register read (no flash access in the ISR)
    if (REG_READ(GPIO_IN_REG) & (1u << PIN_US_ECHO)) {
        g_rise_us = t;
    } else if (g_rise_us) {
        g_fall_us = t;
        g_done = true;
    }
}
void IRAM_ATTR enc_left_isr() { ++g_enc_left; }
void IRAM_ATTR enc_right_isr() { ++g_enc_right; }

void motor_one(int ch, int in1, int in2, double v, bool invert, double trim) {
    if (invert) v = -v;
    double a = std::fabs(v) * trim;
    if (a > 0.0 && MOTOR_MIN_DUTY > 0.0) a = MOTOR_MIN_DUTY + (1.0 - MOTOR_MIN_DUTY) * a;
    if (a > MOTOR_DUTY_CAP) a = MOTOR_DUTY_CAP;  // defence in depth: the mixer already capped it
    const uint32_t full = (1u << MOTOR_PWM_BITS) - 1u;
    uint32_t duty = static_cast<uint32_t>(std::lround(a * full));
    if (duty == 0) {  // coast
        digitalWrite(in1, LOW);
        digitalWrite(in2, LOW);
    } else {
        digitalWrite(in1, v > 0 ? HIGH : LOW);
        digitalWrite(in2, v < 0 ? HIGH : LOW);
    }
    ledcWrite(ch, duty);
}

}  // namespace

void begin() {
    analogReadResolution(12);
    for (int pin : {PIN_MQ3_LEFT, PIN_MQ3_RIGHT, PIN_LDR, PIN_BATTERY}) analogSetPinAttenuation(pin, ADC_11db);

    pinMode(PIN_US_TRIG, OUTPUT);
    digitalWrite(PIN_US_TRIG, LOW);
    pinMode(PIN_US_ECHO, INPUT);
    attachInterrupt(digitalPinToInterrupt(PIN_US_ECHO), echo_isr, CHANGE);

    pinMode(PIN_MOTOR_STBY, OUTPUT);
    digitalWrite(PIN_MOTOR_STBY, LOW);  // standby until the reflex task enables the motors
    for (int pin : {PIN_MOTOR_AIN1, PIN_MOTOR_AIN2, PIN_MOTOR_BIN1, PIN_MOTOR_BIN2}) {
        pinMode(pin, OUTPUT);
        digitalWrite(pin, LOW);
    }
    ledcSetup(kChLeft, MOTOR_PWM_HZ, MOTOR_PWM_BITS);
    ledcSetup(kChRight, MOTOR_PWM_HZ, MOTOR_PWM_BITS);
    ledcAttachPin(PIN_MOTOR_PWMA, kChLeft);
    ledcAttachPin(PIN_MOTOR_PWMB, kChRight);
    ledcWrite(kChLeft, 0);
    ledcWrite(kChRight, 0);

    g_pixels.begin();
    g_pixels.setBrightness(MOOD_LED_BRIGHTNESS);
    g_pixels.clear();
    g_pixels.show();
    pinMode(PIN_STATUS_LED, OUTPUT);
    digitalWrite(PIN_STATUS_LED, LOW);

    if (BUZZER_PASSIVE) {
        ledcSetup(kChBuzzer, 2000, 10);
        ledcAttachPin(PIN_BUZZER, kChBuzzer);
        ledcWrite(kChBuzzer, 0);
    } else {
        pinMode(PIN_BUZZER, OUTPUT);
        digitalWrite(PIN_BUZZER, LOW);
    }

    pinMode(PIN_BTN_REWARD, INPUT_PULLUP);
    pinMode(PIN_BTN_PUNISH, INPUT_PULLUP);

    if (ENCODERS_ENABLED) {
        pinMode(PIN_ENC_LEFT, INPUT_PULLUP);
        pinMode(PIN_ENC_RIGHT, INPUT_PULLUP);
        attachInterrupt(digitalPinToInterrupt(PIN_ENC_LEFT), enc_left_isr, RISING);
        attachInterrupt(digitalPinToInterrupt(PIN_ENC_RIGHT), enc_right_isr, RISING);
    }
}

// ---------------------------------------------------------------------------- analog

double pin_volts(int pin) {
    uint32_t sum = 0;
    for (int i = 0; i < ADC_OVERSAMPLE; ++i) sum += analogReadMilliVolts(pin);
    return sum / static_cast<double>(ADC_OVERSAMPLE) / 1000.0;
}

double mq3_volts(int index) { return pin_volts(index == 0 ? PIN_MQ3_LEFT : PIN_MQ3_RIGHT) * MQ3_DIVIDER; }

double battery_volts() { return pin_volts(PIN_BATTERY) * BATTERY_DIVIDER * BATTERY_CAL; }

Opt<double> light() {
    if (!LDR_FITTED) return none;
    double v = (pin_volts(PIN_LDR) - LDR_DARK_V) / (LDR_BRIGHT_V - LDR_DARK_V);
    return v < 0.0 ? 0.0 : v > 1.0 ? 1.0 : v;
}

// ---------------------------------------------------------------------------- ultrasonic

void us_trigger() {
    if (digitalRead(PIN_US_ECHO) == HIGH) return;  // the last echo is still running: skip this slot
    noInterrupts();
    g_rise_us = 0;
    g_done = false;
    interrupts();
    g_waiting = true;
    g_trig_us = esp_timer_get_time();
    digitalWrite(PIN_US_TRIG, HIGH);
    delayMicroseconds(10);
    digitalWrite(PIN_US_TRIG, LOW);
}

bool us_poll(Opt<double>& cm, int64_t& echo_end_us) {
    if (!g_waiting) return false;
    int64_t now = esp_timer_get_time();
    if (g_done) {
        g_waiting = false;
        double d = static_cast<double>(g_fall_us - g_rise_us) * US_CM_PER_US;
        cm = (d < US_MIN_CM || d > US_MAX_CM) ? Opt<double>() : Opt<double>(d);
        echo_end_us = g_fall_us;
        return true;
    }
    if (now - g_trig_us > US_TIMEOUT_US) {  // missed echo
        g_waiting = false;
        cm = none;
        echo_end_us = now;
        return true;
    }
    return false;
}

// ---------------------------------------------------------------------------- outputs

void motors(double left, double right, bool enable) {
    if (!enable) left = right = 0.0;
    motor_one(kChLeft, PIN_MOTOR_AIN1, PIN_MOTOR_AIN2, left, MOTOR_LEFT_INVERT, MOTOR_TRIM_LEFT);
    motor_one(kChRight, PIN_MOTOR_BIN1, PIN_MOTOR_BIN2, right, MOTOR_RIGHT_INVERT, MOTOR_TRIM_RIGHT);
    digitalWrite(PIN_MOTOR_STBY, enable ? HIGH : LOW);
}

void led(Rgb c) {
    if (c.r == g_last_led.r && c.g == g_last_led.g && c.b == g_last_led.b) return;  // show() only on change
    g_last_led = c;
    for (int i = 0; i < MOOD_LED_COUNT; ++i) g_pixels.setPixelColor(i, g_pixels.Color(c.r, c.g, c.b));
    g_pixels.show();
}

void status_led(bool on) { digitalWrite(PIN_STATUS_LED, on ? HIGH : LOW); }

void tone(int hz) {
    if (hz == g_last_hz) return;
    g_last_hz = hz;
    if (BUZZER_PASSIVE) {
        if (hz > 0) ledcWriteTone(kChBuzzer, hz);
        else ledcWrite(kChBuzzer, 0);
    } else {
        digitalWrite(PIN_BUZZER, hz > 0 ? HIGH : LOW);
    }
}

bool reward_pressed() { return digitalRead(PIN_BTN_REWARD) == LOW; }
bool punish_pressed() { return digitalRead(PIN_BTN_PUNISH) == LOW; }

void encoder_counts(long& left, long& right) {
    left = g_enc_left;
    right = g_enc_right;
}

}  // namespace hw
