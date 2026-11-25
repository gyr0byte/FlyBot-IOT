# firmware/: the FlyBot robot (ESP32)

PlatformIO project for the ESP32 DevKit (`esp32dev`), Arduino framework (Arduino-ESP32 2.0.17), ESP-IDF
`esp-mqtt` for MQTT. It follows [`docs/message-contract.md`](../docs/message-contract.md) and ports the
simulator's edge logic (`sim/flybot_sim/edge.py`, `edge_sensing.py`) to C++, cross-checked bit for bit.
Written and compiled; **not yet flashed** (no hardware yet). Bring-up: [`docs/hardware-bringup.md`](../docs/hardware-bringup.md).
Decisions: [`docs/firmware-decisions.md`](../docs/firmware-decisions.md). Summary: [`docs/firmware-summary.md`](../docs/firmware-summary.md).

| Path | What |
|---|---|
| `platformio.ini` | `esp32dev` (the robot) and `native` (tests on the PC) |
| `include/config.h` | every pin and threshold; section B mirrors `sim/flybot_sim/edge_config.py` |
| `include/secrets.example.h` | template for `include/secrets.h` (git-ignored): Wi-Fi, broker, MQTT password |
| `lib/flybot_edge/` | edge logic, hardware-independent: state machine, watchdog, fallback, looming escape, habituation, sleep, e-stop, motor mixer (`edge.cpp`); odour channel, distance filter, battery monitor (`sensing.cpp`); contract section 6 sequence rules (`streams.cpp`) |
| `lib/flybot_io/` | hardware-independent I/O: contract message builders and strict parsers (`messages.cpp`), round-trip windows, debouncer, buzzer tunes, mood colours (`io_util.cpp`) |
| `src/main.cpp` | start-up: hardware, watchdog, boot ID, tasks |
| `src/tasks.cpp` | reflex task (200 Hz: edge, ultrasonic, motors), sensor task (20 Hz), UI task |
| `src/net.cpp` | comms task: Wi-Fi, MQTT, status, events, ping |
| `src/portal.cpp`, `src/settings.cpp` | setup portal; Wi-Fi and broker settings in NVS |
| `src/hw.cpp` | the only code that touches pins |
| `src/console.cpp` | serial console for bring-up (`help`) |
| `test/` | PC tests: `test_edge_unit`, `test_crosscheck` (Python vectors), `test_messages` |
| `tools/` | `check_config.py`, `gen_vectors.py`, `check_messages.py` |

## Build and flash (tools on D:, see CLAUDE.md)

```powershell
$env:PLATFORMIO_CORE_DIR = "D:\Tools\platformio"; $env:TMP = "D:\Tools\tmp"; $env:TEMP = "D:\Tools\tmp"
cd firmware
copy include\secrets.example.h include\secrets.h   # once, then fill it in
D:\Tools\pio-venv\Scripts\pio run -e esp32dev            # build
D:\Tools\pio-venv\Scripts\pio run -e esp32dev -t upload  # flash (board on USB)
D:\Tools\pio-venv\Scripts\pio device monitor             # serial console, 115200 baud; type help
```

PlatformIO was installed with `python -m venv D:\Tools\pio-venv` and `pip install platformio==6.1.18`
(pip cache `D:\Tools\cache\pip`); its platforms, toolchain and libraries are in `D:\Tools\platformio`.
Without `secrets.h` the firmware still builds (placeholders) and opens the setup portal on the robot.

## Tests on the PC

```powershell
$env:PATH = "C:\MinGW\bin;$env:PATH"                     # any host gcc (see firmware-decisions.md, 10)
D:\Tools\pio-venv\Scripts\pio test -e native             # 32 tests
cd ..
sim\.venv\Scripts\python -B firmware\tools\check_messages.py   # the C++-built messages vs the Python contract
sim\.venv\Scripts\python -B firmware\tools\check_config.py     # config.h vs edge_config.py
```

- `test_edge_unit`: hand-written tests of the edge logic, streams and sensing.
- `test_crosscheck`: replays `test/vectors/` (recorded from the Python edge module) and requires identical
  results: 100 040 control steps, 15 803 sensing steps, 8 972 arithmetic and 6 000 stream vectors.
- `test_messages`: 1 447 payloads against the Python contract's verdicts; writes 1 280 built messages to
  `.pio/messages_out.txt` for `check_messages.py`; tests of the I/O helpers.

Regenerate the vectors after any change to `sim/flybot_sim/edge.py`, `edge_sensing.py`, `edge_config.py`
or the contract schemas: `sim\.venv\Scripts\python -B firmware\tools\gen_vectors.py`.
