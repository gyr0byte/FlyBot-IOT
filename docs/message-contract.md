# FlyBot MQTT message contract

**Contract revision:** 9 (2026-11-25). Each message type's current `v` is in the §1 table.

This is the source of truth for every message between the FlyBot programs. Programs only
talk through MQTT. If code and this document disagree, the code is wrong. The
machine-checkable form is in [`schemas/`](schemas/). General rules (units, key style,
precision) are in [`conventions.md`](conventions.md).

Contents:
1. [Topic overview](#1-topic-overview)
2. [Common fields](#2-common-fields)
3. [Topics](#3-topics)
4. [MQTT users and access control](#4-mqtt-users-and-access-control)
5. [Who drives the robot](#5-who-drives-the-robot)
6. [Receiver rules: sequence numbers, reboots, duplicates](#6-receiver-rules-sequence-numbers-reboots-duplicates)
7. [Timing](#7-timing)
8. [Versioning](#8-versioning)
9. [Security notes](#9-security-notes)
10. [Changelog](#10-changelog)

---

## 1. Topic overview

All topics start with `flybot/01/` (robot ID `01`). "Robot" means the ESP32 (user `esp32`)
or the simulator (user `sim`), never both at once. The logger is a module of the brain
program and uses the `brain` connection.

| Topic | Published by | Subscribed by | QoS | Retained | Rate | Schema | `v` |
|---|---|---|---|---|---|---|---|
| `sensors` | robot | brain, dashboard | 0 | no | 20 Hz (10–20 allowed) | `sensors` | 2 |
| `events` | robot | brain, dashboard, bot | 1 | no | on occurrence | `events` | 3 |
| `status` | robot (last will: broker) | brain, dashboard, bot | 1 | **yes** | on change + every 10 s | `status` | 3 |
| `cmd/motor` | brain | robot, dashboard | 0 | no | one per `sensors` message (20 Hz) | `cmd-motor` | 1 |
| `cmd/manual` | dashboard | robot, brain | 0 | no | 10 Hz while driving | `cmd-manual` | 2 |
| `cmd/mode` | dashboard, bot | robot, brain, dashboard, bot | 1 | **yes** | on user action | `cmd-mode` | 2 |
| `cmd/sleep` | brain | robot, dashboard | 1 | no | on change + every 5 s while asleep | `cmd-sleep` | 1 |
| `reward` | robot (buttons), dashboard, bot | brain, dashboard | 1 | no | on press | `reward` | 3 |
| `brain/status` | brain (last will: broker) | dashboard, bot | 1 | **yes** | on change + every 10 s | `brain-status` | 3 |
| `brain/state` | brain | dashboard, bot | 0 | no | 5 Hz | `brain-state` | 5 |
| `brain/weights` | brain | dashboard | 1 | **yes** | on change, max 1 per 2 s | `brain-weights` | 1 |
| `brain/control` | dashboard only | brain | 1 | no | on user action | `brain-control` | 4 |
| `brain/sleep` | dashboard, bot | brain only | 1 | no | on user action | `brain-sleep` | 2 |
| `ping` | robot | brain | 0 | no | 1 Hz | `ping` | 2 |
| `pong` | brain | robot | 0 | no | once per ping, immediately | `pong` | 1 |

The brain subscribes to `flybot/01/#`, so the logger also sees and stores every topic.
The dashboard reads every topic except `brain/sleep` (explicit list in the ACL, §4.1).

QoS choices:
- **QoS 0** for high-rate, latest-value-wins messages (`sensors`, `cmd/motor`,
  `cmd/manual`, `brain/state`). A resent old value is worse than a missing one.
- **QoS 0** for `ping`/`pong`, so measured loss and latency are real and not hidden by retries.
- **QoS 1** for messages that must arrive (`events`, `status`, `cmd/mode`, `cmd/sleep`, `reward`,
  `brain/*` status, weights, control and sleep). Receivers drop QoS 1 duplicates (§6).
- **Retained** only for current state: `status`, `brain/status`, `brain/weights`, `cmd/mode`.
  `reward`, `brain/control`, `brain/sleep` and `cmd/sleep` are **never retained**: a restarted
  brain or robot would replay them (re-train, re-reset, or sleep with no brain behind it).

---

## 2. Common fields

Every message starts with these three fields:

| Key | Type | Unit | Range | Meaning |
|---|---|---|---|---|
| `v` | integer | – | ≥ 1, exact value per type in §1 | Schema version of this message type (§8). |
| `seq` | integer | – | 0 … 4294967295 | Sequence number, counted **per sender, per topic**. Starts at 0 when the sender starts, +1 per message, wraps to 0 after 4294967295. |
| `up` | integer | ms | 0 … 9007199254740991 | Sender's uptime when the message was created. |

**Sender names (`src`).** MQTT does not tell subscribers who published a message. So every
message on a topic that can have more than one sender carries `src`, the sender's name.
Receivers keep a separate `seq` stream per sender name (§6), and the logger knows who acted.

| Sender | `src` | Carried on |
|---|---|---|
| Robot buttons (ESP32 or simulator) | `btn` | `reward` |
| Telegram bot | `bot` | `cmd/mode`, `reward`, `brain/sleep` |
| **Each dashboard tab** | `dash-` + 6 lowercase hex digits, e.g. `dash-3fa2c1` (pattern `^dash-[0-9a-f]{6}$`) | **every** message the tab sends: `cmd/manual`, `cmd/mode`, `reward`, `brain/control`, `brain/sleep` |

Dashboard tab names:
- Generated **once at page load** from a cryptographic random source (`crypto.getRandomValues`)
  and kept in memory only, not saved in browser storage. A reload or a second tab gets a new
  name and counts as a new sender.
- The tab's `seq` starts at 0 at page load, and `up` is ms since page load.
- The tab's MQTT client ID uses the same name: `flybot-01-dash-3fa2c1` (§4.2).

Topics with exactly one sender (robot-only or brain-only topics) carry no `src`. `src` is
**bookkeeping only**: it is self-declared, and the broker's access-control list (§4) decides
who may publish.

**Boot ID (`bid`).** Every message the **robot** publishes carries `bid` right after the common fields:
an integer from 1 to 4294967295. The robot draws it at random at each boot (ESP32: `esp_random()`) and
keeps it for that whole boot. 0 is never used. A different `bid` means the robot rebooted, so a
reboot can never be confused with a late message (§6 rule 4). The messages are `sensors`, `events`,
`status` (including the last will), `ping`, and `reward` from the buttons (`src:"btn"`). Other
senders (brain, bot, dashboard tabs) carry no `bid`.

**Wall-clock time** is never in a payload. Receivers record their own UTC arrival time.

---

## 3. Topics

Columns: **Dec.** = decimal places the sender rounds to. "Required" means the key must be present.
Every listed key is required unless marked *optional*. Unknown keys are rejected.

### 3.1 `flybot/01/sensors`

Robot → brain, dashboard. QoS 0, not retained, 20 Hz (10–20 Hz allowed).

| Key | Type | Unit | Range | Dec. | Meaning |
|---|---|---|---|---|---|
| `v`, `seq`, `up` | | | | | Common fields. |
| `bid` | integer | – | 1 … 4294967295 | 0 | Boot ID: random per robot boot (§2). |
| `ol` | number \| null | – | 0.0 … 1.0 | 3 | Left odour: change from the left MQ-3's baseline, scaled 0–1. `null` while warming up or calibrating. |
| `or` | number \| null | – | 0.0 … 1.0 | 3 | Right odour, same as `ol`. |
| `d` | number \| null | cm | 2.0 … 400.0 | 1 | Ultrasonic distance (filtered). `null` = no valid echo (timeout or out of range). |
| `lt` | number \| null | – | 0.0 … 1.0 | 2 | Light level, 0 = dark, 1 = bright. `null` if no LDR is fitted. |
| `bat` | number | V | 0.00 … 10.00 | 2 | Battery pack voltage. |

```json
{"v":2,"seq":1042,"up":52310,"bid":2847193551,"ol":0.214,"or":0.187,"d":38.5,"lt":0.82,"bat":7.61}
```
98 bytes. About 110 bytes worst case with large `seq`, `up` and `bid`.

### 3.2 `flybot/01/events`

Robot → brain, dashboard, bot. QoS 1, not retained, sent when the event happens.
Events raised while offline are queued (at least the last 20) and sent on reconnect. Their
`up` still says when they happened.

| Key | Type | Unit | Range | Dec. | Meaning |
|---|---|---|---|---|---|
| `v`, `seq`, `up` | | | | | Common fields. |
| `bid` | integer | – | 1 … 4294967295 | 0 | Boot ID: random per robot boot (§2). |
| `e` | string | – | see below | – | Event type. |

Extra keys per event type (each type allows **only** its own keys):

| `e` | Extra key | Type | Unit | Range | Dec. | Meaning |
|---|---|---|---|---|---|---|
| `escape` | `ttc` | integer | ms | 0 … 10000 | 0 | Time-to-contact when the looming stimulus was detected. |
| | `d` | number | cm | 2.0 … 400.0 | 1 | Distance at detection. |
| | `resp` | number | – | 0.00 … 1.00 | 2 | Escape strength used (scales the reverse and turn). `0` = detected but habituated: the robot did not move. |
| | `hab` | number | – | 0.00 … 1.00 | 2 | Habituation level just before the stimulus (0 = full response). |
| | `lat` | number | ms | 0.0 … 1000.0 | 1 | Detection-to-motor time measured on the ESP32. Present **only** when `resp` > 0. |
| `obstacle` | `d` | number | cm | 2.0 … 400.0 | 1 | Distance at which the obstacle stop fired. |
| `fallback_on` | `why` | string | – | `cmd_timeout`, `ttl_expired`, `wifi_lost`, `mqtt_lost` | – | Why the robot entered local fallback. |
| `fallback_off` | `dur` | integer | ms | 0 … 86400000 | 0 | Time spent in fallback. |
| `low_battery` | `bat` | number | V | 0.00 … 10.00 | 2 | Pack voltage. |
| | `cut` | boolean | – | | – | `true` = cut-off reached, motors disabled. `false` = warning only. |
| `boot` | `rst` | string | – | `power_on`, `watchdog`, `brownout`, `software`, `panic`, `other` | – | Reset reason. |
| | `fw` | string | – | 1–16 chars, `[0-9A-Za-z.+-]` | – | Firmware version. |

Reward/punish button presses go to `reward` (with `src:"btn"`), not to `events`.

**Habituation (revision 8).** The looming escape habituates on the robot: repeated harmless
stimuli give weaker escapes, recovering with rest, and a clearly stronger (faster-approaching)
stimulus than the recent ones gives the full response again and resets the habituation. `escape` is therefore sent for **every** looming detection, including
fully habituated ones (`resp` 0, no `lat`), so the brain and dashboard can follow habituation even
when the robot does not move. The current level is also in `status.hab` (§3.3).

```json
{"v":3,"seq":7,"up":61234,"bid":2847193551,"e":"escape","ttc":420,"d":31.0,"lat":3.2,"resp":0.82,"hab":0.18}
```
A habituated stimulus (detected, no movement):
```json
{"v":3,"seq":12,"up":75020,"bid":2847193551,"e":"escape","ttc":510,"d":34.0,"resp":0.0,"hab":0.86}
```
Other types:
```json
{"v":3,"seq":8,"up":64010,"bid":2847193551,"e":"obstacle","d":12.4}
{"v":3,"seq":9,"up":70500,"bid":2847193551,"e":"fallback_on","why":"cmd_timeout"}
{"v":3,"seq":10,"up":82750,"bid":2847193551,"e":"fallback_off","dur":12250}
{"v":3,"seq":11,"up":3600000,"bid":2847193551,"e":"low_battery","bat":6.45,"cut":false}
{"v":3,"seq":0,"up":2140,"bid":2847193551,"e":"boot","rst":"power_on","fw":"0.1.0"}
```

### 3.3 `flybot/01/status`

Robot → brain, dashboard, bot. QoS 1, **retained**, sent on any state or mode change and
every 10 s. **Last will:** the robot registers the offline message below (QoS 1, retained) when it
connects, so the broker publishes it if the robot disappears. A robot that shuts down cleanly
publishes the same message itself before disconnecting.

| Key | Type | Unit | Range | Dec. | Meaning |
|---|---|---|---|---|---|
| `v`, `seq`, `up` | | | | | Common fields. |
| `bid` | integer | – | 1 … 4294967295 | 0 | Boot ID: random per robot boot (§2). |
| `st` | string | – | `boot`, `cloud`, `manual`, `escape`, `fallback`, `sleep`, `estop`, `offline` | – | Current robot state (§5). |
| `m` | string | – | `auto`, `manual`, `estop` | – | Mode the robot currently holds. |
| `rssi` | integer | dBm | −127 … 0 | 0 | Wi-Fi signal strength. |
| `heap` | integer | bytes | 0 … 16777216 | 0 | Free heap memory. |
| `bat` | number | V | 0.00 … 10.00 | 2 | Battery pack voltage. |
| `rtt` | number \| null | ms | 0.0 … 60000.0 | 1 | Median ping→pong round-trip time over the last 10 s. `null` if no pongs. |
| `crtt` | number \| null | ms | 0.0 … 60000.0 | 1 | Median sensor→`cmd/motor` round-trip time over the last 10 s, from the `rs` echo. `null` if none. |
| `fw` | string | – | 1–16 chars, `[0-9A-Za-z.+-]` | – | Firmware version. |
| `hab` | number | – | 0.00 … 1.00 | 2 | Current habituation level of the looming escape (0 = full response). |

When `st` is `offline` the message has **only** `v`, `seq`, `up`, `bid`, `st`. It is fixed at connect
time, so `seq` is 0, `up` is the uptime at connect, and `bid` is the current boot's ID.

```json
{"v":3,"seq":120,"up":1200400,"bid":2847193551,"st":"cloud","m":"auto","rssi":-61,"heap":182344,"bat":7.58,"rtt":48.5,"crtt":71.0,"fw":"0.1.0","hab":0.25}
```
Last will:
```json
{"v":3,"seq":0,"up":2310,"bid":2847193551,"st":"offline"}
```

### 3.4 `flybot/01/cmd/motor`

Brain → robot (dashboard watches). QoS 0, not retained, one per brain tick, and the brain ticks once
per `sensors` message (20 Hz, §7.1).
**Only the `brain` user may publish it.** The robot obeys it only in `auto` mode and not while asleep (§5).

The brain publishes `cmd/motor` **only when it has a real decision**. While its sensor data is
stale, while the mode is not `auto`, or while it is asleep, it publishes nothing on this topic. It
never sends filler or "stop" commands. That silence lets the robot's own 500 ms watchdog take over (§5).

| Key | Type | Unit | Range | Dec. | Meaning |
|---|---|---|---|---|---|
| `v`, `seq`, `up` | | | | | Common fields. |
| `l` | number | – | −1.00 … 1.00 | 2 | Left wheel command. |
| `r` | number | – | −1.00 … 1.00 | 2 | Right wheel command. |
| `ttl` | integer | ms | 50 … 1000 | 0 | Time-to-live, counted from when the robot receives it. Default 300. The robot stops the motors when it expires. |
| `rs` | integer | – | 0 … 4294967295 | 0 | `seq` of the newest `sensors` message the brain used for this command. The robot uses it to measure sensor→command round-trip time. |

```json
{"v":1,"seq":881,"up":44120,"l":0.42,"r":0.35,"ttl":300,"rs":1042}
```
66 bytes (under 90 worst case).

### 3.5 `flybot/01/cmd/manual`

Dashboard → robot (brain logs). QoS 0, not retained, 10 Hz while the drive pad is held, then one
`l:0, r:0` message on release. Same fields as `cmd/motor` **plus `src`** (schema `cmd-manual`).
**Only the `dashboard` user may publish it.** The robot obeys it only in `manual` mode.

| Key | Type | Unit | Range | Dec. | Meaning |
|---|---|---|---|---|---|
| `v`, `seq`, `up` | | | | | Common fields. |
| `l`, `r`, `ttl` | | | | | As in `cmd/motor` (§3.4). |
| `rs` | integer | – | 0 … 4294967295 | 0 | `seq` of the newest `sensors` message this tab has received (0 if none yet). |
| `src` | string | – | `^dash-[0-9a-f]{6}$` | – | Sender name of the dashboard tab (§2). |

Each tab keeps its own `seq` stream. If two tabs drive at once, the robot follows whichever valid
command arrived last. Don't do that; it is allowed only so a second tab never gets its commands
dropped as "out of order".

```json
{"v":2,"seq":15,"up":95010,"l":0.5,"r":-0.5,"ttl":300,"rs":1044,"src":"dash-3fa2c1"}
```
84 bytes.

### 3.6 `flybot/01/cmd/mode`

Dashboard, bot → robot, brain, dashboard, bot. QoS 1, **retained**, on user action.
**Only `dashboard` and `bot` may publish it. The brain cannot.** Because it is retained, a
robot that reboots gets its last mode straight back, so an emergency stop survives a power cycle.

| Key | Type | Unit | Range | Dec. | Meaning |
|---|---|---|---|---|---|
| `v`, `seq`, `up` | | | | | Common fields. |
| `m` | string | – | `auto`, `manual`, `estop` | – | Requested mode. |
| `src` | string | – | `bot` or `^dash-[0-9a-f]{6}$` | – | Sender name (§2). |

```json
{"v":2,"seq":4,"up":300120,"m":"estop","src":"dash-3fa2c1"}
```

The retained copy keeps the `src` and `seq` of whichever sender set it last. A receiver that
subscribes later treats it as current state (§6 rule 10) and does not compare its `seq` with
other senders' streams.

### 3.7 `flybot/01/cmd/sleep`

Brain → robot (dashboard watches). QoS 1, **not retained**. Sent on every change, and repeated
every 5 s while asleep. **Only the `brain` user may publish it.** The robot ignores it in `estop`
or `manual` mode (§5).

| Key | Type | Unit | Range | Dec. | Meaning |
|---|---|---|---|---|---|
| `v`, `seq`, `up` | | | | | Common fields. |
| `s` | boolean | – | | – | `true` = sleep, `false` = wake. |
| `why` | string | – | `dark`, `light`, `user` | – | Cause: light sensor went dark / became light, or a `brain/sleep` request. |

```json
{"v":1,"seq":2,"up":610000,"s":true,"why":"dark"}
```

### 3.8 `flybot/01/reward`

Robot buttons, dashboard, bot → brain (dashboard shows). QoS 1, not retained, on press.
Publishers debounce. The brain drops duplicates by (`src`, `seq`) (§6).

| Key | Type | Unit | Range | Dec. | Meaning |
|---|---|---|---|---|---|
| `v`, `seq`, `up` | | | | | Common fields. |
| `bid` | integer | – | 1 … 4294967295 | 0 | Boot ID (§2): **required when `src` is `btn`**, absent otherwise. |
| `r` | integer | – | `1` or `-1` | 0 | `1` = reward ("good bot"), `-1` = punishment ("bad bot"). |
| `src` | string | – | `btn`, `bot` or `^dash-[0-9a-f]{6}$` | – | Sender name (§2). `btn` = robot buttons; the simulator's keyboard reward also uses `btn`. |

```json
{"v":3,"seq":3,"up":120455,"r":1,"src":"bot"}
```
From a dashboard tab:
```json
{"v":3,"seq":0,"up":8120,"r":-1,"src":"dash-3fa2c1"}
```
From the robot's button (with its boot ID):
```json
{"v":3,"seq":2,"up":95200,"bid":2847193551,"r":1,"src":"btn"}
```

### 3.9 `flybot/01/brain/status`

Brain → dashboard, bot. QoS 1, **retained**, on change and every 10 s. **Last will:** the
offline message below (QoS 1, retained), registered by the brain when it connects.

| Key | Type | Unit | Range | Dec. | Meaning |
|---|---|---|---|---|---|
| `v`, `seq`, `up` | | | | | Common fields. |
| `st` | string | – | `online`, `offline` | – | Brain program availability. |
| `ctrl` | string | – | `baseline`, `neural` | – | Active controller. |
| `tick` | number | ms | 0.0 … 10000.0 | 1 | Mean compute time per control tick over the last 10 s (budget: 50 ms). |
| `ovr` | integer | – | 0 … 1000 | 0 | Ticks over budget in the last 10 s. |
| `sens` | string | – | `ok`, `stale`, `none` | – | Sensor input: `ok` = fresh, `stale` = newest `sensors` message older than 250 ms (brain is silent on `cmd/motor`), `none` = no sensor message since the brain started. A change publishes `brain/status` immediately. |
| `lag` | integer | messages | 0 … 100000 | 0 | Inbound messages received but not yet processed by the brain (its queue length when this status was built). When it rises above the brain's warning threshold (brain configuration, default 40 ≈ 2 s of `sensors`) or falls back below it, the brain logs it and publishes `brain/status` immediately. |

When `st` is `offline` the message has **only** `v`, `seq`, `up`, `st` (`seq` 0, `up` at connect).

```json
{"v":3,"seq":31,"up":310050,"st":"online","ctrl":"neural","tick":6.8,"ovr":0,"sens":"ok","lag":0}
```
Sensor data has gone stale:
```json
{"v":3,"seq":32,"up":312400,"st":"online","ctrl":"neural","tick":6.5,"ovr":0,"sens":"stale","lag":0}
```
The brain is falling behind (messages are queuing up faster than it processes them):
```json
{"v":3,"seq":33,"up":318700,"st":"online","ctrl":"neural","tick":21.4,"ovr":3,"sens":"ok","lag":57}
```
Last will:
```json
{"v":3,"seq":0,"up":1520,"st":"offline"}
```

### 3.10 `flybot/01/brain/state`

Brain → dashboard, bot. QoS 0, not retained, 5 Hz.

| Key | Type | Unit | Range | Dec. | Meaning |
|---|---|---|---|---|---|
| `v`, `seq`, `up` | | | | | Common fields. |
| `hun` | number | – | 0.00 … 1.00 | 2 | Hunger. |
| `fear` | number | – | 0.00 … 1.00 | 2 | Fear. |
| `hab` | number | – | 0.00 … 1.00 | 2 | The robot's looming habituation as last reported (`status.hab`, `escape.hab`). |
| `slp` | boolean | – | | – | Brain is asleep. |
| `dec` | string | – | `seek`, `explore`, `approach`, `avoid`, `freeze`, `eat`, `sleep`, `stale`, `paused`, `warmup` | – | Current decision. `stale` = sensor data older than 250 ms (or none yet): the brain is **not** publishing `cmd/motor`, and the robot's watchdog takes over. `paused` = mode is not `auto`. `warmup` = `sensors` are arriving but the odour values are still `null` (MQ-3 warm-up and calibration): the brain sends no `cmd/motor`, because the robot holds itself still in `boot` (§5). `sens` stays `ok`. |
| `why` | array of string | – | 0–3 items, each `^[a-z][a-z0-9_]{0,15}$` | – | Main contributors to the decision, strongest first (e.g. `odour_l`, `hunger`, `fear`, `avoid`). |
| `spk` | object | spikes | keys below, each integer 0 … 100000 | 0 | Spikes per neuron group in the last 200 ms. A group that is not built yet is left out. Empty `{}` with the baseline controller. |
| `les` | array of string | – | unique, from the lesion targets in §3.12 | – | Groups currently switched off. |
| `pref` | object | – | keys `odr`, `lt` (both *optional*), each −1.00 … 1.00 | 2 | Learned preference per cue: + approach, − avoid. Empty `{}` before learning exists. |
| `rs` | integer \| null | – | 0 … 4294967295 | 0 | `seq` of the newest `sensors` message used. `null` before the first one. |
| `ack` | object \| null | – | `{"src": "dash-xxxxxx", "seq": int, "ok": bool}` | – | Result of the last `brain/control` message handled: its sender name and `seq`, so each tab can tell whether the acknowledgement is for its own command. `null` if none since start. |

`spk` keys (all *optional*): `rl`, `rr` receptor left/right · `pl`, `pr` projection left/right ·
`ln` antennal-lobe local inhibitory · `kc` Kenyon cells · `apl` global inhibitory ·
`ma`, `mv` output approach/avoid · `dp`, `dn` dopamine reward/punish.

```json
{"v":5,"seq":2210,"up":442100,"hun":0.63,"fear":0.08,"hab":0.25,"slp":false,"dec":"seek","why":["odour_l","hunger"],"spk":{"rl":41,"rr":22,"pl":18,"pr":7,"ln":12,"kc":96,"apl":3,"ma":5,"mv":1},"les":[],"pref":{"odr":0.4,"lt":-0.6},"rs":1042,"ack":{"src":"dash-3fa2c1","seq":12,"ok":true}}
```
About 290 bytes at 5 Hz.

At startup, before the first sensor message and before any network layer exists:
```json
{"v":5,"seq":0,"up":210,"hun":0.0,"fear":0.0,"hab":0.0,"slp":false,"dec":"stale","why":[],"spk":{},"les":[],"pref":{},"rs":null,"ack":null}
```

### 3.11 `flybot/01/brain/weights`

Brain → dashboard. QoS 1, **retained**. Sent when the weights change (at most once per 2 s),
at startup, and after a load or reset. Not published until the mushroom body exists (Phase 5.6).

Weights from each Kenyon cell to the two output groups are quantised to 0–255:
real weight = `q / 255 × sc`.

| Key | Type | Unit | Range | Dec. | Meaning |
|---|---|---|---|---|---|
| `v`, `seq`, `up` | | | | | Common fields. |
| `n` | integer | – | 1 … 5000 | 0 | Number of Kenyon cells. |
| `sc` | number | – | > 0 … 1000 | 3 | Scale: weight for `q = 255`. |
| `ap` | array of integer | – | `n` items, each 0 … 255 | 0 | Kenyon cell → approach weights, by cell index. |
| `av` | array of integer | – | `n` items, each 0 … 255 | 0 | Kenyon cell → avoid weights, by cell index. |

JSON Schema cannot check that `ap` and `av` have exactly `n` items. Receivers must check it.
About 14 KB for 2,000 Kenyon cells.

```json
{"v":1,"seq":14,"up":512000,"n":8,"sc":1.0,"ap":[12,200,0,45,255,3,90,17],"av":[180,4,66,0,9,240,31,2]}
```

### 3.12 `flybot/01/brain/control`

Dashboard → brain. QoS 1, **not retained**, on user action. **Only the `dashboard` user may
publish it** (the bot cannot; sleep and wake go through `brain/sleep`, §3.13). Several dashboard
tabs may be open, so every message carries the sending tab's `src`. The brain reports the result in
`brain/state.ack` (with that `src` and `seq`), and changes to lesions or controller in
`brain/state.les` and `brain/status.ctrl`.

| Key | Type | Unit | Range | Meaning |
|---|---|---|---|---|
| `v`, `seq`, `up` | | | | Common fields. |
| `src` | string | – | `^dash-[0-9a-f]{6}$` | Sender name of the dashboard tab (§2). |
| `c` | string | – | `lesion`, `param`, `save`, `load`, `reset`, `state` | Command. |

Extra keys per command (each command allows **only** its own keys):

| `c` | Extra key | Type | Range | Meaning |
|---|---|---|---|---|
| `lesion` | `tgt` | string | `rcpt`, `ln`, `mb`, `apl`, `learn`, `hab`, `hun`, `fear` | Target: receptors, antennal-lobe inhibition, mushroom body, global inhibition, learning, habituation, hunger, fear. |
| | `off` | boolean | | `true` = switch the target off, `false` = back on. |
| `param` | `k` | string | `^[a-z][a-z0-9_.]{0,47}$` | Parameter name from the brain config (e.g. `ctrl`, `seek.gain`). Unknown names → `ack.ok = false`. |
| | `val` | number \| boolean \| string | string 1–32 chars | New value. |
| `save` | `name` | string, *optional* | `^[a-z0-9_-]{1,32}$` | Snapshot name. Default `latest`. |
| `load` | `name` | string, *optional* | `^[a-z0-9_-]{1,32}$` | Snapshot name. Default `latest`. |
| `reset` | `what` | string | `weights`, `state`, `all` | What to reset: learned weights, internal state (hunger, fear, habituation), or both. |
| `state` | `hun` | number, *optional* | 0.00 … 1.00 | Set hunger now. |
| | `fear` | number, *optional* | 0.00 … 1.00 | Set fear now. At least one of `hun`, `fear` is required. |

**`state`** sets internal states directly (for demonstrations). The normal dynamics continue from
the new value: hunger keeps rising, fear keeps decaying. A state that is lesioned (`hun` or
`fear` off) is not changed and the brain answers `ack.ok = false`.

**Lesion `hab`.** Habituation runs on the robot (§3.2), and no brain command reaches it there, so
`lesion` with `tgt:"hab"` is answered with `ack.ok = false`.

```json
{"v":4,"seq":12,"up":98000,"src":"dash-3fa2c1","c":"lesion","tgt":"mb","off":true}
```
Other commands:
```json
{"v":4,"seq":13,"up":99500,"src":"dash-3fa2c1","c":"param","k":"ctrl","val":"neural"}
{"v":4,"seq":14,"up":101000,"src":"dash-3fa2c1","c":"save","name":"after_training"}
{"v":4,"seq":15,"up":102000,"src":"dash-3fa2c1","c":"load"}
{"v":4,"seq":16,"up":103000,"src":"dash-3fa2c1","c":"reset","what":"weights"}
{"v":4,"seq":17,"up":104000,"src":"dash-3fa2c1","c":"state","hun":0.9}
{"v":4,"seq":18,"up":104500,"src":"dash-3fa2c1","c":"state","hun":0.2,"fear":0.8}
```

### 3.13 `flybot/01/brain/sleep`

Dashboard, bot → brain. QoS 1, **not retained**, on user action (dashboard sleep/wake button,
Telegram `/sleep` and `/wake`). **Only `dashboard` and `bot` may publish it, and only `brain`
may subscribe.** The brain decides whether to act, then tells the robot through `cmd/sleep`
with `why:"user"`. It never reaches the robot directly.

| Key | Type | Unit | Range | Dec. | Meaning |
|---|---|---|---|---|---|
| `v`, `seq`, `up` | | | | | Common fields. |
| `s` | boolean | – | | – | `true` = request sleep, `false` = request wake. |
| `src` | string | – | `bot` or `^dash-[0-9a-f]{6}$` | – | Sender name (§2). |

```json
{"v":2,"seq":2,"up":45000,"s":true,"src":"bot"}
```

### 3.14 `flybot/01/ping`

Robot → brain. QoS 0, not retained, 1 Hz. Only the common fields and `bid`.

```json
{"v":2,"seq":57,"up":57012,"bid":2847193551}
```

### 3.15 `flybot/01/pong`

Brain → robot. QoS 0, not retained, published immediately for every ping.

| Key | Type | Unit | Range | Meaning |
|---|---|---|---|---|
| `v`, `seq`, `up` | | | | Common fields (the brain's own counter and uptime). |
| `ps` | integer | – | 0 … 4294967295 | `seq` of the ping being answered. |
| `pu` | integer | ms | 0 … 9007199254740991 | `up` of the ping being answered, echoed unchanged. |

The robot computes round-trip time as `its uptime now − pu`, so it needs no table of pings sent.

```json
{"v":1,"seq":4410,"up":881230,"ps":57,"pu":57012}
```

---

## 4. MQTT users and access control

Anonymous access is disabled. Each program connects as its own user (passwords in `.env`,
firmware credentials in `firmware/include/secrets.h`). The broker enforces the table below. It
decides who may drive the robot: only `brain` can publish `cmd/motor`, only `dashboard` can
publish `cmd/manual`, only `dashboard` and `bot` can publish `cmd/mode`, only `brain` can publish
`cmd/sleep`, only `dashboard` and `bot` can publish `brain/sleep`, which only `brain` can read,
and only `dashboard` can publish `brain/control`.

P = may publish, S = may subscribe, – = neither.

| Topic | esp32 | sim | brain | dashboard | bot |
|---|---|---|---|---|---|
| `sensors` | P | P | S | S | – |
| `events` | P | P | S | S | S |
| `status` | P | P | S | S | S |
| `cmd/motor` | S | S | P S | S | – |
| `cmd/manual` | S | S | S | P S | – |
| `cmd/mode` | S | S | S | P S | P S |
| `cmd/sleep` | S | S | P S | S | – |
| `reward` | P | P | S | P S | P |
| `brain/status` | – | – | P S | S | S |
| `brain/state` | – | – | P S | S | S |
| `brain/weights` | – | – | P S | S | – |
| `brain/control` | – | – | S | P S | – |
| `brain/sleep` | – | – | S | P | P |
| `ping` | P | P | S | S | – |
| `pong` | S | S | P S | S | – |

`brain` reads `flybot/01/#` (every topic, for the logger). `dashboard` reads an explicit list:
every topic except `brain/sleep`.
`bot` can change mode, send rewards and ask the brain to sleep or wake, but cannot lesion,
change parameters, save, load or reset the brain. It is also locked to the allowed Telegram chat ID.

### 4.1 Mosquitto ACL

This becomes `infra/mosquitto/acl` in Phase 2:

```text
# FlyBot ACL, generated from docs/message-contract.md section 4 (revision 2).
# allow_anonymous false is set in mosquitto.conf.

user esp32
topic write flybot/01/sensors
topic write flybot/01/events
topic write flybot/01/status
topic write flybot/01/reward
topic write flybot/01/ping
topic read flybot/01/cmd/motor
topic read flybot/01/cmd/manual
topic read flybot/01/cmd/mode
topic read flybot/01/cmd/sleep
topic read flybot/01/pong

user sim
topic write flybot/01/sensors
topic write flybot/01/events
topic write flybot/01/status
topic write flybot/01/reward
topic write flybot/01/ping
topic read flybot/01/cmd/motor
topic read flybot/01/cmd/manual
topic read flybot/01/cmd/mode
topic read flybot/01/cmd/sleep
topic read flybot/01/pong

user brain
topic read flybot/01/#
topic write flybot/01/cmd/motor
topic write flybot/01/cmd/sleep
topic write flybot/01/brain/status
topic write flybot/01/brain/state
topic write flybot/01/brain/weights
topic write flybot/01/pong

user dashboard
topic read flybot/01/sensors
topic read flybot/01/events
topic read flybot/01/status
topic read flybot/01/cmd/motor
topic readwrite flybot/01/cmd/manual
topic readwrite flybot/01/cmd/mode
topic read flybot/01/cmd/sleep
topic readwrite flybot/01/reward
topic read flybot/01/brain/status
topic read flybot/01/brain/state
topic read flybot/01/brain/weights
topic readwrite flybot/01/brain/control
topic write flybot/01/brain/sleep
topic read flybot/01/ping
topic read flybot/01/pong

user bot
topic read flybot/01/status
topic read flybot/01/events
topic read flybot/01/cmd/mode
topic read flybot/01/brain/status
topic read flybot/01/brain/state
topic write flybot/01/cmd/mode
topic write flybot/01/reward
topic write flybot/01/brain/sleep
```

### 4.2 Connection settings

| User | Client ID | Keep-alive | Clean session | Last will |
|---|---|---|---|---|
| esp32 | `flybot-01-esp32` | 5 s | yes | `flybot/01/status` offline, QoS 1, retained |
| sim | `flybot-01-sim` | 5 s | yes | `flybot/01/status` offline, QoS 1, retained |
| brain | `flybot-01-brain` | 10 s | yes | `flybot/01/brain/status` offline, QoS 1, retained |
| bot | `flybot-01-bot` | 30 s | yes | none |
| dashboard | `flybot-01-<sender name>`, e.g. `flybot-01-dash-3fa2c1` (one per tab, same hex as its `src`, §2) | 30 s | yes | none |

The broker publishes a last will about 1.5 × keep-alive after a client goes silent, so a lost
robot shows offline within about 8 s. The broker needs `persistence true` so retained
`status`, `brain/status`, `brain/weights` and `cmd/mode` (including an e-stop) survive a broker restart.
The ESP32 uses ESP-IDF `esp-mqtt` (QoS 1 publish, last will, TLS).

---

## 5. Who drives the robot

The robot's mode comes from the retained `cmd/mode`. The defaults in §5.1 are defined behaviour:
the firmware and the simulator must implement them exactly.

| Mode | `cmd/motor` | `cmd/manual` | `cmd/sleep` | Edge reflexes | No valid command for 500 ms |
|---|---|---|---|---|---|
| `auto` | obeyed | ignored | obeyed | on | enter `fallback` (local wandering). This includes the brain going silent because its sensor data is stale. |
| `manual` | ignored | obeyed | ignored | on | stop and wait |
| `estop` | ignored | ignored | ignored | off, motors disabled | stay stopped |

- **Boot** holds the robot still. In `boot` the motors are off, `cmd/motor`, `cmd/manual` and `cmd/sleep`
  are ignored and there is no watchdog. The robot leaves `boot` only after odour warm-up and calibration
  have finished and the mode is decided (§5.1 (1)). The odour baseline is therefore captured with the
  robot standing still. During warm-up the brain reports `dec: "warmup"` and sends no `cmd/motor`.
- **Emergency stop** latches until a `cmd/mode` of `auto` or `manual` arrives. Because
  `cmd/mode` is retained and only the dashboard and bot can publish it, nothing else can clear an e-stop.
- **Sleep** (`auto` mode + `cmd/sleep s:true`): see §5.1 (3). The robot wakes on `s:false`, when
  the mode changes, or after 15 s with no repeated `s:true` (the brain is probably gone).
- **Escape** overrides `cmd/motor` and `cmd/manual` for its duration, in `auto` and `manual`.
- **Command time-to-live:** a command applies from its arrival until arrival + `ttl`, or until a
  newer command replaces it. When `ttl` runs out the motors stop.
- **Leaving fallback:** see §5.1 (4).
- **Brain side:** the brain publishes **no** `cmd/motor` while the mode is not `auto`
  (`dec:"paused"`), while asleep (`dec:"sleep"`), or while its sensor data is stale
  (`dec:"stale"`, `brain/status.sens:"stale"`). It never sends stop commands to cover a gap. In
  `auto` mode the robot's 500 ms watchdog then moves it to `fallback`, where it keeps moving
  safely on local sensors. A robot whose sensor messages are not reaching the brain therefore
  wanders and avoids obstacles instead of standing still. When fresh sensor data returns, the
  brain resumes, and the robot leaves fallback by the rule below.

### 5.1 Defined defaults

1. **Start in `auto` when no mode is saved.** After connecting and subscribing, the robot waits up
   to **2 s** for the retained `cmd/mode` (it stays in `boot` meanwhile). If none arrives, it
   behaves as if `auto` had been received. It does **not** publish `cmd/mode` itself (it has no
   right to), so the broker still holds nothing until the dashboard or bot sets a mode. A
   retained `estop` always wins: it arrives on subscribe and is applied before the robot moves.
2. **Stop in `manual` when there are no commands.** In `manual` mode, when no valid
   `cmd/manual` has arrived for 500 ms (or the last one's `ttl` has expired), the motors stop and
   the robot stays in `manual` (`st:"manual"`). It does **not** enter `fallback` and does not
   wander. Edge reflexes stay on.
3. **Reflexes off while asleep.** In `sleep` the motors are off and the looming escape and
   obstacle stop are disabled. `cmd/motor` is ignored and the 500 ms watchdog does not trigger
   `fallback`. The robot keeps publishing `sensors`, `status` and `ping`, so the brain can see
   the light come back.
4. **Leave fallback after 3 good commands.** From `fallback`, the robot returns to `cloud` after
   **3 consecutive valid `cmd/motor` messages**. Valid = passes the schema, known `v`, newer `seq`
   (§6). Consecutive = each arrives within the previous one's `ttl`. An invalid message or a gap
   longer than `ttl` resets the count to 0. The robot then publishes `fallback_off` and `status`.

`status.st` values:

| `st` | Meaning |
|---|---|
| `boot` | Starting up: Wi-Fi/MQTT connect, MQ-3 warm-up and calibration. Odour is `null`. |
| `cloud` | `auto` mode, following `cmd/motor`. |
| `manual` | `manual` mode, following `cmd/manual`. |
| `escape` | Escape reflex running. |
| `fallback` | `auto` mode with no valid commands: local wandering and obstacle avoidance. |
| `sleep` | Asleep via `cmd/sleep`. |
| `estop` | Emergency stop. |
| `offline` | Last will only: the robot has gone. |

---

## 6. Receiver rules: sequence numbers, reboots, duplicates

Every receiver (robot, brain, logger, dashboard, bot) applies these rules.

1. **Validate** every message against its schema. Invalid → drop, count per topic, log.
2. **Unknown version:** a `v` the receiver does not know → drop, count separately, log.
3. **Streams.** Sequence numbers are tracked per stream. On topics with `src` (`cmd/manual`,
   `cmd/mode`, `reward`, `brain/control`, `brain/sleep`) a stream is **topic + sender name**. Each
   dashboard tab is its own stream, so one tab's `seq` is never compared with another's. On every
   other topic a stream is the topic alone. For each stream keep the last `seq` and `up`.
4. **Reboot detection.** `seq` restarts at 0 when a sender starts. Keep `bid` too on streams that carry it.
   - **Robot messages (with `bid`):** a **different `bid` means the robot rebooted**. Reset the stream
     and do not count the jump in `seq` as lost messages. With the same `bid`, only `seq` decides
     (rules 5–7); `up` is not used to detect a reboot.
   - **Other senders (no `bid`):** if `up` is lower than the last `up` seen on that stream, the sender
     restarted. Reset the stream; no loss is counted.
   - The logger records every restart.
5. **Duplicates.** Same `seq` as last time and `up` not lower → a QoS 1 redelivery. Drop it.
   This matters for `reward`: one press must train once.
6. **Loss.** `seq` higher than last + 1 → `seq − last − 1` messages lost (reported in the
   reliability results). Expected on QoS 0 topics, measured not retried.
7. **Out of order.** `seq` lower than last and the same `bid` (or, without `bid`, `up` not lower) →
   late message. Commands are dropped; robot data is not ticked. Logged data is kept and counted.
8. **Wrap.** After 4294967295 comes 0 with `up` still rising. Treat that as +1, not a reboot.
   (At 20 Hz this takes about 6.8 years.)
9. **Offline last-will messages** (`st:"offline"` on `status` and `brain/status`) are fixed at
   connect time, so they are left out of sequence tracking.
10. **Retained messages** delivered on subscribe are treated as current state and do not
    start loss counting.
11. **Brain: stale sensor input.** The brain treats `sensors` as stale when the newest valid
    message arrived more than 250 ms ago, or none has arrived since it started. While stale it
    **stops publishing `cmd/motor` entirely**: no stop or zero commands. It reports
    `brain/state.dec:"stale"` and `brain/status.sens:"stale"` (or `"none"`), and publishes
    `brain/status` at once on every change. Normal publishing resumes on the first fresh message.
    Because the brain ticks only on `sensors` messages (§7.1), it is silent on `cmd/motor` whenever
    none arrive. The 250 ms check is a wall-clock link-health check and only drives the reporting.
    Odour values of `null` during MQ-3 warm-up are **not** stale: `sensors` are arriving. The brain reports
    `dec: "warmup"` with `sens: "ok"` and sends no `cmd/motor` (the robot holds still in `boot`, §5).
12. **Robot: silence is a signal.** The robot does not expect the brain to say "stop". An
    absence of valid `cmd/motor` is handled only by the time-to-live and the 500 ms watchdog (§5).
13. **New and idle senders.** The first message from a sender name not seen before starts a new
    stream. Its `seq` is accepted whatever its value, and no loss is counted. A reloaded dashboard
    tab is a new sender, not a restart. A stream with no message for **10 minutes** is
    forgotten, so closed tabs do not pile up in memory. A forgotten sender that comes back
    starts a new stream.

---

## 7. Timing

| Item | Value |
|---|---|
| `sensors` rate | 20 Hz nominal (every 50 ms); 10–20 Hz allowed |
| Brain control tick | one tick per `sensors` message (20 Hz from the robot); compute budget per tick 50 ms, target well under (§7.1) |
| `cmd/motor` rate | at most one per brain tick, i.e. per `sensors` message |
| `cmd/motor` / `cmd/manual` time-to-live | 300 ms default (50–1000 allowed) |
| Robot command watchdog | no valid command for 500 ms → `fallback` (auto) or stop (manual) |
| Leave fallback | 3 consecutive valid `cmd/motor` messages, each within the previous one's `ttl` (§5.1) |
| Mode at start-up | retained `cmd/mode` if it arrives within 2 s of subscribing, else `auto` (§5.1) |
| Brain stale-sensor limit | 250 ms since newest `sensors` → stop publishing `cmd/motor`, `dec:"stale"`, `sens:"stale"`; robot enters `fallback` about 500 ms after the last command it received |
| `cmd/manual` rate | 10 Hz while the drive pad is held; one stop message on release |
| `cmd/sleep` | on change; repeated every 5 s while asleep; robot wakes after 15 s without a repeat |
| `brain/state` rate | 5 Hz |
| `brain/weights` | on change, at most once per 2 s; also at startup and after load/reset |
| `status`, `brain/status` | on change + every 10 s |
| `ping` | 1 Hz; `pong` immediately |
| Keep-alive / last will | robot 5 s (offline shown within about 8 s); brain 10 s |
| Event queue while offline | at least the last 20 events, sent on reconnect |

Latency targets are measured, not guaranteed: sensor→command round-trip time is reported for the
local and cloud setups and compared with the on-robot escape reflex time (`events.lat`).

### 7.1 Time base: robot time, sensor-clocked brain, faster-than-real-time simulation

1. **Robot time.** Rates and durations in this contract (sensor rate, time-to-live, watchdog,
   repeats, hunger and fear dynamics) are measured in the **robot's clock**, the `up` of the
   robot's messages, not in wall-clock time. On the real robot the two run at the same speed.
   A simulator may run its clock faster than real time (point 5).
2. **Sensor-clocked brain.** The brain runs **one control tick per `sensors` message**, not on a
   timer of its own. All of its dynamics and every robot-facing timer (hunger, fear, habituation,
   smoothing, the 5 s `cmd/sleep` repeat) advance by robot time taken from `sensors.up`, never by
   the wall clock. The brain behaves identically whether the robot runs in real time or faster.
   Only link-health checks use the wall clock: the 250 ms stale check (§6 rule 11), keep-alive and
   ping round-trip time.
3. **Robot time never goes backwards.** The brain keeps its own monotonic robot time by adding
   up the positive differences between consecutive `sensors.up` values.
   - When `sensors.bid` changes (the robot rebooted), that step counts as 0 and the brain resets its
     sequence-number state for that sender (§6 rule 4). Internal state (hunger, fear, learned weights)
     carries on unchanged.
   - A late `sensors` message (same `bid`, lower `seq`) is dropped before it reaches the clock, so `up`
     never steps backwards within a boot.
4. **Gaps.** If consecutive `sensors.up` values differ by more than **500 ms**, the brain treats it
   as a data gap. It integrates a capped step (**100 ms**) instead of applying the whole jump to
   its dynamics, and records the gap (logged and counted).
5. **Lockstep (simulator only).** For fast batch runs the simulator publishes `sensors` with
   `seq = n` and `up` = its simulated time. It then waits for the `cmd/motor` with `rs = n`
   before simulating the next 50 ms. Nothing else changes on the wire. In `manual`, `estop` or
   while asleep it does not wait, because no command is expected. Otherwise it waits at most
   **W** of real time, and after 3 such timeouts in a row it runs freely until a command arrives
   again, so the watchdog and fallback still work without a brain. W is **100 ms** for interactive
   lockstep runs and **2000 ms** for batch and experiment runs: a timeout makes the robot carry on
   with its previous command, so a brief host stall (scheduler, garbage collection) would otherwise
   change a batch result. Batch results record the number of timeouts per trial; a trial with
   timeouts cannot be reproduced exactly.
6. **Latency figures come only from real-time runs.** In lockstep the wire round trip has nothing
   to do with robot time, so the simulator's `status.rtt` / `crtt` are measured in wall-clock time
   there. Latency and round-trip results for the report are taken **only** from real-time runs. In
   lockstep results those figures are left empty. The logger and analysis use `up`, not arrival
   time, to place messages from fast runs on a timeline.

---

## 8. Versioning

- Each message type has its own version in `v`, starting at **1**.
- **Any** change to a payload increases that type's `v` by 1: adding, removing or renaming a
  key, or changing a type, unit, range, precision or enum list. The schemas are strict, so even
  adding a key breaks old receivers.
- The change to this document (field table, example, changelog, revision number) and to the
  schema (`v` const, fields, `examples`) go in **the same commit**.
- Senders send exactly one version. Receivers accept only versions they know and drop others
  (§6 rule 2). It is a monorepo, so publishers and subscribers are updated together. A change to a
  robot-facing message needs a firmware reflash.
- Topic names are not versioned (no `/v2/`). The `v` field is.
- The contract revision at the top goes up with every change to this document, including
  changes that do not alter a payload.

---

## 9. Security notes

- No anonymous clients. One user per program. The ACL (§4), not any payload field, controls who can drive.
- `src` fields are self-declared bookkeeping and are not trusted for authorisation.
- **The dashboard's MQTT password reaches the browser**, because the browser connects to the broker
  directly. Anyone who can open the dashboard can change mode, drive manually, reward, request
  sleep and send brain control commands, though never `cmd/motor` or `cmd/sleep`. So the dashboard needs its own login,
  and in production only TLS ports (8883 MQTT, 8884 secure WebSockets) are open.
  A later option is to proxy through the Next.js server so the password stays server-side.
- The Telegram bot answers only the chat ID in `TELEGRAM_ALLOWED_CHAT_ID`. Even if the bot is
  compromised, the ACL limits it to `cmd/mode`, `reward` and `brain/sleep`. It cannot reset or
  lesion the brain.

---

## 10. Changelog

| Revision | Date | Change |
|---|---|---|
| 1 | 2026-11-23 | First version. All message types at `v = 1`. |
| 2 | 2026-11-23 | Added `brain/sleep` (`v = 1`): dashboard and bot ask the brain to sleep or wake; only the brain reads it. Dashboard ACL changed from `flybot/01/#` to an explicit list without `brain/sleep`. `brain/control` → `v = 2`: dashboard-only, `src` and the `sleep` command removed; bot loses publish rights. `brain/state` → `v = 2`: `ack` no longer has `src`. Stale sensor data: the brain stops publishing `cmd/motor` instead of sending stop commands, so the robot's watchdog moves it to fallback. `brain/status` → `v = 2`: new `sens` field (`ok`/`stale`/`none`). New §5.1 makes four robot defaults defined behaviour: start in `auto` if no mode arrives within 2 s, stop (not wander) in `manual` with no commands, reflexes off while asleep, leave fallback after 3 consecutive valid commands. |
| 3 | 2026-11-23 | Per-tab dashboard sender names (`dash-` + 6 hex), carried in `src` on every message a dashboard tab sends. `cmd/manual` → `v = 2` with its own schema `cmd-manual` (adds `src`); `cmd/motor` unchanged at `v = 1`. `cmd/mode` → `v = 2`: `src` is `bot` or a tab name. `reward` → `v = 2`: `src` is `btn`, `bot` or a tab name. `brain/sleep` → `v = 2`: `src` is `bot` or a tab name. `brain/control` → `v = 3`: required `src` (tab name). `brain/state` → `v = 3`: `ack` has `src` again (tab name). §2 sender-name table; §6 streams are topic + sender name, new rule 13 (new and idle senders); dashboard client ID reuses the tab name. |
| 4 | 2026-11-23 | Time base (§7.1): rates and durations are in robot time (`up`); the brain ticks once per `sensors` message and advances its dynamics by robot time only (monotonic, reboot step = 0, gaps over 500 ms capped at 100 ms); lockstep simulation; latency figures only from real-time runs. No payload or `v` changes. |
| 5 | 2026-11-23 | MQ-3 warm-up: `brain/state` → `v = 4` with new `dec` value `warmup` (odour `null`, brain sends no `cmd/motor`, `sens` stays `ok`). §5 states the boot hold explicitly: motors off, commands ignored, no watchdog, leave `boot` only after odour calibration. |
| 6 | 2026-11-23 | Boot ID: new field `bid` (random 1…4294967295 per robot boot) on every robot message: `sensors`, `events`, `status` (incl. last will) and `ping` → `v = 2`; `reward` → `v = 3` (`bid` required when `src` is `btn`, absent otherwise). §6 rule 4: a `bid` change is a reboot; with the same `bid`, `seq` alone decides duplicate / late / lost. §7.1 (3): the reboot step is a `bid` change. Other senders unchanged. |
| 7 | 2026-11-24 | Lockstep (§7.1 (5)): the simulator's wait for `cmd/motor` is 100 ms for interactive runs and 2000 ms for batch and experiment runs, so host stalls cannot change batch results; timeouts are counted per trial. No payload or `v` changes. |
| 8 | 2026-11-24 | Habituation visibility and state control: `events` → `v = 3` (`escape` sent for every looming detection, with `resp` and `hab`; `lat` only when `resp` > 0); `status` → `v = 3` (`hab`); `brain/state` → `v = 5` (`hab`); `brain/control` → `v = 4` (command `state` with `hun` / `fear`). Lesion `hab` answers `ack.ok = false` (habituation is robot-side). Habituation is one level (wording settled during step 5.8: a clearly faster stimulus dishabituates, no per-class levels). |
| 9 | 2026-11-25 | Backlog visibility: `brain/status` → `v = 3` with `lag`, the number of inbound messages the brain has received but not yet processed; crossing the brain's warning threshold publishes `brain/status` immediately. |
