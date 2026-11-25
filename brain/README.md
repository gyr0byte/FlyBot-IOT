# brain/: the FlyBot brain program

One Python program, `python -m flybot_brain`, made of separate modules:

| Module | Status | Job | MQTT user |
|---|---|---|---|
| `core.py` + `controllers/` | **Phase 4–5** | Contract rules, control ticks, baseline and neural controllers | `brain` |
| `network/` + `circuits/` | **Phase 5** | Spiking network engine with dopamine-gated plasticity; circuits as data (designed now, FlyWire in 5.13) | – |
| `logger/` | **Phase 8** | Every `flybot/01/#` message → SQLite, on the brain's connection; CSV export; read API | `brain` |
| `bot/` | **Phase 10** | Telegram alerts and commands, own connection with a narrow ACL | `bot` |

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
.venv\Scripts\python -m flybot_brain --controller neural  # spiking network (steps 5.1–5.6)
.venv\Scripts\python -m flybot_brain --controller neural --no-persistence   # a naive brain
```

Other options: `--config <yaml>`, `--seed N`, `--no-logger`, `--no-bot`, `-v`. **Ctrl+C** stops it cleanly: it saves snapshot
`latest` and publishes `brain/status` offline. `--no-persistence` neither loads nor saves `latest`
(each learning-experiment animal starts naive).

**Seeds.** With `--reset-on-reboot`, the n-th robot boot the brain sees (n = boot-ID changes since
it started) is re-seeded with `trial_seed(seed, n)`: the base seed for the first boot, then a seed
derived from (base, n). Every batch trial gets its own noise, and re-running a batch with the same
`--seed` reproduces it exactly (with the simulator's batch lockstep wait, contract §7.1 (5)).
Without the flag, a reboot keeps state and the random stream simply continues.

**With the simulator.** Start the brain in one terminal, then in `sim\`:
- `.venv\Scripts\python -m flybot_sim live` for the live view;
- `.venv\Scripts\python -m flybot_sim batch --trials 50 --controller baseline` for a lockstep batch.
  Start the brain with `--reset-on-reboot` for this.

Don't run `sim\tools\drive.py` at the same time: it also logs in as `brain`.

## What it does on the wire (contract revision 9)

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

## Logger (Phase 8)

A module of the brain program (`flybot_brain/logger/`), separate from the brain logic. The brain's
MQTT link hands it every message on `flybot/01/#` (the brain's own messages included, as the broker
echoes them back) before the brain looks at it. It runs in its own thread and writes to SQLite at
`FLYBOT_DB_PATH` (`.env`, default `data/flybot.db` on D:, git-ignored).

- **Validates** every message with the contract schemas; invalid ones are counted, not stored.
- **Counts missing messages:** sequence numbers are tracked per topic and sender (contract §6), so
  gaps (lost), duplicates and late messages are counted. A robot reboot (new `bid`) is not a gap;
  retained messages and the fixed offline last wills are not stream steps. Counts are in the
  `reliability` table.
- **Writes in batches** (every 0.5 s or 200 rows), WAL mode so readers never block it.
- **Tables** (plan step 8.1): `sensors`, `events` (robot events and rewards), `commands`
  (`cmd/motor` from the brain, `cmd/manual`, and the control topics `cmd/mode`, `cmd/sleep`,
  `brain/control`, `brain/sleep` with their sender), `brain_state`, `weights`, `latency` (robot
  `rtt` / `crtt` from `status`, brain tick time and `lag` from `brain/status`), `status`, `trials`
  (for the evaluation tools: `Logger.add_trial`) and `reliability`. Every row has the arrival time
  `ts` (Unix seconds) and the sender's `up` (and `bid` for robot messages), so fast lockstep runs can
  be placed on a robot-time timeline.
- **Retention** (step 8.3): at start the brain prunes raw `sensors`, `commands` and `brain_state`
  rows older than `logger.retention_days` (7). Events, trials, latency, status, weights and the
  reliability counts are kept.
- `--no-logger` switches it off; the experiment tools use it, so lockstep experiments do not flood
  the database.

```powershell
.venv\Scripts\python -m flybot_brain.logger run                  # standalone, own connection (no brain)
.venv\Scripts\python -m flybot_brain.logger export --out D:\exp   # one CSV per table (--minutes N)
.venv\Scripts\python -m flybot_brain.logger prune --days 7
.venv\Scripts\python -m flybot_brain.logger stats                 # row counts and reliability
.venv\Scripts\python -m flybot_brain.logger api --port 8000      # read API, http://127.0.0.1:8000/docs
```

**Read API** (step 8.4, FastAPI, read-only, 127.0.0.1): `/api/health`, `/api/sensors?minutes=10`
(downsampled to `max_points`), `/api/events?minutes=60`, `/api/latency?minutes=60` (median, p95,
histograms), `/api/trials` (with success rates), `/api/timeline` (meals, startles, sleep periods)
and `/api/reliability`. The dashboard's server calls it; the browser never does.

**Checked live:** a brain with the logger and a 3-trial simulator batch recorded 2,158 sensor
messages, 1,858 commands, 548 brain states, events, weights, status and latency, with 0 invalid and
0 lost; the CSV export opened cleanly in pandas.

## Telegram bot (Phase 10)

A module of the brain program (`flybot_brain/bot/`) on its **own** MQTT connection as user `bot`
(contract section 4: it reads `status`, `events`, `cmd/mode`, `brain/status`, `brain/state`; it may
publish `cmd/mode`, `reward` and `brain/sleep` only). It starts with the brain when
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_CHAT_ID` are set in `.env` (the `.env.example`
placeholder does not count); `--no-bot` switches it off; `python -m flybot_brain.bot` runs it alone.

**Commands** (only from allowed chats; others are ignored and logged):

| Command | Does |
|---|---|
| `/status` | robot state, mode, battery, Wi-Fi, uptime; brain controller, sensor health, lag; decision and why; hunger, fear, habituation; lesions |
| `/good`, `/bad` | `reward` +1 / −1 (`src: bot`) |
| `/sleep`, `/wake` | `brain/sleep` (the brain decides and tells the robot) |
| `/stop` | `cmd/mode` `estop` (retained) |
| `/resume` | `cmd/mode` `auto` (retained): clears an e-stop from the phone |
| `/learned` | the learned preferences (`brain/state.pref`) in words |
| `/help` | the list |

**Alerts** (to every allowed chat, once per event): robot offline / back online (from the retained
status and its last will; not on the first message after start), brain offline / online, low battery
(warning and cut-off), looming escape (at most one per 60 s, then "+N more"; habituated detections
with `resp` 0 are not alerts), fallback entered / left, brain falling behind (`brain/status.lag`
above 40) and caught up, and "Learned: the light = bad (−0.55)" when a preference passes ±0.5
(re-armed when it falls back below 0.25).

The logic (`bot/core.py`) has no I/O; `bot/telegram.py` holds the real client (Telegram Bot API over
HTTPS, long polling, standard library only; the token is never logged) and a fake one used by the
tests; `bot/runner.py` wires them to MQTT.

### Setting it up (once)

1. In Telegram, open **@BotFather**, send `/newbot`, choose a name and a username ending in `bot`.
   BotFather replies with a token like `123456789:AAH...`.
2. Open a chat with your new bot and send it any message (bots cannot write to you first).
3. Find your chat ID: open `https://api.telegram.org/bot<token>/getUpdates` in a browser and read
   `"chat":{"id": ...}` (or message **@userinfobot**).
4. In the repository's `.env` (never committed) set
   `TELEGRAM_BOT_TOKEN=<token>` and `TELEGRAM_ALLOWED_CHAT_ID=<your id>` (several IDs: comma-separated,
   e.g. for a demo phone).
5. Start the brain (`python -m flybot_brain`); it logs "telegram bot running for 1 chat(s)". Send
   `/status`.

## Failure containment (logger and bot)

Nothing that goes wrong in the logger or the bot can stop the brain's control loop. Errors are logged
(at most once every 10 s per part, so a broken disk cannot flood the log) and counted.

| Failure | What happens |
|---|---|
| A link tap raises (e.g. a logger bug) | Caught in the MQTT thread and counted (`link.tap_errors`); the message is still handled |
| Logger queue full (the writer is stuck) | The message is dropped and counted (`dropped`); `tap()` never blocks. Limit: 100,000 messages |
| A message cannot be stored (bug, unexpected field) | Skipped and counted (`errors`); the writer carries on |
| SQLite error on write or commit (disk full, locked, I/O error) | The open batch is dropped and counted, the connection closed and reopened with backoff (1 s, doubling, up to 60 s); messages queue up meanwhile |
| Logger cannot open its database at start-up | Logged; the brain runs without the logger |
| Telegram unreachable or refusing (`getUpdates`) | Logged; the poller backs off (1 s, doubling, up to 60 s) and retries |
| `sendMessage` fails | Retried up to 3 times with backoff, then that reply is dropped and logged |
| An exception in the bot logic, or a failing MQTT publish | Logged; the user gets "Sorry, that failed" and the bot carries on |
| Bot cannot start (missing `MQTT_BOT_*`, etc.) | Logged; the brain runs without the bot |

`tests/test_resilience.py` injects each of these failures (and all of them at once) and checks that
the brain still answers every sensors message with a `cmd/motor`, and that the logger and bot recover
when the failure clears.

## Baseline controller

Braitenberg-style (`controllers/baseline.py`). In priority order each tick:
1. **Arrival:** stop and report `eat` when an odour value passes `arrive`. Hysteresis keeps it
   from twitching in the patchy plume.
2. **Obstacle:** turn away on the spot, toward the stronger odour side, for `avoid_ms`. It also
   slows down from `slow_cm`. This and rule 4 are the shared, designed motor layer
   (`controllers/motor.py`, config `motor:`).
3. **Seek:** steer by the normalised left/right difference. The difference is weighted by the
   summed signal up to `steer_full_at`, because a weak signal is mostly plume patchiness.
4. **Wander:** with no signal, drive forward with seeded random curves (`explore`).

All gains and thresholds are in `config\brain.yaml`.

### Baseline results (Phase 4)

> Superseded: these were measured before every trial had its own brain seed. See
> [Neural vs baseline](#neural-vs-baseline) for the re-measurement (3 base seeds × 50 trials).

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

## Neural controller (Phase 5, steps 5.1–5.6)

`controllers/neural.py` runs a spiking network (`network/`) on a circuit loaded from files.
Today that is `circuits/designed/`, made by `tools/make_designed_circuit.py`.

**What is neural and what is designed.** This split is deliberate, and it should be stated this way
in the report.

| Part | Neural or designed | Where |
|---|---|---|
| Odour → receptor firing (rate code) | neural input layer: 50 Poisson receptors per side, 2 + 150 Hz × odour | circuit `inputs`, groups `rl`, `rr` |
| Receptors → projection neurons, cross-lateral inhibition | **neural** (spiking network) | groups `pl`, `pr`, `ln_l`, `ln_r` |
| Turn and speed from PN activity | **neural readout**: PN rates per side, smoothed over robot time; contrast sets the turn, total sets the speed | `neural:` config |
| Arrival ("eat") | **neural readout**: peak PN rate above `arrive_hz` | `neural:` config |
| Odour and light → Kenyon cells → approach / avoid output neurons; learning | **neural** (mushroom body, dopamine-gated plasticity) | see [Learning](#learning-phase-5-steps-55-56) |
| How strongly the robot follows the odour | **neural readout**: valence v = (A − V)/(A + V) of the output neurons scales the innate attraction | `neural:` config (`valence_gain`) |
| Obstacle avoidance, slowing near obstacles, wandering without odour | **designed rules**, the same motor layer as the baseline | `controllers/motor.py`, `motor:` config |

The circuit itself is designed for now: sizes, wiring and weights are design choices (the manifest
says so). Step 5.13 swaps it for FlyWire connectome data without changing the engine.

**The designed circuit** (2,241 neurons, 31,152 connections). The olfactory front end, which
steers, is 170 neurons and 1,400 connections:
- 50 receptors per side;
- 25 projection neurons per side, each fed by 12 same-side receptors. They sit at half threshold, so
  they respond from an odour value of about 0.03 but stay silent in clean air;
- 10 local inhibitory neurons per side, driven by their own side's receptors. They inhibit the
  opposite side's projection neurons, which roughly doubles the left/right contrast for weak and
  moderate signals.

The mushroom body and output neurons (2,071 more neurons) are described under
[Learning](#learning-phase-5-steps-55-56). Nothing feeds back from them to the projection neurons,
so the steering signal is unchanged by them.

### Neural vs baseline

Lockstep over MQTT, brain process with `--reset-on-reboot`. For each controller and scenario:
3 base seeds (`--seed 1/2/3`) × 50 trials. Every trial has its own brain seed (see Seeds above),
and batch lockstep waits up to 2 s for the brain (contract §7.1 (5)). Over 900 trials there was
1 lockstep timeout and no fallback, so every batch can be re-run with the same result. Both
controllers ran on the same build (2026-11-24). The neural brain was untrained (valence ≈ 0).

| Scenario | Controller | Reached, seeds 1 / 2 / 3 | Reached, pooled (95% CI) | Median time to source | Collisions per trial |
|---|---|---|---|---|---|
| default (60 s) | neural | 82 / 84 / 80% | **82%** (75–87%) | 21.5 s | 0.25 |
| default (60 s) | baseline | 84 / 88 / 80% | **84%** (77–89%) | 20.0 s | 0.23 |
| obstacles (90 s) | neural | 54 / 56 / 44% | **51%** (43–59%) | 29.1 s | 2.17 |
| obstacles (90 s) | baseline | 68 / 56 / 58% | **61%** (53–68%) | 26.7 s | 1.39 |
| two_sources (90 s) | neural | 90 / 94 / 94% | **93%** (87–96%) | 23.4 s | 0.00 |
| two_sources (90 s) | baseline | 100 / 96 / 96% | **97%** (93–99%) | 21.9 s | 0.05 |

The intervals are Wilson 95% intervals over the 150 pooled trials.

**Reading the table:**
- On `default` the two controllers are level.
- On `obstacles` the neural controller is about 10 points lower, with more collisions; the
  intervals overlap a little. On `two_sources` it is a few points lower.
- It is slightly slower everywhere (1.5 to 2.5 s): its smoothed spike-rate readout reacts more
  slowly than the baseline's direct sensor difference.

**Correction to the step 5.4 results.** The earlier table (neural 92% / 60% / 100%, baseline 86% /
62% / 98%) used one brain seed, and every trial of a batch was re-seeded with that same seed. All
50 trials replayed one noise sequence, so a batch result mostly reflected that one sequence. The
5.4 readout was also tuned on those same scenario seeds. Re-running the unchanged 5.4 brain on
`obstacles` with seeds 1, 2 and 3 gave 60%, 52% and 22%. The claim that the neural controller
"matches or beats the baseline" does not hold up; the table above replaces it. Lockstep timeouts
(host stalls over the old 100 ms wait) also made trials irreproducible: in a repeated 10-trial
batch, the 3 trials with timeouts gave different results.

**Before tuning** (in-process, 32 trials): 50% / 16% / 62%. Three things fixed that:
- 400 ms readout smoothing, on top of the MQ-3's lag, made the robot orbit the source and scrape
  walls. Now 100 ms.
- Lateral inhibition sharpens plume patchiness too, so weak signals now steer less (`steer_full_hz` 120).
- The arrival threshold fired about 20 cm from the source. Now 145 Hz.

## Learning (Phase 5, steps 5.5–5.6)

### Mushroom body (5.5)

The designed circuit adds a mushroom body after the projection neurons:
- **2,000 Kenyon cells (KCs).** Each takes 4–8 random projection-neuron "claws" from both sides,
  with lognormal claw weights (so KCs differ in how easily they fire). 30% of KCs also get 3
  inputs from 20 light neurons (60 Hz at full light).
- **One APL neuron:** driven by all KCs, it inhibits all KCs (global feedback), which keeps the
  code sparse.

Measured on the circuit (`tests/test_mushroom_body.py`; fraction of KCs active):

| Input | Active KCs |
|---|---|
| odour + light | 9.8% |
| odour in the dark | 6.7% |
| light only | 8.2% |
| strong odour, with APL / APL removed | 6.7% / 79% |

Pattern overlap (Jaccard: KCs active in both / KCs active in either) is 0.29 between odour-with-light and odour-in-the-dark, and
0.18 between odour-in-the-dark and light only. Repeats of the same cue overlap 0.66–0.81. The code
is light-dominated: odour-with-light and light-only overlap 0.66.

### Output neurons, dopamine and the learning rule (5.6)

- **Output neurons (MBONs):** 10 "approach" and 10 "avoid". Each KC connects to each MBON with
  probability 0.3, at weight 1.0 (= `w_max`): learning can only depress.
- **Dopamine neurons (DANs):** 5 reward and 5 punishment DANs, driven by the `reward` topic. A
  message starts a 300 ms pulse (DANs fire at about 45 Hz).
- **Modulation map** (`modulation.csv`): reward DANs gate KC → avoid synapses, punishment DANs gate
  KC → approach synapses. As in the fly, a reward weakens the route to avoidance and a punishment
  weakens the route to approach.
- **Rule (three-factor):** each DAN spike depresses every synapse it gates by
  `lr × eligibility(KC)`, clipped to [0, 1]. A KC spike sets its eligibility tag to 1, and the tag
  decays with τ = 2.5 s, so dopamine up to a few seconds after a KC fired still counts.
  `lr` = 0.001. Forgetting (`neural.forget_tau_s`) is available and off.
- **Readout:** valence v = (A − V)/(A + V) of the approach (A) and avoid (V) output rates, smoothed
  over 300 ms. The odour is innately attractive and learning modulates it:
  drive = clip(1 + 2v, −1, 1.5). The turn toward the odour is scaled by drive; when drive < 0 the
  robot turns away (`dec: avoid`) and does not stop to eat.
- **Reporting:** `brain/weights` carries each KC's mean weight onto approach and avoid output
  neurons (0–255, `n` = 2,000, list lengths checked before publishing). `brain/state.pref` carries
  the valence experienced with light (`lt`) and without (`odr`). Learned weights are saved in
  snapshots.

**Why the eligibility tag saturates.** The first version added 1 to the trace per KC spike (`lr`
0.0003). A KC firing at 20 Hz built a trace of about 50, so one dopamine pulse cut active synapses
by up to 0.68, and after a few pairings every odour-driven KC was at zero: the valence was −1 for
any odour. The saturating tag models the coincidence signal in a KC (it does not grow with firing
rate). `lr` was then chosen by a criterion on weights fixed in advance, not on behaviour: for KCs
active at the pulse, about 5–10% depression after one pairing and 50–70% after ten, without
reaching 0. Measured: 6.5% after one pairing; after ten, median 43% and 90th percentile 67%.

### The learning experiment

`sim/tools/learning_experiment.py`, scenario `sim/scenarios/learning.yaml`:
- **Arena:** dark, two equal odour sources 0.9 m apart, a lamp over one ("lit"); the other is "dark".
- **Phases per animal:** pre-test 8 trials from the midline → training 15 trials started near the
  lit source → post-test 8 trials from the midline. A trial ends at the first source reached or
  after 60 s (timeout).
- **Training:** on reaching the lit source the simulator presses the training button (`reward` from
  `btn`) and keeps the trial running 2 s, so the brain pairs the reward with what it senses.
- **Animals:** each animal is a fresh brain process (`--no-persistence`, seed = animal + 1) that
  keeps what it learns across its trials. Odd animals get the mirrored arena (lamp on the right);
  training starts are checked to be near that animal's own lit source. Animal k has the same seeds
  and side in every condition, so each animal has a "control twin".
- **Conditions:** punish lit (−1 on reaching the lit source), reward lit (+1), control (same
  training, no button), delayed punish (−1 pressed 1.5 s after arrival).
- **Method:** conditions interleaved per animal, batch lockstep wait 2 s, lockstep timeouts recorded;
  2 timeouts in the 10-animal run.
- **Measures:** approaches to each source and timeouts per phase; preference index
  PI = (lit − dark)/(lit + dark); learned valence `pref.lt` / `pref.odr` at the end of each phase.
  Statistics are paired per animal: ΔPI (post − pre) and post-test counts versus the control twin,
  with exact two-sided sign tests (ties dropped).

### Results: 10 animals per condition

Post-test means per animal (8 trials). Pre-tests were alike in every condition (about 4.1 lit /
3.7 dark / 0.2 timeouts, PI ≈ +0.03).

| Condition | Post lit / dark / timeout | PI pre → post | Valence lit / dark |
|---|---|---|---|
| control | 4.3 / 3.5 / 0.2 | +0.02 → +0.09 | 0.00 / 0.00 |
| punish lit | 1.9 / 4.9 / 1.2 | +0.04 → **−0.46** | −0.86 / −0.41 |
| delayed punish (1.5 s) | 2.1 / 3.9 / 2.0 | +0.04 → −0.30 | −0.83 / −0.57 |
| reward lit | 4.2 / 3.6 / 0.2 | +0.04 → +0.07 | +0.89 / +0.61 |

Versus the control twin (post-test difference in trials; up / down; sign test p):

| Condition | ΔPI | Lit approaches | Dark approaches | Timeouts |
|---|---|---|---|---|
| punish lit | −0.50 (1 / 9, **p = 0.021**) | −2.4 (1 / 7, p = 0.070) | +1.4 (7 / 2, p = 0.18) | +1.0 (8 / 1, p = 0.039) |
| delayed punish | −0.34 (3 / 7, p = 0.34) | −2.2 (1 / 8, **p = 0.039**) | +0.4 (6 / 3, p = 0.51) | +1.8 (9 / 0, **p = 0.004**) |
| reward lit | +0.03 (4 / 4, p = 1.0) | −0.1 | +0.1 | 0.0 |

### Results: 30 animals, punish lit and control

**Sample size.** The experiment started with 10 animals per condition. After that interim look,
the switch to the dark source versus control (+1.4, 7 / 2, p = 0.18) was ambiguous. As planned for
an ambiguous 10-animal result, punish lit and control were extended with animals 10–29, using the
same method (conditions interleaved, 2 s lockstep wait; no lockstep timeouts in the new animals).
The final sample was fixed at 30 per condition before the new animals ran, with no further
additions whatever the result. The other two conditions stay at 10 animals. The 10-animal results
above are unchanged (the extension only appended animals) and are reported alongside.

| Condition (30 animals) | Pre lit / dark / timeout | Post lit / dark / timeout | PI pre → post | Valence lit / dark |
|---|---|---|---|---|
| control | 4.1 / 3.7 / 0.3 | 4.2 / 3.7 / 0.1 | +0.05 → +0.05 | 0.00 / −0.01 |
| punish lit | 4.1 / 3.6 / 0.3 | 1.5 / 5.2 / 1.2 | +0.05 → **−0.53** | −0.88 / −0.42 |

Punish lit versus the control twin, 10 animals and 30 animals:

| Measure | 10 animals | 30 animals |
|---|---|---|
| ΔPI | −0.50 (1 / 9, p = 0.021) | **−0.58** (2 / 26, p = 3 × 10⁻⁶) |
| lit approaches | −2.4 (1 / 7, p = 0.070) | **−2.6** (1 / 24, p = 1.5 × 10⁻⁶) |
| dark approaches | +1.4 (7 / 2, p = 0.18) | **+1.5** (21 / 5, p = 0.0025) |
| timeouts | +1.0 (8 / 1, p = 0.039) | **+1.1** (23 / 2, p = 1.9 × 10⁻⁵) |
| valence lit − dark | −0.44 | −0.46 |

The effect sizes barely moved between 10 and 30 animals; the larger sample narrowed the
uncertainty. With 30 animals, punished animals clearly switch to the dark source: they go there
more often than their control twins, as well as avoiding the lit one. Some of the lost lit
approaches still become timeouts (+1.1 per 8 trials).

### Saturating vs accumulating eligibility

Same animals, seeds and method, punish lit and control only (10 animals each); the accumulating
version is the first rule (`make_designed_circuit.py --rule accumulate`, same wiring).

| Punish lit, post-test | Accumulating | Saturating |
|---|---|---|
| lit / dark / timeout (control 4.3 / 3.5 / 0.2) | 2.8 / 2.6 / **2.6** | 1.9 / **4.9** / 1.2 |
| ΔPI | +0.02 (3 / 6, p = 0.51) | **−0.50** (1 / 9, p = 0.021) |
| valence lit / dark | −0.99 / −0.84 | −0.86 / −0.41 |
| timeouts vs control twin | +2.4 (9 / 0, p = 0.004) | +1.0 (8 / 1, p = 0.039) |

Per animal, the saturating rule gave 2.3 more dark choices than the accumulating rule (8 / 1,
p = 0.039) and 1.4 fewer timeouts (2 / 7, p = 0.18). With the accumulating trace, punishment mostly
taught "avoid odour sources" (the robot gave up); with the saturating tag it mostly shifted the
choice away from the lit source. Two earlier runs of both rules (old 100 ms lockstep wait,
conditions not interleaved, so not exactly reproducible) showed the same pattern.

### What the results show, and what they do not

- **Punishment learning works and is mostly cue-specific** (30 animals): approaches to the punished
  lit source fall and approaches to the unpunished dark source rise, relative to control twins.
  Some animals also give up more often (timeouts).
- **Delayed punishment (1.5 s) still teaches** (10 animals): lit approaches fall, but it shows up
  more as general avoidance (timeouts) than as a switch to the other source.
- **The learning generalises partly.** The punished cue's valence falls further than the
  unpunished odour's (−0.88 vs −0.42), but the dark odour becomes mildly aversive too. The two
  sources share the odour, and odour-driven KCs are shared between the two codes (overlap 0.29), so
  some generalisation is expected.
- **Reward has no behavioural effect** in this design, although the learned valence reaches +0.89:
  the robot already reaches a source in about 97% of test trials, and positive valence can only
  raise the turn gain toward its cap. This is a limit of the test, not evidence against reward
  learning.
- All of this is in simulation, with a designed (not connectome) circuit.

## Internal states: hunger and fear (Phase 5, step 5.7)

Hunger and fear change the network, not the controller's rules. **How they act is circuit data**
(manifest `state_modulation`, see the format below); **how they change over time is brain
configuration** (`brain.yaml` `states:`). Both are published in `brain/state` (`hun`, `fear`), and
the dashboard can set them with `brain/control` `c:"state"` (contract revision 8).

**Dynamics** (robot time):
- **Hunger** rises from 0 to 1 over 10 min (`hunger_rise_s` 600) and is reset to 0 when the robot
  eats at a source (`dec: eat`). `hunger_start` sets its value at start-up and after a state reset;
  experiments use it to make fed (0) and hungry (1) animals.
- **Fear** jumps by 0.5 × the escape strength on each `escape` event from the robot (`resp`, so a
  habituated, unacted threat does not frighten) and decays with τ = 20 s.
- **Habituation** (5.8) runs on the robot, next to the escape reflex (see
  [sim/README.md](../sim/README.md#looming-habituation-plan-step-58)). The brain mirrors the level the
  robot reports (`status.hab`, `escape.hab`) into `brain/state.hab`. Lesion `hab` answers
  `ack.ok = false`: no brain command reaches the robot's reflex.

**Coupling in the designed circuit**, chosen by criteria fixed before any behaviour was measured:

| State | Target | k | Criterion and result |
|---|---|---|---|
| hunger | receptor → PN gain, both sides | 0.15 | hunger = 1 raises the PN response to a weak odour by ~50%: measured +53%. In flies, hunger signals boost olfactory input. |
| hunger | approach-MBON bias | 0.6 | mirror image of fear, so hunger and fear meet on the same valence |
| fear | avoid-MBON bias | 0.6 | target: untrained mid-odour valence −0.5 at fear = 1. **Not reached:** −0.25 (see below) |

- **Why the fear target was missed.** The bias has to stay below threshold (output neurons must
  not fire without input). The largest subthreshold bias, 0.8, gives only −0.32, because the output
  neurons are driven hard by Kenyon cells (about 30 Hz) and a bias shifts that little. And the
  closer to threshold, the more the output neurons fire on the faint light-driven KC background of
  a dark arena without odour (hunger = fear = 1, worst of 10 seeds, per neuron): 0.14 Hz at 0.4,
  0.28 Hz at 0.6, 0.74 Hz at 0.8, against 0.06 Hz without states. 0.6 was chosen as the middle:
  valence −0.25 at fear = 1, and approach + avoid at most 0.54 Hz without odour, well below the
  controller's 2 Hz valence gate. `tests/test_states.py` fails if this drifts (each group above
  0.5 Hz, or approach + avoid above the gate).
- **Speed (readout).** Seek speed is scaled by `clip(drive, 0.4, 1)` (`neural.speed_drive_min`):
  fear lowers the valence, the drive and with it the speed. Without this, fear would only weaken
  the steering. This also slows a robot approaching a learned-aversive odour.

### Hunger vs fear experiment (E1)

`sim/tools/state_experiment.py`, scenario `sim/scenarios/threat.yaml`: one odour source; while the
robot is within 0.5 m of it, a looming object is thrown at it every 4 s.
- **Design (2 × 2):** fed (hunger starts at 0) vs hungry (starts at 1), with the threat vs without.
  10 animals (brain seeds) × 10 independent trials per cell (`--reset-on-reboot`, so hunger starts
  at its condition's value every trial). Animal k has the same trial seeds and starts in every cell
  and runs its four cells back to back. Batch lockstep wait 2 s; no lockstep timeouts.
- **Habituation was off** (`edge: hab_enabled: false` in the scenario), so repeated threats could
  not habituate and confound hunger with fear. The scenario keeps it off, and the tool refuses to
  run if it is on.
- Looming objects that reach the robot count as collisions, so collisions are not used here.

| Cell | Reached | Median time to source | Escapes per trial | Threats per trial |
|---|---|---|---|---|
| fed, threat | 65% | 28.6 s | 2.11 | 3.54 |
| hungry, threat | 77% | 25.5 s | 1.36 | 2.83 |
| fed, no threat | 91% | 20.0 s | 0.04 | 0 |
| hungry, no threat | 88% | 19.0 s | 0.06 | 0 |

Paired per animal (difference in trials that reached the source, out of 10; exact sign test):

| Comparison | Mean | Up / down / tie | p |
|---|---|---|---|
| threat − none, fed | −2.6 | 1 / 8 / 1 | 0.039 |
| threat − none, hungry | −1.1 | 1 / 8 / 1 | 0.039 |
| hungry − fed, with threat | +1.2 | 8 / 2 / 0 | 0.11 |
| hungry − fed, without threat | −0.3 | 4 / 4 / 2 | 1.0 |
| interaction (hungry − fed with threat, minus without) | +1.5 | 7 / 2 / 1 | 0.18 |

**Reading it:** the threat clearly deters, for fed and hungry animals alike. Hunger helps only
under threat and in the expected direction: hungry animals reached the guarded source more often
(8 of 10 animals) and got through faster, with fewer escapes and fewer threats triggered. With 10
animals this is suggestive, not established (p = 0.11; interaction p = 0.18); the sample was not
extended. The weaker-than-planned state coupling (valence −0.25 instead of −0.5 at full fear) may
limit the effect.

## Sleep and wake (Phase 5, step 5.9)

- **Falling asleep:** light below 0.10 for 20 s of robot time puts the brain to sleep. It sends
  `cmd/sleep` (`why: dark`, repeated every 5 s), sends no `cmd/motor`, and ignores its inputs.
- **Waking:** light above 0.25 for 2 s wakes it (`cmd/sleep s:false, why: light`). A passing shadow
  cannot trigger sleep (it needs 20 s), and the band 0.10–0.25 is hysteresis: a dim arena keeps an
  awake brain awake and a sleeping brain asleep.
- **User requests** (`brain/sleep`) still work. Light does not wake a user-requested sleep, and after
  a user wake the brain stays awake in the dark until it has seen light again.
- `sleep.auto: false` switches automatic sleep off. The 5.6 learning experiments use it: their dark
  arena (0.05) would otherwise put the animals to sleep mid-trial.

**Consolidation: the two-part synapse.** Each plastic synapse's depression (initial − current
weight) has a **labile** and a **stable** part.
- Learning adds labile depression.
- Forgetting (`neural.forget_tau_s`, off by default) removes only the labile part.
- While asleep, labile depression moves into the stable part with τ = 60 s (`sleep.consolidate_tau_s`).
  The weights themselves do not change; only how much of their depression forgetting can undo.
- This replaces the plan's wording ("strengthen the strongest, normalise the rest") with a mechanism
  that the retention test below can check (approved).
- **No change without sleep, checked:** with no sleep the stable part stays 0. The same training
  sequence on the code before and after this change gave bit-identical weights, valence and drive,
  with forgetting off and with it on.

Snapshots save both parts; older snapshots without the stable part still load (stable = 0).

### Sleep and memory experiment (E3)

`sim/tools/sleep_experiment.py`, scenario `sim/scenarios/sleep_learning.yaml`.
- **Design:** training as in 5.6 (punish the lit source, 15 trials started near it), then 10 min of
  robot time without odour, **dark** (the brain falls asleep after 20 s) or **lit** (awake), each
  ending with 10 s of light (dawn), then a post-test of 8 trials. The arena is dim (0.15), not dark,
  so the animals stay awake in training and tests.
- 10 animals, both conditions per animal with the same seeds, mirrored arena for odd animals.
- Forgetting was on (τ 5 min) and consolidation τ was 60 s, both fixed in advance; hunger was held
  at 0. **The outcome largely follows from these time constants: this demonstrates the mechanism in
  the closed loop. It is not a discovery.**
- **Method fixes before the run:** the first attempt failed because, while the robot slept, lockstep
  did not wait for the brain and the brain fell hundreds of messages behind, so it was still
  "asleep" during the post-test. Now the tool waits after every trial until the brain has processed
  its last sensors message, E3 publishes `brain/weights` every 2 s (not 250 ms), and the brain
  reports a backlog in `brain/status.lag` (contract revision 9).

| Condition | Phase | Lit / dark / timeout per animal | PI | Valence, lit cue | w KC→ap | Brain slept |
|---|---|---|---|---|---|---|
| sleep | train | 13.5 / 1.3 / 0.2 | +0.82 | −0.55 | 0.940 | 0/10 |
| sleep | rest | – | – | −0.55 | **0.952** | 10/10 |
| sleep | post | 3.9 / 3.3 / 0.8 | +0.12 | **−0.30** | 0.952 | 0/10 |
| awake | train | 13.6 / 1.3 / 0.1 | +0.83 | −0.52 | 0.939 | 0/10 |
| awake | rest | – | – | −0.19 | **0.992** | 0/10 |
| awake | post | 4.2 / 3.6 / 0.2 | +0.07 | **−0.03** | 0.995 | 0/10 |

Paired per animal, sleep minus awake (exact sign test):

| Measure | Mean | Up / down / tie | p |
|---|---|---|---|
| w KC→ap after the rest (lower = more retained) | −0.039 | 0 / 10 / 0 | 0.002 |
| post-test valence for the lit cue | −0.27 | 0 / 10 / 0 | 0.002 |
| post-test PI | +0.05 | 4 / 3 / 3 | 1.0 |
| post-test lit approaches | −0.3 | 4 / 3 / 3 | 1.0 |

**Reading it:**
- **Consolidation works** in the weights and the learned valence, in every animal. After the awake
  rest the learned depression was almost entirely forgotten (w 0.992, valence near 0). After sleep
  most of it remained (w 0.952, valence −0.30).
- **It did not change behaviour.** Neither group avoided the lit source in the post-test. With
  forgetting on during training, training reached only valence −0.55 (5.6: −0.86), and the retained
  −0.30 leaves the drive positive (1 + 2 × (−0.30) = 0.4): the robot still approaches, more slowly.
- Every sleep-condition rest had exactly 3 lockstep timeouts. While the brain caught up, the sleeping
  robot went 15 s without a repeated `cmd/sleep` and woke itself (the contract's safety timeout).
  This happened inside the unmeasured rest phase. One training trial had 2 timeouts (host load).

## Lesions and explanations (Phase 5, step 5.10)

**Lesion switches** (`brain/control` `lesion`, contract §3.12; or `--lesion X` at start for batches):

| Target | Effect |
|---|---|
| `rcpt` | receptor groups silenced (no odour reaches the brain) |
| `ln` | antennal-lobe local (cross-inhibition) neurons silenced |
| `mb` | Kenyon cells silenced (no mushroom body output, no learning) |
| `apl` | the global inhibitory neuron silenced (dense Kenyon cell code) |
| `learn` | plasticity off; everything else runs |
| `hun`, `fear` | that state held at 0 (and `brain/control` `state` for it refused) |
| `hab` | refused (`ack.ok: false`): habituation runs on the robot |

- Circuit lesions silence every group tagged with that `lesion_target` in `groups.csv`: its neurons
  never spike. The random draws stay the same, so a brain with a lesion switched on and off again
  behaves bit for bit like one that was never lesioned.
- Active lesions are listed in `brain/state.les`. The baseline controller has no circuit, so only
  `hun` and `fear` apply to it.

**Explanations.** `brain/state.why` lists the main contributors to each decision, strongest first
(at most 3), from these names: `odour_l` / `odour_r` / `odour` (which side the odour pulls to),
`hunger`, `fear`, `learned_avoid` / `learned_approach`, `obstacle`, `no_odour`, `at_source`, and
`dark` / `user` / `stale` from the core. Each has a score from 0 to 1:
- odour side: PN contrast × confidence;
- fear and hunger: their share of the change in drive (at full strength each moves the valence by
  about 0.25, so the drive by about 0.5);
- learned_*: the rest of the drive change, beyond what fear, hunger and untrained noise (0.3)
  explain;
- obstacle: how much the motor layer slows down.

This is an attribution rule, not a counterfactual simulation; `tests/test_lesions.py` checks that
it names the right causes in scripted situations (odour side, fear, hunger, learned avoidance,
obstacle, no odour). The logger (Phase 8) will store it with every `brain/state`.

### Lesion experiment (E4)

`sim/tools/lesion_experiment.py`.
- **Seeking:** the neural brain intact, with the mushroom body lesioned and with the receptors
  lesioned, 50 independent trials each on `default` (base seed 1, per-trial seeds, batch lockstep
  wait 2 s).
- **Learning:** 10 punish-lit animals with the mushroom body lesioned, on the 5.6 protocol
  (hunger held at 0, automatic sleep off).

| lesion | reached (95% CI) | median time to source | collisions per trial |
|---|---|---|---|
| intact | 43/50 = 86% (74%–93%) | 22.1 s | 0.18 |
| mb | 39/50 = 78% (65%–87%) | 19.7 s | 0.14 |
| rcpt | 12/50 = 24% (14%–37%) | 25.8 s | 0.48 |

| phase | lit / dark / timeout per animal | PI | pref.lt | pref.odr | w KC→ap | w KC→av |
|---|---|---|---|---|---|---|
| pre | 4.5 / 3.3 / 0.2 | +0.15 | – | – | 1.000 | 1.000 |
| train | 13.4 / 1.2 / 0.4 | +0.84 | – | – | 1.000 | 1.000 |
| post | 3.8 / 4.0 / 0.2 | -0.02 | – | – | 1.000 | 1.000 |

**Reading it:**
- **Mushroom body off: seeking intact.** 78% reached against 86% intact; the 95% intervals overlap
  (65–87% vs 74–93%), and the median time is similar (19.7 vs 22.1 s). Steering comes from the
  antennal lobe, which the lesion does not touch.
- **Receptors off: only wandering.** 24% of trials still reached the source, by chance while
  wandering (the designed motor layer), against 86% with odour; the robot no longer steers.
- **Mushroom body off: no learning.** Every animal received 12 to 15 punishments, yet the KC → output
  weights stayed exactly 1.000 and, with the output neurons silent, there was no valence at all
  (`pref` stays empty). Intact animals on the same protocol depress the approach weights (5.6;
  the final regression repeats that comparison on this code). The post-test preference moved
  from +0.15 to −0.02 with nothing learned: pre- and post-tests use different trial seeds, the same
  drift as in the 5.6 control animals.
- No lockstep timeouts in any of the 460 trials (150 seeking, 310 learning).

## Save and restore (Phase 5, step 5.11)

A snapshot (`.local/brain/snapshots/<name>.json`, format 2) holds everything the brain has learned
or is in:
- the learned KC → output weights, each with its **labile and stable** parts, and the learned
  preferences (`pref`);
- **hunger, fear** and the robot's **habituation** level as last reported;
- the **sleep state** (asleep, why, and whether automatic sleep is armed);
- the **active lesions**, and the base seed (for the record).

It is written on demand (`brain/control` `save`) and on a clean shutdown (`latest`), and read on
start-up and with `load`. Format 1 snapshots (weights, hunger and fear only) still load; the rest
then starts from defaults.

**Lesions are not restored by default.** A brain loading a snapshot starts intact and logs which
lesions the snapshot had. Restoring them is an explicit option, `persistence.restore_lesions: true`
or `--restore-lesions`, and the brain logs a warning when it does. Loading always replaces the
current lesion set with the snapshot's (or with none).

**Individuality.** Each brain build has its own seed (`--seed`): the Poisson inputs, noise and the
motor layer's random choices follow from it, so two brains on the same circuit behave slightly
differently, and the same seed reproduces the same behaviour exactly (with the batch method, see
"Seeds" above). This is a feature, not noise to hide:
- seeking success on the same scenario differs between base seeds 1 / 2 / 3: 82 / 84 / 80% on
  `default`, 54 / 56 / 44% on `obstacles` (Neural vs baseline, above);
- untrained animals start with their own preferences: across the 30 control animals of the 5.6
  experiment, the pre-test preference index ranged from −1.0 to +0.75 (SD 0.42), from 0 to 7 of 8
  approaches to the lit source.

## Brian2 check (Phase 5, step 5.12)

An independent rebuild of the designed circuit's receptor → antennal lobe → mushroom body path in
Brian2 2.10.1 (`analysis/brian2/`, analysis venv on D:, numpy code target), compared with FlyBot's
engine. The output and dopamine neurons are left out; nothing feeds back from them into this path.
- **How:** `engine_rates.py` (brain venv) runs the engine; `brian2_rates.py` (analysis venv) reads the
  circuit files itself (`groups.csv`, `connections.csv`, `manifest.yaml`) and writes the engine's
  model as Brian2 equations in the same update order: each step the synaptic current decays, then
  v is updated by Euler and checked against threshold; a spike reaches its targets so that it acts
  one step later, as in the engine.
- **Cases:** weak odour in the dark, odour mostly on the left, strong odour under the lamp, lamp only.
  10 seeds × 5 s each, in both simulators.
- **Pass criterion (fixed in advance):** every group's mean rate within 10% of the engine's; groups
  below 1 Hz within 0.1 Hz.

**Finding: the refractory convention.** With Brian2's refractory set to `t_ref_ms`, inputs and
projection neurons matched but the fast local neurons fired 10–22% faster in Brian2. The engine
holds a neuron for `t_ref_ms` steps *after* its spike step, so its effective refractory period is
one step longer than the parameter suggests. With Brian2's refractory set to `t_ref_ms + 1` ms, the
two agree. The engine was kept as it is and the convention is documented in the format spec below
(approved).

**Result** (refractory `t_ref_ms + 1` ms in Brian2), mean rates over 10 seeds:

| Case | Group | Engine | Brian2 | Difference |
|---|---|---|---|---|
| odour left, dark | PN left / right | 53.95 / 19.61 Hz | 54.06 / 19.51 Hz | +0.2% / −0.5% |
| | LN left / right | 109.16 / 76.11 Hz | 109.03 / 76.27 Hz | −0.1% / +0.2% |
| | APL | 10.74 Hz | 10.58 Hz | −1.5% |
| | KC active at least once | 7.4% | 7.4% | |
| strong odour, lamp | LN left / right | 173.79 / 173.92 Hz | 173.79 / 174.24 Hz | 0.0% / +0.2% |
| | APL | 132.70 Hz | 133.16 Hz | +0.3% |
| | KC active at least once | 14.7% | 14.5% | |
| lamp only | APL | 56.56 Hz | 57.34 Hz | +1.4% |
| weak odour, dark | **LN right** | **1.10 Hz** | **0.86 Hz** | **−22% (fails)** |

35 of 36 group comparisons pass. Receptor and light inputs agree within 0.5%; with moderate and
strong odour every group agrees within 1.5%; under the weakest odour the left projection neurons
differ by +5.2% (within the criterion), and the right local neurons are the exception below. The full tables are in `.local/experiments/brian2/report.md`
(regenerate with the two scripts).

**The one exception:** the right local neurons under the weakest odour (0.05 / 0.02) fire at
1.10 Hz in the engine and 0.86 Hz in Brian2. That is about 6 standard errors (engine seed-to-seed
SD 0.13 Hz), so it is systematic, not noise. It is confined to a group driven just at threshold;
in the same case the neurons it inhibits (PN right, 0.59 vs 0.55 Hz) and the Kenyon cells match.
Its cause is not explained; near threshold, tiny differences in how the two simulators order
updates within a step can change whether a spike happens.

## Phase 5 regression after steps 5.7–5.12

Re-run on the final code of step 5.12 with the same method and seeds as before.

**Seeking** (3 base seeds × 50 independent trials per controller and scenario; before = the
re-measurement in "Neural vs baseline" above, after step 5.6):

| Scenario | Controller | Before: seeds 1 / 2 / 3, pooled | Now: seeds 1 / 2 / 3, pooled (95% CI) | Median time before / now | Collisions per trial before / now |
|---|---|---|---|---|---|
| default | neural | 82 / 84 / 80%, 82% | 86 / 74 / 78%, **79%** (72–85%) | 21.5 / 21.6 s | 0.25 / 0.30 |
| default | baseline | 84 / 88 / 80%, 84% | 84 / 88 / 80%, **84%** (77–89%) | 20.0 / 20.0 s | 0.23 / 0.23 |
| obstacles | neural | 54 / 56 / 44%, 51% | 44 / 56 / 46%, **49%** (41–57%) | 29.1 / 26.7 s | 2.17 / 1.79 |
| obstacles | baseline | 68 / 56 / 58%, 61% | 68 / 56 / 58%, **61%** (53–68%) | 26.7 / 26.7 s | 1.39 / 1.39 |
| two_sources | neural | 90 / 94 / 94%, 93% | 86 / 92 / 94%, **91%** (85–94%) | 23.4 / 27.0 s | 0.00 / 0.03 |
| two_sources | baseline | 100 / 96 / 96%, 97% | 100 / 96 / 96%, **97%** (93–99%) | 21.9 / 22.1 s | 0.05 / 0.05 |

- **Baseline: identical to before**, trial for trial. Its code did not change, and the batch method
  reproduces runs exactly.
- **Neural: within noise of before** on every scenario (the intervals overlap widely). It now
  includes hunger and fear (5.7), whose speed scaling (R1) slows seeking when the valence is low;
  the median time on `two_sources` rose by 3.6 s, and collisions on `obstacles` fell.
- 4 lockstep timeouts in 450 neural trials (host load), none in the baseline's.

**Learning** (5.6 protocol, 10 animals, punish lit vs control twins, hunger held at 0, automatic
sleep off; before = the same animals 0–9 in the 5.6 experiment):

| Measure | Before (5.6) | Now |
|---|---|---|
| lit approaches vs control twin | −2.4 (1 / 7, p = 0.070) | **−2.8** (0 / 8, p = 0.008) |
| dark approaches vs control twin | +1.4 (7 / 2, p = 0.18) | **+1.5** (6 / 0, p = 0.031) |
| timeouts vs control twin | +1.0 (8 / 1, p = 0.039) | +1.3 (8 / 0, p = 0.008) |
| ΔPI, punish lit | −0.50 (1 / 9, p = 0.021) | −0.43 (2 / 8, p = 0.11) |
| learned valence lit / dark | −0.86 / −0.41 | −0.65 / −0.36 |

The 5.6 conclusions hold: punishment moves animals from the lit to the dark source and the learned
valence is cue-biased with partial generalisation. No lockstep timeouts in these 620 trials.

## Circuit file format (version 1)

Circuits are **data, not code** (plan, Phase 5 design rule). The network engine
(`flybot_brain/network/engine.py`) runs any circuit folder that follows this format: the designed
circuit and, from step 5.13, the FlyWire-derived one. The config chooses which.
`flybot_brain/network/circuit.py` loads and validates a folder, and rejects anything that breaks a
rule below with a precise message. Generator scripts write circuits with `write_circuit()`,
which reloads what it wrote, so a generator can never produce an invalid circuit.

```
<circuit>/
  manifest.yaml     identity, source, inputs, outputs, projections
  groups.csv        one row per neuron group
  connections.csv   one row per connection (sparse)
  neurons.csv       optional: one row per neuron
  modulation.csv    optional: which modulator neurons gate which plastic synapses
  NOTICE            data-derived circuits: licence and required citations
```

### `manifest.yaml`

```yaml
format: 1
name: designed
description: ...
source: {kind: designed}            # or {kind: dataset, dataset: "FlyWire 783", licence: CC BY-NC 4.0, citations: [...]}
generator: {script: brain/tools/make_designed_circuit.py, seed: 1}
designed_parts: "which parts are designed rather than data"
inputs:                              # signal -> Poisson input group
  odour_left: {group: rl, encoding: poisson_rate, base_hz: 2.0, gain_hz: 150.0}
outputs:                             # readout name -> groups
  steer_left: [pl]
projections:                         # one per pre -> post group pair
  - {name: rl_pl, pre: rl, post: pl, scale: 0.05}
  - name: kc_ma
    pre: kc
    post: ma
    scale: 0.02
    plastic: true
    plasticity: {rule: three_factor, lr: 0.01, tau_elig_ms: 1000, w_min: 0, w_max: 30}
neurons: neurons.csv                 # optional
modulation: modulation.csv           # optional
```

- **`scale`** (> 0) converts each connection's `weight` into synaptic input: designed strength, or a
  FlyWire synapse count. A pathway can be retuned without touching `connections.csv`.
- **`plastic` / `plasticity`:** a plastic projection must give `rule` (only `three_factor` in v1),
  `lr` > 0, `tau_elig_ms` > 0 and `w_min` ≤ `w_max`, in the same units as `weight`. Its initial
  weights must lie within those bounds, and it may not contain `mod` connections. Optional
  `eligibility`: `accumulate` (default; each presynaptic spike adds 1 to the trace) or `saturate`
  (a spike sets the trace to 1, a coincidence tag that does not grow with firing rate). Plastic
  projections that share presynaptic neurons must use the same `tau_elig_ms` and `eligibility`.
- **Inputs** bind to `poisson` groups. Rate = `base_hz + gain_hz × value`, so odour 0–1 maps to a firing rate.

### `groups.csv`

`group,size,model,tau_m_ms,v_thresh,v_reset,t_ref_ms,tau_syn_ms,bias,noise,side,spk_key,lesion_target`

- **`model`:** `lif` or `poisson`. Poisson input groups leave the LIF parameters empty and may not
  receive connections.
- **Neuron model:** leaky integrate-and-fire on a dimensionless voltage (rest 0), with an exponential
  synaptic current and 1 ms steps.
- **`t_ref_ms` is the number of 1 ms steps a neuron is held at reset *after* the step in which it
  spiked.** The effective refractory period in the usual sense (spike step included) is therefore
  `t_ref_ms + 1` ms, and the shortest interval between two spikes is `t_ref_ms + 1` ms: the designed
  circuit's `t_ref_ms: 2` caps a neuron at 333 Hz. `tests/test_refractory.py` pins this. The Brian2
  check (step 5.12) found it; the engine was kept as it is and the convention documented (approved).
  **For step 5.13:** refractory periods taken from the literature (or any other simulator) must have
  1 ms subtracted when entered as `t_ref_ms`.
- **`side`:** `L`, `R` or `-`.
- **`spk_key`:** must be a contract `brain/state.spk` key. Several groups may share one; they are summed.
- **`lesion_target`:** a contract lesion target (used in step 5.10).

### `connections.csv`

`projection,pre,post,weight,type`

- **`pre` and `post`** are indices inside the projection's groups. Each (pre, post) appears at most once.
- **`weight`** is ≥ 0, in raw units (multiplied by the projection's `scale`).
- **`type`** is `exc` (+), `inh` (−) or `mod`. It follows the presynaptic neuron (Dale's law): the
  loader rejects a neuron with two types.
- **`mod` connections** (dopamine, serotonin) are **not membrane input**. The engine keeps them apart
  for the modulation system: which synapses a modulator gates comes from `modulation.csv`.

### `neurons.csv` (optional)

`group,index,external_id,cell_type,compartment`

Per-neuron identity: for FlyWire, the root id, cell type and mushroom body compartment.

### `modulation.csv` (optional)

`modulator_group,modulator_index,target_group,target_from,target_to,compartment`

Says which modulator neuron gates the plastic synapses onto which target neurons:
- give either a half-open index range `[target_from, target_to)`, or a `compartment` label from
  `neurons.csv` (FlyWire: dopamine neuron → MBON compartment);
- the target group must be the post group of a plastic projection.

### `state_modulation` (optional manifest key)

How internal states (step 5.7) act on the circuit. Each entry couples one state to one target:

```yaml
state_modulation:
  - {state: hunger, kind: gain, projection: rl_pl, k: 0.15}   # effective weights x (1 + k * hunger)
  - {state: fear,   kind: bias, group: mv,         k: 0.6}    # group bias + k * fear
```

- `state` is `hunger` or `fear` (0–1, from the brain's state dynamics).
- `kind: gain` scales a **fixed** (non-plastic) projection's effective weights by `1 + k·state`;
  several entries on the same projection add their `k·state` terms. `kind: bias` adds `k·state` to
  the bias of every neuron in a LIF group.
- Only the keys `state`, `kind`, `k` and `projection` (gain) or `group` (bias) are allowed.
- The circuit holds the coupling; how fast the states rise and fall is brain configuration
  (`brain.yaml` `states:`).

### Engine and performance

- **How it runs:** each control tick integrates `dt_ms` 1 ms steps, where `dt_ms` is the robot-time
  step from contract §7.1. That is 0 on a reboot tick and at most 100 ms after a gap.
- **Spike propagation:** connections are merged into one edge list sorted by presynaptic neuron, so
  a step only touches the edges of neurons that spiked.
- **Randomness** (Poisson inputs, noise) is seeded and re-seeded on reset.
- **Spike counts per group** feed `brain/state.spk`.

**Performance** on a synthetic circuit at FlyWire scale (3,054 neurons, 24,240 connections,
`network/synthetic.py`), measured on the development laptop:

| Activity | 50 ms tick, median | p95 | max |
|---|---|---|---|
| sparse KCs (~1.5 Hz) | 3.5 ms | 3.9 ms | 7.3 ms |
| busier KCs (~5 Hz) | 4.5 ms | 5.0 ms | 5.6 ms |

The budget is 50 ms per tick; the target was under 10 ms. `tests/test_network.py` re-checks it on every run.

## Tests

From `brain\`: `.venv\Scripts\python -m pytest`. That runs 340 tests (201 brain, 139 contract):
- the core: tick, time base, reboots, gaps, warm-up, stale, paused, sleep, control, the reset flag,
  per-trial seeds and the backlog guard (`brain/status.lag`);
- the command line (`--no-persistence`, `--lesion`, `--restore-lesions`);
- message parsing in the link;
- baseline decisions;
- the circuit loader, accepting and rejecting every format rule;
- the engine (step 5.1), including the FlyWire-scale performance check;
- receptors (5.2) and antennal lobe contrast (5.3) on the designed circuit;
- the neural controller's readout and motor-layer split (5.4);
- plasticity in the engine: gating by the modulation map, eligibility (accumulating and
  saturating), bounds, forgetting;
- the mushroom body (5.5): 4–12% of Kenyon cells active, different cues give different patterns,
  APL needed for sparseness;
- learning (5.6): dopamine neurons, punishment / reward / delayed punishment change the valence,
  graded change per pairing, `brain/weights`, snapshots of learned weights;
- internal states (5.7): `state_modulation` format and engine, the designed coupling, the silence
  guard for the output neurons, hunger and fear dynamics, slower seeking at low drive;
- sleep (5.9): the two-part synapse (no change without sleep), consolidation, sleep and wake with
  hysteresis, user sleep;
- lesions and explanations (5.10); save and restore (5.11); the refractory convention (5.12);
- the shared contract consistency tests.
