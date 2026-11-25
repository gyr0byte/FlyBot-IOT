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

## 13. Messages: own strict JSON parser, cross-checked against the Python contract

`lib/flybot_io/messages.cpp` builds everything the robot publishes and parses everything it receives.
I started with ArduinoJson and dropped it: version 7.2 deliberately stores short decimals as 32-bit
floats to save memory, so `"l":0.33` arrived as 0.32999998; it is also more lenient than Python's
`json` (single quotes, trailing text, leading zeros). Every incoming message is a flat object, so the
firmware now checks the JSON grammar and UTF-8 exactly like Python's `json.loads`, extracts the
members itself (integers exactly, decimals with `strtod`, the last duplicate key wins as in Python), and
validates each schema by hand (types, ranges, enums, patterns, required and unknown keys). No heap.

- **Parsers:** 1 447 payloads (480 valid; the rest have missing keys, extra keys, wrong types, values out
  of range, wrong versions, bad patterns, broken JSON, invalid UTF-8, duplicate keys, `1.0` or `3e2` as
  integers and so on) get exactly the Python contract's verdict, and the valid ones decode to the same
  values.
- **Builders:** 1 280 messages built from random and out-of-range inputs all pass the Python schemas,
  and every number equals Python's `round()` of the clamped input (`firmware/tools/check_messages.py`).
  The builders cannot produce an invalid message: values are clamped to the contract ranges first.
- An unknown `v` is counted separately from other invalid messages (contract section 6 rule 2); both
  reset the leave-fallback count, as in the simulator.

## 14. Two Python-contract details mirrored, one deliberately not

- Python's `re.search` with `$` also matches before one trailing newline, so `"src":"bot\n"` is valid
  for the Python contract. The firmware mirrors it to agree exactly (proposal P2 below).
- Python's `json.loads` accepts `NaN` and `Infinity`, and the schema then accepts `"l": NaN`. The robot
  rejects them (a NaN wheel command is never right). This is the only verdict that differs; it is left
  out of the vectors and listed as proposal P1.

## 15. Task layout (plan step 6.5)

| Task | Core | Priority | Period | Owns |
|---|---|---|---|---|
| reflex | 1 | 23 (highest) | 5 ms (200 Hz, the simulator's step) | the Edge, the ultrasonic sensor, the motors |
| sensor | 1 | 10 | 50 ms (20 Hz) | odour channels, light, battery; publishes `sensors` |
| ui | 1 | 4 | 20 ms | buttons, RGB LED, buzzer, on-board LED |
| comms | 0 | 6 | 20 ms | Wi-Fi, MQTT, portal, NTP; publishes status, events, rewards, ping |
| console | 1 | 1 (Arduino loop) | 10 ms | serial commands |

The reflex task never waits on the network: inputs come through a queue it drains without blocking, and
everything it publishes leaves through another; MQTT messages are queued with `esp_mqtt_client_enqueue`,
which never blocks. The plan lists a separate motor task; I folded it into the reflex task, which writes
the motors right after each edge step, because a hand-off between tasks would add up to a tick of latency
to the escape. The plan's ultrasonic "about 40 Hz" became 25 Hz, the simulator's rate: the looming
thresholds were tuned at 25 Hz, and an HC-SR04 with no echo holds its output high for up to 38 ms, so
40 Hz readings would overlap. The ultrasonic is interrupt-timed (no `pulseIn`), so it never blocks.

## 16. Escape latency measured in microseconds

The edge computes `lat` in whole milliseconds from its own clock. On the robot, the reflex task replaces
it with the measured time from the end of the echo that completed the detection to the motor outputs
being written (0.1 ms resolution), which is what plan step 6.6 asks for. The console `status` shows it.

## 17. MQTT connection details

esp-mqtt reconnects by itself (every 2 s) while Wi-Fi is up. The last will must carry the uptime at
connect (contract 3.3), so it is rebuilt in `MQTT_EVENT_BEFORE_CONNECT` before every connection attempt.
Wi-Fi reconnects are paced by the comms task (1 s, doubling to 30 s). When Wi-Fi drops, the edge is told
at once with `fallback_on.why = wifi_lost` instead of waiting for the keep-alive. TLS is ready: tick TLS
in the portal (or set `SECRET_MQTT_TLS 1`) and put the broker's CA certificate in `secrets.h`; the robot
waits for NTP before a TLS connection, because certificate dates need the clock. NTP is used only for that
and for logs; no wall-clock time goes into a payload (contract section 2).

## 18. Setup portal

Built on the Arduino `WebServer` and `DNSServer` (a captive portal), with no extra library. It opens when
the robot has not joined Wi-Fi 30 s after power-on, when both buttons are held for 3 s at power-on, or
from the console. It stores the Wi-Fi network and password, the broker address, port and TLS, and
optionally a new MQTT password, in NVS (they then override `secrets.h`), and restarts. Passwords are never
shown back. **The motors stay off while the portal is open**, although the contract would let a robot
without a broker leave `boot` after 10 s and wander: someone is holding a phone next to it. Once Wi-Fi has
worked, losing it never opens the portal; the robot wanders in fallback as the contract says.

## 19. Smaller firmware choices

- **Boot ID** is drawn after `WiFi.mode(WIFI_STA)`: `esp_random()` is only truly random with the radio on.
- **Rewards pressed while offline are dropped** (with an error beep), not queued: a reward delivered
  later would train whatever the robot is doing then. The offline queue (32 entries; the contract asks
  for at least 20) holds events, which keep the `up` of when they happened.
- **Motors:** 20 kHz PWM, coast at zero, standby (STBY low) in boot, e-stop, sleep, battery cut-off and
  while the portal is open. The duty cap is applied by the edge mixer (as in the simulator) and again in
  the PWM layer. `MOTOR_*_INVERT`, `MOTOR_TRIM_*` and `MOTOR_MIN_DUTY` in config.h are for bring-up.
- **Mood colours** (plan 6.9): boot white pulse, cloud green, manual cyan, escape red, fallback amber
  (purple when also offline), sleep dim blue pulse, e-stop red blink, portal blue blink, battery cut dim
  red, low battery an orange flash every 2 s. "Hungry" cannot be shown: see proposal P3.
- **Buzzer:** boot, connected, escape, Wi-Fi lost, low battery (every 30 s), cut-off, reward, punish,
  error, portal. Passive piezo by default (`BUZZER_PASSIVE`).
- **Partition table `min_spiffs.csv`**: two 1.9 MB app slots (room for over-the-air updates, plan 6.14)
  instead of the default 1.25 MB, which the firmware already filled to 78 %.
- **A missing `secrets.h`** still builds (with the template's placeholders): the console says so and the
  portal opens. A fresh clone stays buildable.
- **Watchdog:** the task watchdog (5 s, restart on timeout) watches the reflex, sensor, ui and comms
  tasks; the console command `freeze` stops feeding it to test the restart (reset reason `watchdog`).
- **Encoders** (optional, off by default) only count pulses for the console; there is no contract field.

## Contract proposals (not implemented; for your decision)

- **P1. Reject NaN and Infinity in `Contract.decode`.** Python's `json.loads` accepts them by default, so
  `{"l": NaN}` passes the `cmd-motor` schema (NaN fails no `minimum` or `maximum` check). Suggest
  `json.loads(..., parse_constant=...)` raising, so every receiver agrees with the firmware.
- **P2. Anchor the sender-name patterns exactly.** In Python, `^(bot|dash-[0-9a-f]{6})$` also matches
  `"bot\n"` (`$` matches before a trailing newline). Suggest validating `src` with a full match (or
  `\Z`), and noting in the contract that sender names never contain control characters.
- **P3. Let the robot know the brain's mood if "hungry" should show on the LED.** Plan step 6.9 lists a
  "hungry" colour, but hunger lives in `brain/state`, which the `esp32` user may not read (section 4).
  Options: allow `esp32` to read `brain/state`, or add an optional mood field to `cmd/motor`. Without a
  change the LED shows the robot's own states only.
