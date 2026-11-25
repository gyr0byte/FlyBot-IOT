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

## 5. Edge logic: a separate pure C++ library, ported statement by statement

`firmware/lib/flybot_edge/` holds `edge.cpp` (state machine, watchdog, fallback, looming, habituation,
sleep, e-stop, motor mixer), `sensing.cpp` (odour channel, distance filter, battery monitor) and
`streams.cpp` (contract section 6 sequence rules). They use no Arduino calls and no heap after
construction, so they run on the PC under test. Functions keep the Python names and statement order,
and every floating-point expression keeps Python's operation order (doubles throughout; the ESP32
emulates doubles in software, which costs microseconds at 200 Hz). Randomness is injected
(`esp_random()` on the robot, recorded values in the tests).

## 6. Fixed memory instead of Python's growing lists

Python's lists become fixed arrays: 16 sequence streams (when full, the least recently seen one is
forgotten early; rule 13 would forget it after 10 minutes anyway), 32 pending events, the odour
warm-up window holds 128 readings (1 s at up to 128 Hz; the robot reads at 20 Hz), the distance median
at most 16. Within those limits the behaviour is identical.

## 7. Python-exact arithmetic

Three Python details would otherwise make the port drift:
- `round(x)` and `round(x, n)` round exact halves to even and decide on the exact binary value
  (`round(2.675, 2)` is 2.67). `py_round` reproduces this with an exact product (Dekker's
  algorithm) instead of `x * 100` (which rounds twice).
- Python 3.12's `sum()` of floats uses Neumaier compensated summation (`sum([0.1] * 10) == 1.0`), so
  the odour baseline and the approach speed use the same algorithm (`NeumaierSum`).
- The build uses `-ffp-contract=off` so the compiler never fuses a multiply and an add (Python
  never does).

## 8. One Python quirk reproduced on purpose

`fallback_off.dur` is `now - (self.fallback_since or now)` in Python, so a fallback that started at
exactly `up = 0` reports 0. The port does the same to stay bit-identical; on the robot fallback can
never start at 0 because boot holds for at least the warm-up.

## 9. `on_link(false)` takes an optional reason

The contract allows `fallback_on.why = wifi_lost`, but the simulator only ever reports `mqtt_lost`.
`Edge::on_link(up, now, why_lost = MqttLost)` keeps the simulator's behaviour by default; the firmware
passes `WifiLost` when the Wi-Fi itself dropped, so the event says what happened.

## 10. PC tests: the installed MinGW GCC 6.3, C++14, SSE2

There is no host compiler in the project's toolchain. I downloaded a current MinGW-w64 GCC to
`D:\Tools\mingw64`, but this session's sandbox refused to execute a freshly downloaded compiler, so the
native tests use the MinGW GCC 6.3 already installed at `C:\MinGW` (only run, nothing installed or
written on C:; build output goes to `firmware\.pio`). Consequences:
- GCC 6 has no `std::optional`, so the library uses its own small `Opt<T>` and builds as C++14 (the
  ESP32 build uses C++17).
- That compiler is 32-bit, whose default x87 arithmetic uses 80-bit intermediates; the native
  environment builds with `-msse2 -mfpmath=sse` to get the same IEEE doubles as Python.
To run them: put a gcc on `PATH` (e.g. `$env:PATH = "C:\MinGW\bin;$env:PATH"`) and run
`pio test -e native`. `D:\Tools\mingw64` (GCC 14) also works if you allow it; delete it otherwise.

## 11. Cross-check vectors: recorded from the Python module, committed to the repository

`firmware/tools/gen_vectors.py` (run with the simulator's venv) wraps the Python `Edge`, `OdourChannel`,
`DistanceFilter` and `BatteryMonitor` in recording subclasses and logs every input call, every random
number drawn and every output. It produces:
- 4 **simulator runs** (odour seeking with a scripted fake brain that loses, duplicates and corrupts
  commands, goes silent, changes mode, sends sleep and drops the link; obstacles; the threat
  scenario; repeated looming for habituation), with their sensor processing;
- 9 **scripted scenarios** (boot, boot without broker, retained e-stop, cloud/fallback rules, wander,
  manual, looming with habituation and dishabituation, sleep, battery) plus a **boundaries** scenario
  that hits every threshold exactly (ttl window, obstacle 12 cm, wander 30/45 cm, turning hold-off,
  escape cooldown);
- 3 **random** scenarios (random inputs at random times), arithmetic vectors (`round`, `sum`), 6 000
  random stream-tracker operations and a scripted sensing file.

Doubles are stored as their 64-bit patterns (the PC test compiler's `strtod` is not guaranteed exact).
The vectors are committed (3.5 MB of text) so the C++ tests run without Python; rerun the generator
after any change to `edge.py`, `edge_sensing.py` or `edge_config.py`.

**Result:** 17 edge vectors with 100 040 control steps, 15 803 sensing steps, 8 972 arithmetic and
6 000 stream vectors: identical. Motor outputs, states, modes, events (every field), random-number use
and counters match bit for bit. The habituation level is the only value compared with a tolerance
(1e-12 relative), because it goes through `exp()` and C libraries may differ in the last bit; 3 of
those values differ in the last bit, none changes any decision.

## 12. The cross-check was itself tested by planting bugs

To make sure the vectors can fail, I planted 12 one-line bugs in the port, one at a time: `<` vs `<=`
in the fallback ttl window, the obstacle stop, the wander clear distance, the turning hold-off, the
watchdog, the escape cooldown, the distance window and the habituation threshold; naive rounding; a
plain instead of compensated mean for the odour baseline and for the approach speed; a shorter
approach window. 10 were caught (two only after I added the boundaries scenario and the scripted
sensing file, and made the odour comparison exact). The 2 not caught (plain sum in the approach speed,
approach window of 7) change only an internal speed estimate that never changed a decision in any
vector, so the outputs really are identical.
