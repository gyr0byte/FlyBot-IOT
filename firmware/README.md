# firmware/: the FlyBot robot (ESP32)

PlatformIO project for the ESP32 DevKit (`esp32dev`), Arduino framework, ESP-IDF `esp-mqtt` for MQTT.
It follows [`docs/message-contract.md`](../docs/message-contract.md) and ports the simulator's edge
logic (`sim/flybot_sim/edge.py`, `edge_sensing.py`) to C++.

| Path | What |
|---|---|
| `platformio.ini` | `esp32dev` (the robot) and `native` (unit tests on the PC) |
| `include/config.h` | every pin and threshold; section B mirrors `sim/flybot_sim/edge_config.py` |
| `include/secrets.example.h` | template for `include/secrets.h` (git-ignored): Wi-Fi, broker, MQTT password |
| `tools/check_config.py` | checks config.h section B against the simulator's `EdgeConfig` |

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
