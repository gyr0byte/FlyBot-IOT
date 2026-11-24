# FlyBot

A small ESP32 wheeled robot whose decisions come from a **fruit-fly-inspired spiking neural
network** running on a server, connected over **MQTT**. The robot seeks odour sources, dodges
fast-approaching objects on its own, learns from "good bot / bad bot" rewards, and shows internal
states such as hunger, fear and sleep. A live web dashboard and a Telegram bot let you watch and
train it.

College IoT project (Cloud Computing and Internet of Things). Background:
[project proposal](docs/FlyBot_Project_Proposal.md) ·
[software implementation plan](docs/FlyBot_Software_Implementation_Plan.md).

## Architecture

```
  ┌──────────── ROBOT (edge) ────────────┐
  │ ESP32: 2× MQ-3, HC-SR04, LDR, battery │
  │ motors, LED, buzzer, reward buttons   │      sensors, events, status, ping, reward
  │ local reflexes: looming escape,       │ ─────────────────────────────────────────┐
  │ obstacle stop, Wi-Fi-loss fallback    │ ◄────────────────────────────────┐       │
  └───────────────────────────────────────┘  cmd/motor, cmd/manual,          │       ▼
        (or: Python simulator, same topics)   cmd/mode, cmd/sleep, pong   ┌──┴────────────────┐
                                                                          │   MQTT broker     │
                                                                          │   (Mosquitto)     │
                                                                          └──┬──────┬──────┬──┘
                    ┌────────────────────────────────────────────────────────┘      │      │
                    ▼                                                               ▼      ▼
  ┌──────────── brain program (Python) ────────────┐               ┌───────────────┐ ┌───────────┐
  │ brain : spiking network → cmd/motor, cmd/sleep,│               │ Dashboard     │ │ (browser) │
  │         brain/state, brain/weights, pong       │               │ Next.js + TS  │ │ live view,│
  │ logger: every topic → SQLite                   │               │ over WebSocket│ │ controls  │
  │ bot   : Telegram alerts, /good /bad /stop      │               └───────────────┘ └───────────┘
  └────────────────────────────────────────────────┘
```

- **ESP32 → broker → brain → commands back.** The robot publishes sensor readings at 20 Hz.
  The brain runs a 20 Hz control tick and publishes wheel commands with a short time-to-live.
  If commands stop arriving, the robot falls back to local wandering.
- **Fast reflexes stay on the robot.** Looming escape and obstacle stop never wait for the cloud.
- **Dashboard and Telegram bot are just more MQTT clients.** They read state and send
  mode changes, manual driving, rewards and brain controls.
- **Programs only talk through MQTT.** The [message contract](docs/message-contract.md) defines
  every topic and payload, and the broker's access-control list decides who may publish what.
  The simulator can stand in for the robot, and the brain can move from laptop to cloud
  without code changes.
- **Deployment:** development on a Windows laptop with Mosquitto installed natively;
  production on an Azure Ubuntu VM with services under systemd. **No Docker.**

## Repository layout

| Folder | Contents |
|---|---|
| [`firmware/`](firmware/) | ESP32 firmware: PlatformIO, Arduino framework, ESP-IDF `esp-mqtt`. |
| [`brain/`](brain/) | One Python program: brain, logger (SQLite) and Telegram bot modules. |
| [`sim/`](sim/) | Python simulator (digital twin) that speaks the same messages as the robot. |
| [`dashboard/`](dashboard/) | Next.js + TypeScript dashboard. |
| [`infra/`](infra/) | Mosquitto config and ACL, systemd units, deployment notes. |
| [`docs/`](docs/) | Proposal, plan, [conventions](docs/conventions.md), [message contract](docs/message-contract.md). |
| [`docs/schemas/`](docs/schemas/) | JSON Schema (draft 2020-12) for every message type. |
| [`analysis/`](analysis/) | Evaluation notebooks (latency, odour seeking, learning, reliability). |

Configuration: copy [`.env.example`](.env.example) to `.env` (git-ignored). Firmware credentials go
in `firmware/include/secrets.h` (git-ignored).

## Decisions that override the implementation plan

- One monorepo for everything.
- No Docker: Mosquitto runs natively on the laptop; the VM runs services under systemd.
- Brain, logger and Telegram bot are modules of **one** Python program (MQTT users `brain` and `bot`).
- SQLite database.
- New topics beyond the plan: `cmd/manual` (dashboard driving), `cmd/sleep` (brain → robot
  sleep/wake), `brain/sleep` (dashboard/bot → brain sleep requests) and `brain/status`
  (brain online/offline with last will, plus sensor-input health).
- `brain/control` (lesions, parameters, save/load/reset) is dashboard-only. The bot cannot use it.
- On stale sensor data the brain goes silent rather than sending stop commands, so the robot's
  watchdog puts it into local fallback.

## Status

| Phase | What | State |
|---|---|---|
| 0 | Repository, conventions, `.gitignore`, `.env.example` | ✅ done |
| 1 | Message contract + JSON Schemas | ✅ done (contract revision 7) |
| 2 | Local Mosquitto broker with users and ACL | ✅ done (Mosquitto 2.1.4, ACL test 182/182) |
| 3 | Simulator | ✅ done (lockstep about 20x real time; learning-experiment support added in Phase 5) |
| 4 | Brain skeleton + rule-based baseline | ✅ done (baseline, re-measured with independent trials: 84% / 61% / 97%, see [brain/README.md](brain/README.md)) |
| 5 | Neural brain | in progress: 5.1–5.6 done (neural steering 82% / 51% / 93%; mushroom body and dopamine learning: punishment learned and mostly cue-specific (30 animals), reward untestable at ceiling; see [brain/README.md](brain/README.md#learning-phase-5-steps-55-56)) |
| 6 | ESP32 firmware | not started (waiting for parts; reference logic in `sim/flybot_sim/edge*.py`) |
| 7–13 | Integration, logger, dashboard, bot, cloud, evaluation, demo | not started |

## Known issues / later

- **Dashboard MQTT password is visible in the browser.** The dashboard connects to the broker
  directly over WebSockets, so the `dashboard` credentials reach every browser that opens it.
  Later: proxy MQTT through the Next.js server so the password stays server-side. Until then the
  dashboard needs its own login and TLS in production (contract §9).
- **Weight list length is not schema-checked.** JSON Schema cannot check that `brain/weights`
  `ap` and `av` each have exactly `n` items. The brain must check this before publishing, and
  receivers (dashboard, logger) must check it before use (contract §3.11).
- **Single forward ultrasonic sensor.** The robot cannot see obstacles beside it, and turning
  makes the cone sweep across walls, so the looming detector is suppressed while turning
  (`edge_config.loom_max_turn`). A rare false escape can still happen on a wall corner.
