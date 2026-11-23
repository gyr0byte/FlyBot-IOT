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
- New topics beyond the plan: `cmd/manual` (dashboard driving), `cmd/sleep` (brain sleep/wake)
  and `brain/status` (brain online/offline with last will).

## Status

| Phase | What | State |
|---|---|---|
| 0 | Repository, conventions, `.gitignore`, `.env.example` | ✅ done |
| 1 | Message contract + JSON Schemas | ✅ done (contract revision 1) |
| 2 | Local Mosquitto broker with users and ACL | next |
| 3 | Simulator | not started |
| 4 | Brain skeleton + rule-based baseline | not started |
| 5 | Neural brain | not started |
| 6 | ESP32 firmware | not started (waiting for parts) |
| 7–13 | Integration, logger, dashboard, bot, cloud, evaluation, demo | not started |

No application code exists yet.
