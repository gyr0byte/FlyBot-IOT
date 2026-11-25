# firmware/: the FlyBot robot (ESP32)

PlatformIO project for the ESP32 DevKit (`esp32dev`), Arduino framework, ESP-IDF `esp-mqtt` for MQTT.
It follows [`docs/message-contract.md`](../docs/message-contract.md) and ports the simulator's edge
logic (`sim/flybot_sim/edge.py`, `edge_sensing.py`) to C++.

| Path | What |
|---|---|
| `platformio.ini` | `esp32dev` (the robot) and `native` (unit tests on the PC) |
| `include/config.h` | every pin and threshold; section B mirrors `sim/flybot_sim/edge_config.py` |
| `include/secrets.example.h` | template for `include/secrets.h` (git-ignored): Wi-Fi, broker, MQTT password |
| `lib/flybot_edge/` | the hardware-independent edge logic (pure C++, unit-tested on the PC) |
| `test/` | PC tests: unit tests and the cross-check vectors |
| `tools/` | `check_config.py` (config.h vs the simulator), `gen_vectors.py` (cross-check vectors) |

## Tools (on D:, see CLAUDE.md)

```powershell
$env:PLATFORMIO_CORE_DIR = "D:\Tools\platformio"; $env:TMP = "D:\Tools\tmp"; $env:TEMP = "D:\Tools\tmp"
D:\Tools\pio-venv\Scripts\pio run -e esp32dev          # build
D:\Tools\pio-venv\Scripts\pio run -e esp32dev -t upload # flash (board on USB)
D:\Tools\pio-venv\Scripts\pio device monitor            # serial console, 115200 baud
```

PlatformIO was installed with `python -m venv D:\Tools\pio-venv` and
`pip install platformio==6.1.18` (pip cache `D:\Tools\cache\pip`).

Before the first build: `copy include\secrets.example.h include\secrets.h` and fill it in.

## Tests on the PC

```powershell
$env:PATH = "C:\MinGW\bin;$env:PATH"                # any host gcc (see docs/firmware-decisions.md, 10)
D:\Tools\pio-venv\Scripts\pio test -e native       # unit tests + cross-check against the Python reference
```

- `test/test_edge_unit/`: hand-written tests of the edge logic, streams and sensing.
- `test/test_crosscheck/`: replays `test/vectors/` (recorded from the simulator's Python edge module by
  `tools/gen_vectors.py`) and requires identical results. Regenerate the vectors after any change to
  `sim/flybot_sim/edge.py`, `edge_sensing.py` or `edge_config.py`:
  `sim\.venv\Scripts\python -B firmware\tools\gen_vectors.py`
- `tools/check_config.py`: config.h section B against `edge_config.py`.
