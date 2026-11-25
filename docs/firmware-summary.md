# Firmware summary (Phase 6, 2026-11-25)

Autonomous session: the ESP32 firmware is written, tested on the PC and compiled for `esp32dev`.
**It has not been flashed: there is no hardware yet.** Nothing pushed, everything on D:. Every choice is in
[firmware-decisions.md](firmware-decisions.md).

**Update (same day):** the pins now follow the component sheet ([pin-map.md](pin-map.md), with the
differences from my first version and what they changed). Proposals P1 and P2 were approved and are in
the contract code (contract revision 10); P3 was dropped (the LED shows robot-side states only, the
dashboard shows hunger) and is kept as a later idea.

## What was built

| Part | What | Where |
|---|---|---|
| Project | PlatformIO, `esp32dev` (Arduino-ESP32 2.0.17 / ESP-IDF 4.4, esp-mqtt) and `native` (PC tests); all versions pinned; `config.h` with every pin and threshold (section B = `edge_config.py`, checked by a tool); `secrets.example.h` | `firmware/platformio.ini`, `include/` |
| Edge logic | Pure C++ port of the simulator's edge module, statement by statement: boot hold during MQ-3 warm-up, state machine, command TTL and 500 ms watchdog, fallback wander with obstacle avoidance, leaving after 3 good commands, looming escape from time-to-contact with turning suppression and the "appeared" rule, habituation with the relative rule, sleep, e-stop, the §5.1 defaults, motor mixer; odour channel, distance filter, battery monitor; §6 sequence rules. Python-exact arithmetic (`round`, `sum`, no fused multiply-add) | `firmware/lib/flybot_edge/` |
| Messages | Builders for everything the robot sends (values clamped and rounded as Python does) and strict parsers for everything it receives (JSON grammar, UTF-8, every schema rule) | `firmware/lib/flybot_io/` |
| Hardware | MQ-3 through dividers on ADC1 (oversampled, calibrated), interrupt-timed HC-SR04 with timeout and median filter, light, battery, TB6612 PWM with standby and duty cap, WS2812B mood colours, buzzer tunes, debounced buttons, optional encoders | `firmware/src/hw.cpp`, `tasks.cpp` |
| Tasks | Reflex task 200 Hz (core 1, highest priority; owns the edge, ultrasonic and motors; never waits on the network), sensor task 20 Hz, UI task, comms task on core 0, serial console | `firmware/src/tasks.cpp`, `net.cpp`, `console.cpp` |
| Networking | Wi-Fi with backoff; captive setup portal for Wi-Fi and broker (saved to NVS, no reflash); boot ID after the radio starts; NTP; esp-mqtt QoS 1, last will refreshed at each connect, TLS-ready; sensors 20 Hz, status on change and every 10 s, ping/pong and sensor→command round trips, events queued while offline, boot event, rewards from the buttons; low-battery warning and cut-off; task watchdog | `firmware/src/net.cpp`, `portal.cpp`, `settings.cpp` |
| Docs | Bring-up checklist (buck converter to 5.0 V first, MQ-3 left/right, each sensor, motors off the ground, MQTT against the laptop broker, the full robot with the brain) | [hardware-bringup.md](hardware-bringup.md) |

## Test results

| Check | Result |
|---|---|
| PC tests (`pio test -e native`) | **32 passed** (19 unit, 4 cross-check, 9 message) |
| Cross-check with the Python edge module | **identical**: 17 edge vectors, **100 040 control steps** (4 simulator runs with a scripted fake brain, 10 scripted scenarios incl. every threshold hit exactly, 3 random), 15 803 sensing steps, 8 972 `round`/`sum` vectors, 6 000 stream operations. Motor outputs, states, events (every field), random-number use and counters match bit for bit; only the habituation level (via `exp()`) is compared within 1e-12, and 3 values differ in the last bit |
| Cross-check self-test | 10 of 12 planted one-line bugs are caught; the other 2 change only an internal estimate that never affects an output (decision 12) |
| Incoming messages vs the Python contract | **1 507 payloads** (incl. NaN/Infinity and trailing-newline sender names), same verdict and values |
| Outgoing messages vs the Python contract | **1 280 C++-built messages** valid, every number equal to Python's `round()` |
| `config.h` vs `edge_config.py` | 49 thresholds, 1 documented difference (MQ-3 warm-up 60 s instead of the simulator's 5 s) |
| esp32dev build | clean, **0 warnings** |
| Existing suites | brain **390 passed**, simulator **288 passed** (one timing test failed once while both suites ran at the same time on the shared broker; it passed alone and the full suite passed on its own) |

## Flash and RAM (esp32dev)

| | Used | Of | |
|---|---|---|---|
| Flash (app partition, `min_spiffs.csv`) | 1 021 021 bytes | 1 966 080 | **51.9 %** |
| RAM (static: data + bss) | 76 584 bytes | 327 680 | **23.4 %** |
| Sections | text 861 017, data 182 320, bss 51 225 bytes | | |

The default partition table would have been 77.9 % full (1.25 MB app slot); `min_spiffs.csv` keeps two
1.9 MB slots, so over-the-air updates (plan 6.14) still fit. Stack use per task can only be measured on
the board (the console can be extended with high-water marks once it runs).

## What can only be verified on real hardware

- That it flashes, boots and the tasks start; stack sizes and free heap at run time.
- **The MQ-3 left/right feasibility test**, warm-up length, baselines, `ODOUR_FULL_SCALE_V`.
- ADC readings and divider ratios (`BATTERY_CAL`, LDR levels), HC-SR04 accuracy and missed echoes at 25 Hz.
- Motor direction, trim, minimum duty, current draw, the duty cap's effect; `EGO_SPEED_CM_S` (the speed at
  output 1.0, used to subtract the robot's own motion from looming), which the simulator only assumes.
- Reflex timing with Wi-Fi off (plan 6.5), the real detection-to-motor latency (`lat`), whether a brisk
  hand from 60 cm triggers the escape reliably, and whether turning toward walls stays silent.
- Wi-Fi reconnect behaviour, the last will within about 8 s, the setup portal on a phone, TLS (Phase 11).
- WS2812B at 3.3 V data level, buzzer type, button wiring, the watchdog restart.
- Battery run time and the low-battery cut-off on a real pack.

All of these are steps in [hardware-bringup.md](hardware-bringup.md), with a results table for the report.

## Decisions (details in [firmware-decisions.md](firmware-decisions.md))

1. Pin map from the component sheet ([pin-map.md](pin-map.md)); it replaced the one I had derived.
2. Arduino-ESP32 2.0.17 (`espressif32@6.9.0`), not 3.x.
3. PlatformIO in `D:\Tools\pio-venv`, `PLATFORMIO_CORE_DIR=D:\Tools\platformio`.
4. `config.h` as typed constants, checked against `edge_config.py`; MQ-3 warm-up 60 s on the robot.
5. Edge logic as a separate pure C++ library, ported statement by statement.
6. Fixed-size containers instead of Python's growing lists (identical within their limits).
7. Python-exact `round`, `sum` and no fused multiply-add.
8. One Python quirk reproduced on purpose (`fallback_off.dur` when fallback starts at 0).
9. `on_link(false)` can report `wifi_lost` (the simulator only ever says `mqtt_lost`).
10. PC tests with the MinGW GCC 6.3 already installed (C++14, SSE2): the sandbox refused to run the GCC 14 I downloaded.
11. Cross-check vectors recorded from the Python module and committed (3.5 MB).
12. The cross-check was tested by planting bugs.
13. Own strict JSON parser (ArduinoJson stores short decimals as 32-bit floats).
14. NaN/Infinity rejected and sender names matched exactly, now the same in the contract (revision 10).
15. Task layout; motor writes folded into the reflex task; ultrasonic at 25 Hz as in the simulator.
16. Escape latency measured in microseconds on the robot.
17. MQTT: esp-mqtt reconnects, last will rebuilt before each connect, Wi-Fi loss reported at once, NTP before TLS.
18. Setup portal (captive, NVS); motors off while it is open; opened by holding both buttons for 3 s in
    the first minute after boot (not at power-on: the buttons are on boot-sensitive GPIO 14 / 15).
19. Smaller choices: boot ID after the radio, offline rewards dropped, motor standby, mood colours,
    buzzer tunes, `min_spiffs.csv`, build without `secrets.h`, watchdog, encoders console-only.

**Contract proposals:** P1 (reject NaN/Infinity) and P2 (whole-string patterns) approved and done in
contract revision 10; P3 ("hungry" on the LED) dropped for now and kept as a later idea.

## Commits (local, not pushed)

| Commit | What |
|---|---|
| `7fc4516` | chore(firmware): PlatformIO project, config.h and secrets template (step 1) |
| `c09017f` | feat(firmware): hardware-independent edge logic ported from the simulator (step 2) |
| `f26f6be` | test(firmware): cross-check the C++ edge port against the Python reference (step 3) |
| `c586c5c` | feat(firmware): contract messages and I/O helpers, cross-checked with Python (steps 4–5) |
| `0562ca5` | feat(firmware): hardware layer, FreeRTOS tasks, Wi-Fi/MQTT and setup portal (steps 4–5) |
| `6539d28` | docs: hardware bring-up checklist (step 7) |

Plus this summary and the README status (committed last).

## Housekeeping on D:

- `D:\Tools\mingw64` (GCC 14, 11 680 files unpacked) and `D:\Tools\downloads\winlibs-gcc14.zip` (244 MB)
  are unused because the sandbox would not run them. Delete both, or allow them and use
  `D:\Tools\mingw64\bin` instead of `C:\MinGW\bin` for the PC tests (then `std::optional` and C++17 would
  also work on the PC). I did not delete them myself.
- `D:\Tools\pio-venv` and `D:\Tools\platformio` hold PlatformIO and the ESP32 toolchain (needed to build).
