# sim/: FlyBot simulator (digital twin)

A virtual FlyBot and arena that behaves like the real robot **on MQTT**. It connects as user
`sim`, publishes exactly what the ESP32 will (`sensors`, `events`, retained `status` with last
will, `ping`, `reward` from its buttons) and obeys `cmd/motor`, `cmd/manual`, `cmd/mode`,
`cmd/sleep` and `pong`. The brain cannot tell it from the robot. Every message in and out is
validated against `docs/schemas`.

**Only one of the simulator or the real robot runs at a time.** Both use the `flybot/01/...` topics.

## Setup (once)

Everything stays on D:. The commands are the same as for `infra\.venv`:

```powershell
$env:PIP_CACHE_DIR = "D:\Tools\cache\pip"; $env:TMP = "D:\Tools\tmp"; $env:TEMP = "D:\Tools\tmp"
$env:PYTHONPYCACHEPREFIX = "D:\Tools\cache\pycache"
python -m venv sim\.venv
sim\.venv\Scripts\python -m pip install -r sim\requirements.txt
sim\.venv\Scripts\python -m pip install -e contract      # shared contract code, see contract/README.md
```

The broker must be running: `infra\start-broker.cmd local`. MQTT settings come from the root
`.env` (`MQTT_HOST=127.0.0.1`, user `sim`).

## Running

Run these from the `sim` folder (`cd sim`):

| What | Command |
|---|---|
| Live view (real time) | `.venv\Scripts\python -m flybot_sim live --scenario default` |
| One headless trial | `.venv\Scripts\python -m flybot_sim run --scenario default --lockstep` |
| Batch → CSV (lockstep) | `.venv\Scripts\python -m flybot_sim batch --scenario default --trials 20 --controller baseline` |
| Batch in real time | add `--realtime` (needed for latency figures) |
| Test driver (stand-in brain) | `.venv\Scripts\python tools\drive.py "straight:3,turn:2,silent:2,straight"` |

Options: `--scenario <name or path.yaml>`, `--seed N`, `--start <index>` (live/run), `--duration <s>`,
`--out <csv>` (batch). The default batch output is `sim/results/<scenario>-<mode>-<time>.csv` (git-ignored).

**Live view keys:**
- arrows move the selected odour source, TAB selects the next one;
- L throws a looming object at the robot, N toggles day/night;
- G and B are the reward and punish buttons;
- SPACE pauses, R puts the robot back at its start, H toggles the heat map, Q quits.

The heat map is the *true* plume, so you can see what the sensors are sampling.

**Test driver.** Until the brain exists, `tools/drive.py` answers every `sensors` message with a
`cmd/motor`. It runs a script of `straight`, `back`, `turn`, `right`, `arc`, `stop` and `silent`.
`silent` sends nothing, which exercises the watchdog → fallback → recovery cycle. It logs in
with the **brain** credentials, because only the brain may publish `cmd/motor`, so never run it
together with the real brain. It also answers pings.

## Faster than real time: lockstep (contract §7.1)

In lockstep the simulator publishes `sensors` with `seq = n`, waits for the `cmd/motor` with
`rs = n`, then simulates the next 50 ms at once. With the test driver that is about
**20× real time**: 8 one-minute trials take about 21 s.
- **When it doesn't wait:** in `manual`, `estop` or asleep it doesn't wait. Otherwise it waits at
  most 100 ms.
- **Free run:** after 3 timeouts in a row it runs freely, still listening 2 ms per step, so the
  watchdog and fallback work without a brain, and a brain that comes back is picked up at once.

This only works because the brain is **driven by sensor messages and measures time in robot
time** (`sensors.up`), as revision 4 requires. Latency and round-trip columns are therefore
**empty in lockstep results**. Measure them with `--realtime`.

## Scenarios (`scenarios/*.yaml`)

A scenario lists only what differs from the defaults in `flybot_sim/config.py`. Unknown keys are
an error. It can set:
- the arena, `obstacles` (circle `x y r` or rect `x y w h`) and `sources` (`x y strength spread_m`);
  the **first source is the target** that counts as "reached" within `success_radius_m`;
- plume patchiness, light (`day`, levels, `ldr_fitted`) and `start_positions`;
- `seed` and `duration_s`;
- every sensor or robot parameter, and `edge:` overrides for any firmware constant.

Bundled: `default`, `obstacles`, `two_sources` (strong target plus a weak decoy), `night` and
`learning`.

**Learning experiments** (plan step 5.6) use four more scenario keys:
- `light.patches`: lamps `{x, y, r, level, edge_m}`. Inside radius `r` the light is `level`; it
  fades to the ambient level over `edge_m`. The LDR reads the light at the robot's position.
- `sources[].label` names a source (`lit`, `dark`). `goal: any` ends a trial at whichever source
  the robot reaches first, and records its label.
- `experiment.phases`: each phase has `name`, `trials` and optional `rewards: {label: +1 | -1}`.
  Arriving at a rewarded source makes the simulator press the training button: `reward` with
  `src: btn` and the robot's `bid`. The press comes `reward_delay_s` after arrival, and the trial
  keeps running for `hold_after_reward_s` after it, so the brain can pair the reward with what it
  senses.
- A phase's starts can be relative to a labelled source: `relative_to: lit` with
  `relative_starts: [{dx, dy, heading_deg}]`. They are written for a source on the left and
  mirrored when the source is on the right. `check_training_starts()` verifies that every such
  start is nearer its own source than any other.

`scenarios/learning.yaml` is a dark arena with two equal odour sources and a lamp over the left
one. It runs pre-test 8 trials from the midline, training 15 trials started near the lit source,
and post-test 8 trials.

`tools/learning_experiment.py` runs whole experiments: conditions × animals, a fresh brain process
per animal, and every odd animal in the mirrored arena. See [brain/README.md](../brain/README.md#learning-phase-5-steps-55-56)
for the design and results.

**Batch trials:** each trial is a fresh robot boot. Its seed is the scenario seed plus the trial
number, and the start positions rotate, so any single trial can be reproduced.

## How it is built

| Module | What |
|---|---|
| `config.py` | `.env` and YAML scenarios (strict, seeded). |
| `world.py` | Arena, obstacles, patchy odour plume, light, looming objects, ray casting. |
| `robot.py` | Differential drive: speed and acceleration limits, L/R motor mismatch, slip, collisions (counted once per contact). |
| `sensors.py` | **Physics:** MQ-3 pair (±6 cm, 30° out, first-order lag, drift, noise, warm-up, saturation), ultrasonic cone (noise, missed echoes, steep-angle misses, 25 Hz), LDR, battery. |
| `edge_sensing.py` | **Firmware:** odour warm-up (`null`), baseline, 0–1 scaling, drift correction; distance median filter; battery smoothing. |
| `edge.py` | **Firmware:** state machine (`boot cloud manual escape fallback sleep estop`), command TTL, 500 ms watchdog, fallback wander, leaving fallback after 3 good commands, looming escape (time-to-contact, own motion removed), obstacle stop, §5.1 defaults, §6 sequence rules, motor mixer, battery cut-off. |
| `edge_config.py` | Every firmware constant: the future `firmware/include/config.h`. |
| *(shared)* | Contract code (schemas, `Outbox`, `StreamTracker`, `RobotClock`) lives in [`contract/`](../contract/README.md). |
| `link.py` | MQTT as user `sim` with the last will; `FakeLink` for offline tests. |
| `sim.py` | Simulation core: 5 ms physics, schedules, trial metrics. |
| `runner.py` | Real-time and lockstep loops. |
| `view.py` · `batch.py` · `driver.py` | Live view, batch CSV, test driver. |

`edge.py`, `edge_sensing.py` and `edge_config.py` are written for porting to the ESP32. They use
plain Python, integer milliseconds, injected randomness, and no numpy, MQTT or threads.

Things to know:
- **Odour baseline:** the baseline is captured where the robot stands at the end of warm-up.
  Starting near the source makes it read low, exactly as on the real robot, so warm up in clean air.
- **Command timing in lockstep:** a command takes effect at the next 5 ms physics step, so there
  is no link delay. Real-time runs have the real broker delay.

## Tests

From `sim/`: `.venv\Scripts\python -m pytest`. This also runs the contract consistency tests in `contract/tests`. There are 94 tests:
- kinematics and world geometry;
- each sensor model and the firmware sensor processing;
- the edge state machine, including watchdog → fallback → recovery, e-stop override, sleep, escape
  and the §5.1 defaults;
- message schemas;
- the live view, run headless;
- end-to-end lockstep, real-time and batch runs over the broker.

The broker tests are skipped when the broker isn't running.
