# Firmware decisions (Phase 6)

Built autonomously, so every choice I would normally have asked you about is recorded here with the
options and the reason. Nothing here changes the message contract; contract proposals are in their own
section at the end and are **not** implemented.

## 1. Pin map: from the component sheet (replaces the one I had derived)

The first version used a pin map I derived because none was in the repository. It is now replaced by
the pin map from the component sheet, kept in [`pin-map.md`](pin-map.md) with the list of differences
and what each changed (section A of `config.h`, `BATTERY_DIVIDER` for the 100k/47k divider, reserved
I²C pins, and the setup-portal gesture, decision 18).

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

- **Parsers:** 1 507 payloads (477 valid; the rest have NaN or infinities, missing keys, extra keys, wrong types, values out
  of range, wrong versions, bad patterns, broken JSON, invalid UTF-8, duplicate keys, `1.0` or `3e2` as
  integers and so on) get exactly the Python contract's verdict, and the valid ones decode to the same
  values.
- **Builders:** 1 280 messages built from random and out-of-range inputs all pass the Python schemas,
  and every number equals Python's `round()` of the clamped input (`firmware/tools/check_messages.py`).
  The builders cannot produce an invalid message: values are clamped to the contract ranges first.
- An unknown `v` is counted separately from other invalid messages (contract section 6 rule 2); both
  reset the leave-fallback count, as in the simulator.

## 14. NaN and sender names: firmware and contract now agree exactly

The first version found two places where the Python contract code was looser than JSON Schema intends:
Python's `json.loads` accepted `NaN` / `Infinity` (so `{"l": NaN}` passed `cmd-motor`), and Python's
`re` `$` let `"bot\n"` pass as a sender name. Both were approved and fixed in the contract code
(contract revision 10). The firmware rejects both too, so its parsers now agree with the contract on
every one of 1 507 vector payloads, including the NaN, Infinity and trailing-newline cases.

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
the robot has not joined Wi-Fi 30 s after power-on, when both buttons are held together for 3 s within
the first minute after boot, or from the console. (Holding them at power-on, the first idea, is not
safe with the buttons on GPIO 14 and 15: GPIO 14 outputs a signal and GPIO 15 is a strapping pin
while the chip boots. Rewards are therefore sent on release, and a two-button chord sends neither.) It stores the Wi-Fi network and password, the broker address, port and TLS, and
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
  red, low battery an orange flash every 2 s. The LED shows robot-side states only; hunger is shown on
  the dashboard (see "Later ideas").
- **Buzzer:** boot, connected, escape, Wi-Fi lost, low battery (every 30 s), cut-off, reward, punish,
  error, portal. Passive piezo by default (`BUZZER_PASSIVE`).
- **Partition table `min_spiffs.csv`**: two 1.9 MB app slots (room for over-the-air updates, plan 6.14)
  instead of the default 1.25 MB, which the firmware already filled to 78 %.
- **A missing `secrets.h`** still builds (with the template's placeholders): the console says so and the
  portal opens. A fresh clone stays buildable.
- **Watchdog:** the task watchdog (5 s, restart on timeout) watches the reflex, sensor, ui and comms
  tasks; the console command `freeze` stops feeding it to test the restart (reset reason `watchdog`).
- **Encoders** (optional, off by default) only count pulses for the console; there is no contract field.

## Contract proposals

- **P1 (approved, done in contract revision 10).** `Contract.decode` rejects `NaN`, `Infinity` and
  `-Infinity`; `validate` and `encode` refuse non-finite numbers.
- **P2 (approved, done in contract revision 10).** Schema patterns match the whole string (JSON Schema
  meaning), so `"bot\n"` is no longer the sender `bot`.
- **P3 (dropped for now).** No "hungry" LED colour: the LED shows robot-side states only and the
  dashboard shows hunger. Kept below as a later idea.

## Later ideas

- **Show the brain's mood on the robot's LED** (was P3). Hunger lives in `brain/state`, which the `esp32`
  user may not read (contract section 4). It would need either read access to `brain/state` for `esp32`
  or an optional mood field in `cmd/motor`, which is a contract change.
- **I²C pins 21 / 22 are reserved** for an optional OLED or a VL53L0X distance sensor; no driver yet.
