# brain/: the FlyBot brain program

One Python program, `python -m flybot_brain`, made of separate modules:

| Module | Status | Job | MQTT user |
|---|---|---|---|
| `core.py` + `controllers/` | **Phase 4–5** | Contract rules, control ticks, baseline and neural controllers | `brain` |
| `network/` + `circuits/` | **Phase 5** | Spiking network engine; circuits as data (designed now, FlyWire in 5.13) | – |
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
.venv\Scripts\python -m flybot_brain --controller neural  # spiking network (steps 5.1–5.4)
```

Other options: `--config <yaml>`, `--seed N`, `-v`. **Ctrl+C** stops it cleanly: it saves snapshot
`latest` and publishes `brain/status` offline.

**With the simulator.** Start the brain in one terminal, then in `sim\`:
- `.venv\Scripts\python -m flybot_sim live` for the live view;
- `.venv\Scripts\python -m flybot_sim batch --trials 50 --controller baseline` for a lockstep batch.
  Start the brain with `--reset-on-reboot` for this.

Don't run `sim\tools\drive.py` at the same time: it also logs in as `brain`.

## What it does on the wire (contract revision 7)

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
   slows down from `slow_cm`. This and rule 4 are the shared, designed motor layer
   (`controllers/motor.py`, config `motor:`).
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

## Neural controller (Phase 5, steps 5.1–5.4)

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
| Obstacle avoidance, slowing near obstacles, wandering without odour | **designed rules**, the same motor layer as the baseline | `controllers/motor.py`, `motor:` config |

The circuit itself is designed for now: sizes, wiring and weights are design choices (the manifest
says so). Step 5.13 swaps it for FlyWire connectome data without changing the engine.

**The designed circuit** (170 neurons, 1,400 connections):
- 50 receptors per side;
- 25 projection neurons per side, each fed by 12 same-side receptors. They sit at half threshold, so
  they respond from an odour value of about 0.03 but stay silent in clean air;
- 10 local inhibitory neurons per side, driven by their own side's receptors. They inhibit the
  opposite side's projection neurons, which roughly doubles the left/right contrast for weak and
  moderate signals.

### Neural vs baseline

Lockstep over MQTT, brain process with `--reset-on-reboot`, 50 trials per scenario, both
controllers on the same build:

| Scenario | Reached: neural | Reached: baseline | Median time to source: neural / baseline | Collisions per trial: neural / baseline |
|---|---|---|---|---|
| default (60 s) | **46/50 (92%)** | 43/50 (86%) | 26.1 s / 18.1 s | 0.14 / 0.34 |
| obstacles (90 s) | **30/50 (60%)** | 31/50 (62%) | 27.2 s / 23.8 s | 0.98 / 1.00 |
| two_sources (90 s) | **50/50 (100%)** | 49/50 (98%) | 14.7 s / 12.2 s | 0.00 / 0.00 |

0 messages were lost in all 300 trials.

**Reading the table:**
- The neural controller reaches the source as often as the baseline, within 2 points on
  `obstacles` and better on the other two.
- It takes **longer**, about 8 s more on `default`. Its smoothed spike-rate readout reacts more
  slowly than the baseline's direct sensor difference.
- Faster driving cut the `default` time to about 21 s, but lost about 6 points on `obstacles`, so it
  was not adopted.

**Before tuning** (in-process, 32 trials): 50% / 16% / 62%. Three things fixed that:
- 400 ms readout smoothing, on top of the MQ-3's lag, made the robot orbit the source and scrape
  walls. Now 100 ms.
- Lateral inhibition sharpens plume patchiness too, so weak signals now steer less (`steer_full_hz` 120).
- The arrival threshold fired about 20 cm from the source. Now 145 Hz.

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
  weights must lie within those bounds, and it may not contain `mod` connections. The spec is
  checked now and used from step 5.6.
- **Inputs** bind to `poisson` groups. Rate = `base_hz + gain_hz × value`, so odour 0–1 maps to a firing rate.

### `groups.csv`

`group,size,model,tau_m_ms,v_thresh,v_reset,t_ref_ms,tau_syn_ms,bias,noise,side,spk_key,lesion_target`

- **`model`:** `lif` or `poisson`. Poisson input groups leave the LIF parameters empty and may not
  receive connections.
- **Neuron model:** leaky integrate-and-fire on a dimensionless voltage (rest 0), with an exponential
  synaptic current and 1 ms steps.
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
  for the modulation system and ignores them until step 5.6.

### `neurons.csv` (optional)

`group,index,external_id,cell_type,compartment`

Per-neuron identity: for FlyWire, the root id, cell type and mushroom body compartment.

### `modulation.csv` (optional)

`modulator_group,modulator_index,target_group,target_from,target_to,compartment`

Says which modulator neuron gates the plastic synapses onto which target neurons:
- give either a half-open index range `[target_from, target_to)`, or a `compartment` label from
  `neurons.csv` (FlyWire: dopamine neuron → MBON compartment);
- the target group must be the post group of a plastic projection.

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

From `brain\`: `.venv\Scripts\python -m pytest`. That runs 218 tests:
- the core: tick, time base, reboots, gaps, warm-up, stale, paused, sleep, control and the reset flag;
- message parsing in the link;
- baseline decisions;
- the circuit loader, accepting and rejecting every format rule;
- the engine (step 5.1), including the FlyWire-scale performance check;
- receptors (5.2) and antennal lobe contrast (5.3) on the designed circuit;
- the neural controller's readout and motor-layer split (5.4);
- the shared contract consistency tests.
