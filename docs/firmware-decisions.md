# Firmware decisions (Phase 6)

Built autonomously, so every choice I would normally have asked you about is recorded here with the
options and the reason. Nothing here changes the message contract; contract proposals are in their own
section at the end and are **not** implemented.

## 1. Pin map: none found, so one was derived (please check)

The task says "pins exactly as the Pin Map in docs", and the plan mentions a "Pin Map tab", but there is
no pin map in the repository, the docs or earlier sessions (searched for "pin map", GPIO numbers, TB6612,
WS2812 and spreadsheet files). So I derived one from the ESP32 rules and kept it in one block at the
top of `firmware/include/config.h` (section A), so replacing it with yours is a ten-line change.

Rules followed: analog only on ADC1 (GPIO 32–39; ADC2 cannot be read while Wi-Fi is on); the four
analog sensors on the input-only pins 34–39; no flash pins (6–11), no USB-serial pins (1, 3); no motor
outputs on strapping pins (0, 2, 5, 12, 15) or on GPIO 14, which outputs a PWM signal during boot.

| Function | GPIO | Notes |
|---|---|---|
| MQ-3 left AO | 36 (VP, ADC1_CH0) | via divider 10 k / 20 k (5 V → 3.33 V) |
| MQ-3 right AO | 39 (VN, ADC1_CH3) | via divider 10 k / 20 k |
| LDR | 34 (ADC1_CH6) | LDR from 3.3 V, 10 k to GND |
| Battery | 35 (ADC1_CH7) | via divider 100 k / 33 k (8.4 V → 2.08 V) |
| HC-SR04 trigger / echo | 18 / 19 | echo through a 1 k / 2 k divider (5 V → 3.3 V) |
| TB6612 PWMA, AIN1, AIN2 (left) | 25, 26, 27 | |
| TB6612 PWMB, BIN1, BIN2 (right) | 32, 33, 4 | |
| TB6612 STBY | 23 | 10 k pull-down so the motors stay off until the firmware runs |
| WS2812B data | 16 | 330 Ω series resistor |
| Buzzer | 17 | |
| Reward / punish buttons | 21 / 22 | to GND, internal pull-ups |
| On-board LED | 2 | |
| Encoders (optional) | 13 / 14 | |

**If your Pin Map differs, edit section A of `config.h` and the table in `docs/hardware-bringup.md`;
nothing else refers to pin numbers.**

## 2. Arduino-ESP32 2.0.17 (platform `espressif32@6.9.0`), not 3.x

PlatformIO's official `espressif32` platform ships Arduino-ESP32 2.0.17 on ESP-IDF 4.4.7. Arduino 3.x
needs the community "pioarduino" platform fork. 2.0.17 is the mature, widely used line; it includes
ESP-IDF's `esp-mqtt` (`mqtt_client.h`, QoS 1, last will, TLS) as the plan requires. Versions are pinned
(`espressif32@6.9.0`, `ArduinoJson@7.2.1`, `Adafruit NeoPixel@1.12.3`, `native@1.2.1`).

## 3. Tools stay on D:

PlatformIO Core 6.1.18 in its own venv `D:\Tools\pio-venv`, with `PLATFORMIO_CORE_DIR=D:\Tools\platformio`
(platforms, the Xtensa toolchain, the Arduino framework and libraries go there), pip cache
`D:\Tools\cache\pip`, temp `D:\Tools\tmp`. Build output is in `firmware/.pio/` (git-ignored).

## 4. config.h: typed constants, edge thresholds checked against the simulator

Constants are `constexpr` values in `namespace flybot::cfg`, not `#define`s, so they have types, cannot
clash with framework macros (Arduino-ESP32 already defines `RGB_BRIGHTNESS`, which is why the LED
constants are called `MOOD_LED_*`), and the hardware-independent code and the PC tests can include the
same header. Section B has every field of `edge_config.py` under the same name in upper case.
`firmware/tools/check_config.py` compares them and fails on any difference not listed with a reason.

The only difference: **`ODOUR_WARMUP_MS` is 60 000 ms on the robot** (the simulator uses 5 000 ms and
says it is shortened for development). A real MQ-3 needs about a minute after power-on before its
baseline is meaningful (much longer the very first time; see the bring-up checklist). The robot holds
still in `boot` meanwhile, as the contract requires.
