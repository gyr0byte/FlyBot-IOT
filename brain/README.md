# brain/: the FlyBot brain program

One Python program, `python -m flybot_brain`, made of separate modules:

| Module | Status | Job | MQTT user |
|---|---|---|---|
| `core.py` + `controllers/` | **Phase 4–5** | Contract rules, control ticks, baseline and neural controllers | `brain` |
| `network/` + `circuits/` | **Phase 5** | Spiking network engine with dopamine-gated plasticity; circuits as data (designed now, FlyWire in 5.13) | – |
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
.venv\Scripts\python -m flybot_brain --controller neural  # spiking network (steps 5.1–5.6)
.venv\Scripts\python -m flybot_brain --controller neural --no-persistence   # a naive brain
```

Other options: `--config <yaml>`, `--seed N`, `-v`. **Ctrl+C** stops it cleanly: it saves snapshot
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

From `brain\`: `.venv\Scripts\python -m pytest`. That runs 260 tests (136 brain, 124 contract):
- the core: tick, time base, reboots, gaps, warm-up, stale, paused, sleep, control, the reset flag
  and per-trial seeds;
- the command line (`--no-persistence`);
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
- the shared contract consistency tests.
