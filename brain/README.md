# brain/

One Python program that runs on the server (laptop first, then the Azure VM under systemd).
Not yet created. It contains three separate modules:

| Module | Job | MQTT user |
|---|---|---|
| `brain` | Fruit-fly-inspired spiking network (plus the rule-based baseline controller). Reads sensors, publishes motor commands, brain state, weights, sleep and brain status. | `brain` |
| `logger` | Subscribes to every `flybot/01/#` topic, validates against `docs/schemas/`, writes to SQLite. Shares the `brain` connection. | `brain` |
| `bot` | Telegram bot: alerts, `/status`, `/good`, `/bad`, `/stop`, mode changes, `/sleep` and `/wake` (via `brain/sleep`). Own connection with narrow permissions: no `brain/control`. | `bot` |

The modules talk to each other only through MQTT, never by direct calls, so the
message contract stays the single interface.

Planned: `pyproject.toml`, a package with `brain/`, `logger/`, `bot/` sub-packages, a YAML config
file, and `tests/`. The virtual environment lives in `brain/.venv/` (on D:, git-ignored).
