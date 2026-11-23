# sim/

Python simulator ("digital twin") of the robot and its arena. Not yet created.

It pretends to be the ESP32: it publishes the same `sensors`, `events`, `status`, `ping`
and `reward` messages, and obeys `cmd/motor`, `cmd/manual`, `cmd/mode` and `cmd/sleep`
exactly as the firmware will (including command time-to-live and the edge reflexes).
It connects as MQTT user `sim`, which has the same permissions as `esp32`.

**Only one of the simulator or the real robot runs at a time.** Both use the `flybot/01/...` topics.

Planned features: 2D arena with walls and odour sources, differential-drive model,
deliberately noisy and slow sensor models (MQ-3 lag, ultrasonic dropouts), a live top-down
view with keyboard controls, scenario files, and a headless batch mode that writes CSV results.

The virtual environment lives in `sim/.venv/` (on D:, git-ignored).
