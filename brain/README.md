# brain/: the FlyBot brain program

One Python program, `python -m flybot_brain`, made of separate modules:

| Module | Status | Job | MQTT user |
|---|---|---|---|
| `core.py` + `controllers/` | **Phase 4** | Contract rules, control ticks, baseline controller (neural: stub) | `brain` |
| `logger/` | Phase 8 | Every `flybot/01/#` message → SQLite, on the brain's connection | `brain` |
| `bot/` | Phase 10 | Telegram alerts and commands, own connection with a narrow ACL | `bot` |

The modules talk only through MQTT. Contract code (schemas, sequence rules, robot clock) comes
from the shared [`contract/`](../contract/README.md) package.

## Setup (once)

Everything stays on D:.

```powershell
$env:PIP_CACHE_DIR = "D:\Tools\cache\pip"; $env:TMP = "D:\Tools\tmp"; $env:TEMP = "D:\Tools\tmp"
$env:PYTHONPYCACHEPREFIX = "D:\Tools\cache\pycache"
python -m venv brain\.venv
brain\.venv\Scripts\python -m pip install -r brain\requirements.txt
brain\.venv\Scripts\python -m pip install -e contract
```

It uses MQTT user `brain` from the root `.env`. The broker must be running: `infra\start-broker.cmd local`.

## Running

From the `brain` folder:

```powershell
.venv\Scripts\python -m flybot_brain                      # config\brain.yaml
.venv\Scripts\python -m flybot_brain --reset-on-reboot    # independent trials (batches)
.venv\Scripts\python -m flybot_brain --controller neural  # stub: keeps the robot still
```

Other options: `--config <yaml>`, `--seed N`, `-v`. **Ctrl+C** stops it cleanly: it saves snapshot
`latest` and publishes `brain/status` offline.

**With the simulator.** Start the brain in one terminal, then in `sim\`:
- `.venv\Scripts\python -m flybot_sim live` for the live view;
- `.venv\Scripts\python -m flybot_sim batch --trials 50 --controller baseline` for a lockstep batch.
  Start the brain with `--reset-on-reboot` for this.

Don't run `sim\tools\drive.py` at the same time: it also logs in as `brain`.

## What it does on the wire (contract revision 6)

- **One control tick per `sensors` message** (§7.1). It answers with `cmd/motor`: `rs` = that
  `seq`, `ttl` 300 ms.
- **Robot time comes only from `sensors.up`.** It is monotonic: a reboot step counts 0, and gaps
  over 500 ms are capped at 100 ms and counted. Wall-clock time is used only for link health.
- **Silence instead of commands** in these cases:

  | Situation | `brain/state.dec` | `brain/status.sens` |
  |---|---|---|
  | no sensors for 250 ms (wall clock) | `stale` | `stale` |
  | no sensors yet | `stale` | `none` |
  | mode is not `auto` | `paused` | `ok` |
  | asleep | `sleep` | `ok` |
  | odour still `null` (MQ-3 warm-up; the robot holds still in `boot`) | `warmup` | `ok` |

- **Sequence numbers** are tracked per sender. A robot reboot is recognised by a new boot ID (`bid`, revision 6). Duplicate or late `sensors`
  are not ticked. Every message in and out is validated against `docs/schemas`.
- **`ping` → `pong`** is answered immediately, on the network thread.
- **`brain/state`** goes out every 200 ms of robot time, and once a second of wall time while no
  sensors arrive. **`brain/status`** (retained, last will offline) goes out on change and every 10 s.
- **`brain/sleep`** becomes `cmd/sleep` with `why:"user"`, repeated every 5 s of robot time while asleep.
- **`brain/control`:**
  - `param`: `ctrl` or `<section>.<field>`, e.g. `baseline.speed`;
  - `save` / `load` / `reset`;
  - `lesion` answers `ok:false` until Phase 5.

  The result goes into `brain/state.ack` with the sending tab's `src` and `seq`.
- **Robot reboot:** internal state is kept by default. With `--reset-on-reboot` (or
  `experiment.reset_on_reboot`), state and random seed reset, so batch trials are independent.

Snapshots are JSON files in `.local\brain\snapshots\` (git-ignored, on D:). They hold internal
state only. Parameters always come from `config\brain.yaml`.

## Baseline controller

Braitenberg-style (`controllers/baseline.py`). In priority order each tick:
1. **Arrival:** stop and report `eat` when an odour value passes `arrive`. Hysteresis keeps it
   from twitching in the patchy plume.
2. **Obstacle:** turn away on the spot, toward the stronger odour side, for `avoid_ms`. It also
   slows down from `slow_cm`.
3. **Seek:** steer by the normalised left/right difference. The difference is weighted by the
   summed signal up to `steer_full_at`, because a weak signal is mostly plume patchiness.
4. **Wander:** with no signal, drive forward with seeded random curves (`explore`).

All gains and thresholds are in `config\brain.yaml`.

### Baseline results (Phase 4)

Lockstep over MQTT, brain process with `--reset-on-reboot`, 50 trials per scenario. Each trial
is a fresh robot boot, seeds are scenario seed + trial, and start positions rotate.

| Scenario | Trial length | Reached source | Median time to source | Collisions / trial | Lost msgs |
|---|---|---|---|---|---|
| default | 60 s | **43/50 (86%)** | 17.2 s | 0.34 | 0 |
| obstacles | 90 s | **31/50 (62%)** | 23.8 s | 1.02 | 0 |
| two_sources | 90 s | **49/50 (98%)** | 12.2 s | 0.00 | 0 |

**Before tuning** (20 trials each): 35% / 15% / 25%. Two things fixed that:
- the arrival threshold had stopped the robot about 35 cm from the source;
- far from the source it steered after plume patchiness.

**Real-time latency** (2-minute run, local broker): sensor → `cmd/motor` round trip median 5 ms,
95th percentile 10 ms; ping → pong median 5 ms, p95 15 ms. The simulator measures on its 5 ms
physics clock, so "5 ms" means within one step. There was one 355 ms outlier.

The remaining failures are mostly a single forward ultrasonic sensor not seeing obstacles beside
the robot (`obstacles`), and starts far from the plume where the signal stays in the noise.

## Tests

From `brain\`: `.venv\Scripts\python -m pytest`. That runs 141 tests:
- the core: tick, time base, reboots, gaps, warm-up, stale, paused, sleep, control and the reset flag;
- message parsing in the link;
- baseline decisions;
- the shared contract consistency tests.
