# FlyBot – Software Implementation Plan

**Project:** FlyBot – a cloud-connected, fruit-fly-inspired neural controller for an ESP32 robot
**Module:** Cloud Computing and Internet of Things
**Document purpose:** step-by-step plan for building every piece of FlyBot software, in the order it should be built, with a clear "done when" check for each step.

---

## 1. Overview

### 1.1 The software pieces

| # | Piece | Runs on | Language / stack | Job |
|---|---|---|---|---|
| 1 | Message contract | – (a document) | Markdown + JSON Schema | Defines every MQTT topic and message. All other pieces depend on it. |
| 2 | MQTT broker | Laptop, then Azure VM | Eclipse Mosquitto (Docker) | Carries every message between the pieces. |
| 3 | Simulator (digital twin) | Laptop | Python | A virtual robot and odour source that sends the same messages as the real robot. |
| 4 | Brain service | Laptop, then Azure VM | Python + NumPy | Runs the fly-inspired spiking network and sends motor commands. |
| 5 | ESP32 firmware | Robot | C++ (Arduino core, PlatformIO) | Sensors, motors, edge reflexes, Wi-Fi fallback. |
| 6 | Logger + database | Laptop, then Azure VM | Python + SQLite | Stores every message for history and evaluation. |
| 7 | Dashboard | Browser, served from Azure VM | Next.js + mqtt.js | Live view, controls, history. |
| 8 | Telegram bot | Azure VM | Python (python-telegram-bot) | Alerts and good-bot/bad-bot training. |
| 9 | Cloud infrastructure | Azure VM | Docker Compose, Caddy, Let's Encrypt, DuckDNS | Hosts pieces 2, 4, 6, 7, 8 securely. |
| 10 | Evaluation tools | Laptop | Jupyter, pandas, matplotlib | Turns logs into results for the report. |

### 1.2 Rules that apply to every piece

1. **Pieces only talk through MQTT.** No piece calls another directly. This is what lets the brain move from laptop to cloud without changes, and lets the simulator stand in for the robot.
2. **Every message carries a sequence number and a timestamp.** This gives latency and message-loss figures for the report without extra work later.
3. **Build locally, deploy last.** Everything runs on the laptop first. The cloud is a deployment step, not a development environment.
4. **Simulator before hardware.** The brain is developed and tuned against the simulator, so hardware delays never block software progress.
5. **A non-neural baseline first.** A simple rule-based steering controller is built before the neural brain. It proves the pipeline works and later becomes the comparison point for "why not just if-else?".
6. **Configuration, not hard-coding.** Thresholds, neuron counts, rates and credentials live in config files and environment variables.
7. **Secrets never go in the repository.** Only an example environment file is committed.

### 1.3 Build order at a glance

| Phase | What | Depends on | Rough effort |
|---|---|---|---|
| 0 | Tools, repository, conventions | – | 0.5 day |
| 1 | Message contract | 0 | 0.5–1 day |
| 2 | Local broker | 1 | 0.5 day |
| 3 | Simulator | 1, 2 | 2–3 days |
| 4 | Brain skeleton + rule-based baseline | 1, 2, 3 | 1–2 days |
| 5 | Neural brain (step by step) | 4 | 7–12 days |
| 6 | ESP32 firmware | 1, 2, hardware | 6–9 days (in parallel with 3–5) |
| 7 | Robot–brain integration | 5 (at least 5.4), 6 | 2–4 days |
| 8 | Logger + database | 1, 2 | 1 day |
| 9 | Dashboard | 1, 2, 8 | 4–6 days |
| 10 | Telegram bot | 1, 2 | 1 day |
| 11 | Cloud deployment | 2, 4, 8, 9, 10 | 2–3 days |
| 12 | Evaluation tooling and test runs | 7, 8 | 3–5 days |
| 13 | Hardening and demo preparation | everything | 2–3 days |

Effort figures are estimates for one person working part-time on this module. Phase 5 is the most uncertain: tuning a spiking network to behave well usually takes longer than expected.

**Parallel tracks:** while waiting for parts, do phases 0–5 and 8. When parts arrive, start phase 6 alongside phase 5.

---

## 2. Phase 0 – Tools, repository and conventions

### Step 0.1 – Install tools
- VS Code with the PlatformIO extension (firmware).
- Python 3.11 or newer, with a virtual environment per Python service.
- Node.js LTS (dashboard).
- Docker Desktop (broker and later the whole cloud stack).
- Git and a GitHub account.
- MQTT Explorer (desktop app for watching and sending MQTT messages by hand).
- Telegram account (for the bot later).

**Done when:** each tool runs from a terminal and Docker can start a test container.

### Step 0.2 – Create the repository
One GitHub repository with these top-level folders:
- **docs/** – message contract, diagrams, test records, report material.
- **firmware/** – PlatformIO project for the ESP32.
- **brain/** – Python brain service and its tests.
- **sim/** – Python simulator.
- **logger/** – Python MQTT-to-SQLite logger.
- **bot/** – Telegram bot.
- **dashboard/** – Next.js app.
- **infra/** – Docker Compose files, Mosquitto config, Caddy config, deployment notes.
- **analysis/** – Jupyter notebooks for evaluation.

Add a README at the root describing the project, how to run it locally and how to deploy it. Add a gitignore that excludes environment files, virtual environments, build output and database files.

**Done when:** the empty structure is pushed to GitHub.

### Step 0.3 – Agree conventions (write them in docs/conventions.md)
- **Units:** distance in cm, time in ms, voltages in V, motor commands as −1.0 to +1.0 per wheel.
- **Odour values:** sent as change from each sensor's own baseline, scaled 0–1, never raw ADC counts.
- **Timestamps:** milliseconds. The ESP32 sends its own uptime in ms plus the sequence number; services add wall-clock time when they receive a message.
- **Robot ID:** every topic includes the robot ID (01), so a second robot could be added later without changes.
- **Naming:** lowercase topic names, short JSON keys.
- **Branching:** main branch always works; one branch per phase or feature.

**Done when:** the conventions page exists and is committed.

---

## 3. Phase 1 – Message contract

This is the most important document in the project. Write it before any program.

### Step 1.1 – List every topic
Start from the MQTT Topics tab in the component Sheet:

| Topic | Direction | Purpose |
|---|---|---|
| flybot/01/sensors | Robot → cloud | Odour left/right, distance, light, battery |
| flybot/01/events | Robot → cloud | Looming escape, obstacle stop, fallback entered/left, button presses |
| flybot/01/status | Robot → cloud | Current state and health; retained; last-will message says "offline" |
| flybot/01/cmd/motor | Brain → robot | Left and right wheel commands with a time-to-live |
| flybot/01/cmd/mode | Dashboard/bot → robot | Manual, autonomous, sleep, emergency stop |
| flybot/01/reward | Button/bot/dashboard → brain | Reward (+1) or punishment (−1) |
| flybot/01/brain/state | Brain → dashboard | Hunger, fear, sleep, decision, spike summary |
| flybot/01/brain/weights | Brain → dashboard/logger | Learned weights; retained |
| flybot/01/brain/control | Dashboard → brain | Lesion switches, parameter changes, reset |
| flybot/01/ping, flybot/01/pong | Robot ↔ cloud | Round-trip latency measurement |

### Step 1.2 – Define each payload
For every topic write: every field name, its type, unit, allowed range and an example message. Decide the QoS level and whether the message is retained.

### Step 1.3 – Write machine-checkable schemas
Create one JSON Schema file per message type in docs/schemas. The Python services and tests validate against these, so a mismatch is caught immediately.

### Step 1.4 – Define timing
- Sensors: 10–20 messages per second.
- Motor commands: one per brain tick (10–20 per second), each with a time-to-live of about 300 ms.
- Brain state to dashboard: 5 per second (the dashboard doesn't need more).
- Heartbeat/status: on change plus every 10 seconds.
- Ping: once per second.

### Step 1.5 – Define version handling
Add a version field to every message, starting at 1. Any change to a payload increases it.

**Done when:** docs/message-contract.md and the schema files exist, and you can describe any message without looking at code.

---

## 4. Phase 2 – Local MQTT broker

### Step 2.1 – Run Mosquitto in Docker on the laptop
Create a Compose file in infra/ that starts Mosquitto with a config file and a persistent data folder.

### Step 2.2 – Configure listeners
- Port 1883: plain MQTT, local development only.
- Port 9001: WebSockets, for the dashboard during development.
- (Ports 8883 and 8884 with TLS are added in Phase 11.)

### Step 2.3 – Add authentication from the start
- Turn off anonymous access.
- Create separate users: esp32, sim, brain, logger, dashboard, bot.
- Write an access-control list so each user can only publish and subscribe to the topics it needs. For example, the robot can publish sensors but not motor commands; the brain can publish motor commands but not sensors.

### Step 2.4 – Test by hand
With MQTT Explorer, log in as each user and confirm allowed topics work and forbidden ones are refused.

**Done when:** the broker starts with one command, and the access rules behave as written.

---

## 5. Phase 3 – Simulator (digital twin)

The simulator pretends to be the robot. It lets you build and tune the brain without hardware, and gives clean, repeatable test runs for the report.

### Step 3.1 – World model
- A 2D arena (for example 2 m × 2 m) with walls.
- One or more odour sources with position and strength.
- Optional obstacles and a "light level" that can be switched between day and night.

### Step 3.2 – Robot model
- Differential-drive movement: two wheel speeds give forward speed and turning rate.
- Realistic limits: maximum speed, acceleration, a small amount of random wheel slip.

### Step 3.3 – Sensor models (make them realistically bad)
- **Odour:** concentration falls off with distance from the source, plus noise and slow drift. Model the MQ-3's slow response with a time lag (it reacts and recovers over seconds). Two sensors placed at the real mounting positions (left and right, about 12 cm apart).
- **Distance:** ultrasonic cone of about 15–30 degrees, noise, occasional missed readings, update rate limited to about 25 per second.
- **Light:** a value that follows the day/night setting.
- **Battery:** a slowly falling voltage.

The realism matters: a brain tuned on perfect sensors will fail on the real robot.

### Step 3.4 – Speak the message contract
- Publish sensors, status and pong exactly as the real ESP32 will.
- Subscribe to motor commands and mode commands, and obey the time-to-live (stop if commands go stale).

### Step 3.5 – Simulate the edge reflexes too
Implement the same looming escape and Wi-Fi-loss fallback logic that will go in the firmware, so the brain experiences the same events it will see on the real robot.

### Step 3.6 – Visualisation and control
- A simple live top-down view showing the arena, robot, odour sources and robot path.
- Keyboard controls to move the odour source, trigger a looming object, toggle light, and press reward/punish.

### Step 3.7 – Scenario files and headless mode
- Scenario config files: start positions, source positions, noise levels, duration.
- A headless mode (no window) that runs many trials quickly and saves results to CSV. This is what makes brain tuning practical.

**Done when:** you can drive the simulated robot by hand from MQTT Explorer and watch it move, and a headless batch of trials produces a CSV.

---

## 6. Phase 4 – Brain service skeleton and rule-based baseline

### Step 4.1 – Service structure
A long-running Python program with these parts:
- **MQTT layer:** connects, subscribes, publishes, reconnects automatically.
- **Input buffer:** keeps the latest sensor reading and marks it stale if it is older than a set limit.
- **Control loop:** runs at a fixed rate (start at 20 Hz). Each tick: read inputs → decide → publish command.
- **Config loader:** reads all parameters from a YAML file.
- **Logging:** readable console logs with timestamps.

### Step 4.2 – Safety behaviour
- If sensor data is stale, send "stop" and report it.
- Echo the sequence number of the sensor message that caused each command, so the robot can measure round-trip time.
- Respond to ping with pong immediately.

### Step 4.3 – Rule-based baseline controller
A simple Braitenberg-style rule: turn toward the stronger odour side, slow down near obstacles, stop when the odour is high enough ("arrived").

### Step 4.4 – Controller switch
Make the controller swappable by config: "baseline" or "neural". Both receive the same inputs and produce the same outputs.

### Step 4.5 – Tests
Unit tests for message parsing, stale-data handling and the baseline rule, using the JSON schemas.

**Done when:** with the simulator running, the baseline steers the simulated robot to the odour source in most trials, and the whole loop runs through the broker.

---

## 7. Phase 5 – Neural brain, step by step

Build the network one layer at a time and test each layer in the simulator before adding the next. Never add two layers at once: when something breaks, you need to know which part did it.

### Step 5.1 – Neuron engine
- Leaky integrate-and-fire neurons, simulated with a small time step (about 1 ms) inside each 50 ms control tick.
- Neuron groups (populations) and connections between groups (weight matrices) as reusable building blocks.
- Record spikes per group for the dashboard raster plot.
- Unit tests: a constant input gives a steady firing rate; no input gives no spikes; stronger input gives faster firing.
- Measure speed: one control tick must finish in well under 50 ms.

**Done when:** tests pass and a network of about 2,000 neurons runs comfortably in real time on the laptop.

### Step 5.2 – Receptor input layer (smell receptors)
- About 50 neurons per side (left and right).
- Odour value sets their firing rate (rate coding with some randomness).

**Done when:** firing rate rises and falls smoothly with odour strength in the simulator.

### Step 5.3 – Antennal lobe
- Projection neurons that receive receptor input, one group per side.
- Local inhibitory neurons that connect across both sides, so a stronger side suppresses the weaker one. This sharpens left/right contrast and controls overall gain.

**Done when:** the left/right difference in projection-neuron firing is clearer than the raw sensor difference.

### Step 5.4 – Steering readout (first working neural behaviour)
- Left versus right projection-neuron activity sets the turning direction.
- Total activity sets forward speed.
- Smooth the output over a few ticks so the robot doesn't twitch, especially given the MQ-3's slow response.
- Tune in the simulator with headless batches until it matches or beats the baseline success rate.

**Done when:** the neural controller reaches the odour source in the simulator about as reliably as the baseline. This is the most important milestone in the project; once it works, the robot can be connected (Phase 7).

### Step 5.5 – Mushroom body (sparse coding)
- About 1,000–2,000 Kenyon cells.
- Each Kenyon cell receives a few random connections from projection neurons.
- One global inhibitory neuron (like the fly's APL neuron) keeps only about 5–10% of Kenyon cells active at once.
- If the learning demo uses light as the second cue, add light as an extra input here, since two MQ-3s cannot reliably tell different smells apart.

**Done when:** different input patterns (odour only, light only, both) produce clearly different, sparse Kenyon-cell patterns.

### Step 5.6 – Output neurons and learning
- Two output groups: approach and avoid.
- Kenyon cells connect to both; these are the connections that learn.
- Reward and punishment signals (dopamine-like neurons) are triggered by the reward topic (button, Telegram or dashboard).
- Learning rule: a connection changes only when its Kenyon cell was recently active AND a reward or punishment arrives (three-factor rule). Reward strengthens approach; punishment strengthens avoid.
- Approach-minus-avoid balance changes speed and willingness to move toward the stimulus.
- Publish the learned weights to the weights topic.

**Done when:** in the simulator, a cue paired with punishment several times is later avoided, and one paired with reward is approached, without any rule saying so.

### Step 5.7 – Internal states
- **Hunger:** rises slowly over time, drops to zero on "eating" (reaching the odour source). Higher hunger raises odour-seeking gain and willingness to approach despite danger.
- **Fear:** jumps when a looming-escape event arrives from the robot, then decays over tens of seconds. Higher fear lowers speed and makes avoidance stronger.
- Both are published in brain state.

**Done when:** the same situation produces different behaviour depending on hunger and fear (for example, a hungry robot approaches the odour near a "threat", a fed one doesn't).

### Step 5.8 – Habituation
- Repeated harmless stimuli weaken their own connections (short-term synaptic depression), which recovers with rest.
- A new or different stimulus produces a full response again.

**Done when:** repeating the same harmless trigger gives a smaller response each time, and a pause restores it.

### Step 5.9 – Sleep and wake
- Darkness from the light sensor (or a dashboard command) switches the brain to sleep: motors stopped, inputs ignored.
- During sleep, apply a small consolidation step to learned weights (strengthen the strongest, normalise the rest).
- Waking resumes normal operation.

**Done when:** covering the light sensor puts the robot to sleep and the weights view shows a before/after change.

### Step 5.10 – Lesion switches and explanation log
- Each major group (receptors, antennal lobe inhibition, mushroom body, learning, inhibitory neuron) can be switched off live through the brain control topic.
- For each decision, record which groups and inputs contributed most, for the "why did it do that?" view.

**Done when:** switching off the mushroom body stops learning but not seeking, and the explanation log names the right causes.

### Step 5.11 – Save and restore brain state
- Save weights, internal state and parameters to a file on demand and on shutdown; reload on start.
- Each brain build can use a random seed, which also gives the "individuality" talking point.

**Done when:** restarting the brain keeps what it learned.

### Step 5.12 – Offline validation with Brian2 (optional)
Rebuild the small circuit in Brian2 and confirm it behaves like your NumPy version. This gives a credible check and good figures for the report.

---

## 8. Phase 6 – ESP32 firmware

Do this as soon as parts arrive, in parallel with Phase 5. Test each step on the bench with the robot's wheels off the ground.

### Step 6.1 – Project setup
- PlatformIO project for the ESP32 DevKit with the Arduino framework.
- One config header with every pin from the Pin Map tab, and every threshold in one place.
- A separate secrets file (Wi-Fi and MQTT credentials) excluded from the repository.

**Done when:** the board flashes and prints to the serial monitor.

### Step 6.2 – Sensors, one at a time
1. **MQ-3 left and right:** read through the voltage dividers on ADC1 pins only. Wait for warm-up, measure each sensor's baseline, then report change from baseline. Add smoothing and slow baseline-drift correction.
2. **Ultrasonic:** distance in cm with timeouts for missed echoes and a filter against single bad readings.
3. **Light sensor:** a normalised value.
4. **Battery voltage:** through its divider, converted to pack voltage.

**Done when:** all values print steadily on the serial monitor, and moving a cotton ball with alcohol from left to right clearly changes which MQ-3 reads higher. **This is the project's feasibility test: do it as early as possible.**

### Step 6.3 – Motors
- PWM for both motors through the TB6612, with direction pins and standby pin.
- A mixer that turns left/right commands (−1 to +1) into PWM, with a duty cap to protect the 6 V motors, gentle ramping, and a dead zone for very small commands.

**Done when:** the robot drives forward, backward and turns on command from the serial monitor.

### Step 6.4 – Wi-Fi and MQTT (unencrypted, local broker first)
- Connect to Wi-Fi with automatic reconnection and backoff.
- Connect to the laptop broker as user esp32 with a last-will message on the status topic.
- Publish sensors at the agreed rate; subscribe to motor and mode commands.
- Use the agreed message format, sequence numbers and timestamps.

**Done when:** MQTT Explorer shows live sensor messages, and sending a motor command by hand moves the robot.

### Step 6.5 – Task structure
Split the firmware into separate tasks so Wi-Fi delays never block the reflex:
- **Reflex task:** highest priority, fixed rate (about 40 Hz), owns the ultrasonic sensor.
- **Sensor task:** 20 Hz, odour, light, battery.
- **Comms task:** Wi-Fi and MQTT, on the other processor core.
- **Motor task:** applies the latest allowed command.

**Done when:** pulling Wi-Fi does not change the reflex timing.

### Step 6.6 – Looming escape reflex (edge)
- Estimate how fast the distance is shrinking and calculate time-to-contact (distance divided by approach speed).
- If time-to-contact drops below a threshold within a set range, trigger escape immediately: reverse, then turn away.
- Require two agreeing readings to avoid false triggers.
- Publish an escape event to the cloud afterwards.
- Measure detection-to-motor time on the ESP32 itself for the report.

**Done when:** a hand moved toward the robot at a brisk, steady speed from about 60 cm triggers a dodge reliably.

### Step 6.7 – Safety state machine and command watchdog
- States: boot, cloud control, escape, fallback, sleep, emergency stop.
- If no valid motor command arrives for 500 ms, or a command's time-to-live has passed, switch to fallback.
- Escape always overrides cloud commands.
- Emergency stop from the mode topic overrides everything.

**Done when:** every state is reachable and visible in the status topic, and stopping the brain makes the robot enter fallback within half a second.

### Step 6.8 – Fallback behaviour
- Slow wandering with obstacle avoidance, using only local sensors.
- On reconnect and fresh commands, hand control back to the cloud.

**Done when:** unplugging the router mid-run keeps the robot moving safely, and plugging it back restores cloud control.

### Step 6.9 – Local input and output
- RGB LED colours for each state (calm, hungry, scared, asleep, fallback, offline).
- Buzzer patterns for escape, Wi-Fi lost and low battery.
- Reward and punish buttons with debounce, published to the reward topic.
- Onboard LED for connection status.

**Done when:** each state shows the right colour and each button press appears in MQTT Explorer.

### Step 6.10 – Health reporting and watchdog
- Heartbeat with signal strength, free memory, battery voltage and uptime.
- Hardware watchdog so a frozen program restarts itself.
- Low-battery warning and safe shutdown of motors at a cut-off voltage.

**Done when:** the dashboard (later) or MQTT Explorer (now) shows health data, and a deliberately frozen task causes a clean restart.

### Step 6.11 – Time sync and latency support
- Sync the clock over the internet on start-up.
- Send ping once per second and calculate round-trip time from pong.
- Also calculate sensor-to-command round-trip time using the echoed sequence number.

**Done when:** round-trip times are visible in the status topic.

### Step 6.12 – Wi-Fi setup portal
If the robot can't join a known network, it opens its own setup hotspot where you can pick a network and enter the password from your phone. Essential for demo day on unfamiliar or blocked Wi-Fi.

### Step 6.13 – Encryption (done in Phase 11)
Switch to TLS on port 8883 with the broker's root certificate when the cloud broker is ready.

### Step 6.14 – Over-the-air updates (optional)
Flash new firmware over Wi-Fi so the robot doesn't need a cable once assembled.

---

## 9. Phase 7 – Robot and brain integration (laptop)

### Step 7.1 – Replace the simulator with the real robot
Same broker, same brain. Stop the simulator, power the robot. Nothing else should need to change.

### Step 7.2 – Re-tune for reality
Expect differences: slower odour response, more noise, motors that don't match each other. Adjust smoothing, gains and thresholds in config only. Keep notes of every change; they become report material.

### Step 7.3 – Measure latency
Record round-trip times for several minutes on the laptop setup. This is the "local" baseline for the edge-vs-cloud comparison.

### Step 7.4 – Run first real trials
Odour seeking from three or four marked start positions, baseline controller versus neural controller.

**Done when:** the real robot finds the odour source in most trials in a still room, and both controllers have recorded results.

---

## 10. Phase 8 – Logger and database

### Step 8.1 – Database design (SQLite)
Tables:
- **sensors:** time, sequence, odour left/right, distance, light, battery.
- **events:** time, type, details.
- **commands:** time, sequence, left/right, source (brain, manual, fallback).
- **brain_state:** time, hunger, fear, sleep, decision, summary spike counts.
- **weights:** time, snapshot of learned weights.
- **latency:** time, ping round-trip, sensor-to-command round-trip.
- **trials:** start time, end time, scenario, start position, controller, outcome, notes (filled by the evaluation tools).

### Step 8.2 – Logger service
- Subscribes to all FlyBot topics, validates each message against the schema, writes it to the right table.
- Writes in small batches for efficiency; reconnects automatically.
- Counts invalid or missing messages (gaps in sequence numbers) for the reliability results.

### Step 8.3 – Retention and export
- Keep raw sensor data for a limited period on the VM to save space; keep events, trials and latency permanently.
- A one-command export to CSV for analysis and backup.

### Step 8.4 – Read API for the dashboard
A small read-only HTTP API (either inside the dashboard app or a tiny Python service) that returns history for charts: last hour of sensors, event timeline, latency statistics, trial results.

**Done when:** a 10-minute run is fully recorded, and the export opens cleanly in a notebook.

---

## 11. Phase 9 – Dashboard (Next.js)

### Step 9.1 – App setup
- Next.js app with TypeScript.
- Connects to the broker over WebSockets as user dashboard (secure WebSockets after Phase 11).
- Simple password login so the public URL isn't open to everyone.

### Step 9.2 – Live overview page
- Connection status of robot, brain and broker (from retained status and last-will messages).
- Current state with a coloured badge (searching, feeding, fleeing, sleeping, fallback).
- Hunger and fear gauges.
- Battery, signal strength and round-trip time.

### Step 9.3 – Live sensor charts
Odour left and right, distance and light over the last minute, updating in real time.

### Step 9.4 – Brain view
- **Spike raster:** recent spikes per neuron group.
- **Weights heatmap:** approach and avoid weights, updating during learning.
- **Decision panel:** the latest decision and the "why" explanation from the brain.

### Step 9.5 – Controls
- Mode buttons: autonomous, manual, sleep, emergency stop.
- Manual drive pad.
- Reward and punish buttons.
- Lesion switches for each neuron group.
- Brain save, load and reset.

### Step 9.6 – History page
Charts from the read API: event timeline, latency histogram, trial success rates, a "day in the life" timeline of meals, startles and sleep.

### Step 9.7 – Performance and layout
- Limit chart redraw rates so the browser stays smooth.
- Make it readable on a projector and usable on a phone.

**Done when:** during a live run, every panel updates, every control works, and the page stays smooth for 10 minutes.

---

## 12. Phase 10 – Telegram bot

### Step 10.1 – Create the bot
Register with BotFather, store the token as a secret, and restrict the bot to your own chat ID (and any you add for the demo).

### Step 10.2 – Commands
- **/status** – state, hunger, fear, battery, uptime.
- **/good** and **/bad** – publish reward or punishment.
- **/sleep** and **/wake** – mode commands.
- **/stop** – emergency stop.
- **/learned** – a short summary of current preferences.

### Step 10.3 – Alerts
- Robot offline (from the last-will message) and back online.
- Low battery.
- Escape events (with a limit so it doesn't spam).
- Fallback mode entered and left.
- A message when learning produces a clear preference ("Learned: cue A = good").

**Done when:** every command works from your phone and each alert arrives once per event.

---

## 13. Phase 11 – Cloud deployment (Azure)

### Step 11.1 – Account and VM
- Activate Azure for Students with your college email.
- Create an Ubuntu 24.04 LTS VM in the free-tier size, in the Central India region (closest to Nepal, lowest round-trip time).
- Add 2 GB of swap space, because the VM has only 1 GB of RAM.
- Set up SSH key login and disable password login.

### Step 11.2 – Firewall
Allow only: SSH from your own IP, 443 (web), 8883 (secure MQTT for the robot), 8884 (secure WebSockets for the dashboard). Block everything else.

### Step 11.3 – Domain and certificates
- Get a free DuckDNS subdomain pointing at the VM.
- Use Caddy for HTTPS on the dashboard, with automatic Let's Encrypt certificates.
- Get a certificate for Mosquitto as well, and make sure it renews automatically.

### Step 11.4 – Production Compose file
One Compose file in infra/ that runs: Mosquitto (with TLS listeners and the same users and access rules), brain, logger, dashboard, Telegram bot and Caddy. Each with automatic restart and a health check. Secrets come from an environment file on the VM only.

### Step 11.5 – Deploy
Copy the repository to the VM, add the environment file, start the stack. Check every container is healthy.

### Step 11.6 – Switch the robot to the cloud
- Update firmware: cloud broker address, port 8883, TLS with the root certificate, robot credentials.
- Confirm the robot connects, the brain drives it, the dashboard shows it, and the logger records it.

### Step 11.7 – Measure cloud latency
Repeat the latency measurement from Phase 7 with the cloud setup. This gives your core edge-vs-local-vs-cloud comparison.

### Step 11.8 – Backup broker and local fallback
- Create a free HiveMQ Cloud cluster and store its address and credentials as a second broker option in the firmware.
- Rehearse the full local fallback: laptop runs the same Compose stack, phone hotspot as Wi-Fi, robot pointed at the laptop.

### Step 11.9 – Monitoring
- Telegram alert when the brain or broker container goes down.
- A daily database export copied off the VM.
- Keep an eye on the Azure credit balance.

**Done when:** the robot runs from the cloud for 30 minutes without intervention, and switching to the local fallback takes under 5 minutes.

---

## 14. Phase 12 – Evaluation tooling and test runs

### Step 12.1 – Trial recorder
A small tool (a dashboard page or a script) to mark the start and end of each trial, choose the scenario and start position, and record the outcome. Saved into the trials table.

### Step 12.2 – Analysis notebooks
One notebook per test in proposal section 10:
1. **Odour seeking:** success rate and time-to-source per start position, neural versus baseline.
2. **Looming escape:** detection-to-motor time on the ESP32, success rate.
3. **Latency:** local versus cloud round-trip, median and 95th percentile, histograms; compared with the edge reflex time.
4. **Learning:** preference before and after training, over repeated sessions.
5. **Resilience:** time to enter fallback after Wi-Fi loss, time to resume cloud control.
6. **Reliability:** message loss from sequence gaps, reconnect counts, battery runtime.
7. **Behaviour:** hunger and fear effects, habituation curve, sleep consolidation before/after.

### Step 12.3 – Run the tests
Fixed number of trials per test (decide in advance, for example 10 per start position), same room conditions, recorded with overhead video. Note anything unusual.

### Step 12.4 – Simulator comparison
Run the same scenarios in the simulator and compare with the real robot. The difference itself is a good discussion point for the report.

**Done when:** every test has a results table and at least one chart ready for the report.

---

## 15. Phase 13 – Hardening and demo preparation

### Step 13.1 – Soak test
Run the full cloud system for at least an hour. Fix any crash, memory growth or disconnect pattern.

### Step 13.2 – Failure drills
Practise each failure deliberately: Wi-Fi off, brain stopped, broker restarted, battery low, venue Wi-Fi blocking port 8883. Confirm the robot stays safe and the system recovers.

### Step 13.3 – Demo script
A timed 3–5 minute sequence: odour seeking, hand-swipe dodge, training with good/bad, showing the learned choice, hunger versus fear, lesion switch, light-covered sleep, router unplugged and recovered. Practise it several times in the demo room if possible.

### Step 13.4 – Backup material
- A recorded full run in case the live demo fails.
- Pre-made screenshots of the dashboard.
- Saved results from the offline full-connectome demo, if attempted.

### Step 13.5 – Repository clean-up
README with architecture, setup and deployment steps; example environment file; licence; tagged release for submission.

**Done when:** the demo runs end to end twice in a row without intervention.

---

## 16. Definition of done for the whole project

- [ ] Message contract and schemas documented and used by every service.
- [ ] Simulator runs scenarios headless and produces results.
- [ ] Neural brain seeks odour, learns, and shows hunger, fear, habituation and sleep.
- [ ] Firmware handles sensors, motors, looming reflex, fallback, state machine and health reporting.
- [ ] Logger records everything; data exports cleanly.
- [ ] Dashboard shows live data, brain activity and controls; history page works.
- [ ] Telegram bot commands and alerts work.
- [ ] Full stack runs on Azure with TLS; local fallback rehearsed.
- [ ] Every evaluation test has results and charts.
- [ ] Demo rehearsed; backup video recorded.
- [ ] Repository tagged and documented for submission.

---

## 17. What to do first

1. Phase 0: install tools and create the repository.
2. Phase 1: write the message contract.
3. Phase 2: get the local broker running.
4. As soon as the MQ-3 sensors arrive, run the left/right feasibility test in Step 6.2. If it fails, the odour-seeking design needs to change, and it's better to know in week one than week six.
