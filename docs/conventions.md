# FlyBot conventions

Rules every FlyBot program follows: firmware, brain, logger, bot, simulator and dashboard.
The exact topics and payloads are in [`message-contract.md`](message-contract.md).

## 1. Units

| Quantity | Unit | Notes |
|---|---|---|
| Distance | cm | Ultrasonic range 2–400 cm. |
| Time and durations | ms | Uptime, time-to-live, time-to-contact, latency, round-trip time. |
| Voltage | V | Battery pack voltage, not the divided ADC pin voltage. |
| Motor command | −1.0 … +1.0 per wheel | +1.0 = full forward, −1.0 = full reverse, 0 = stop. The firmware maps this to PWM, applies its duty cap and dead zone. |
| Signal strength | dBm | Wi-Fi RSSI, negative integer. |
| Memory | bytes | |
| Normalised values | 0.0 … 1.0 | Odour, light, hunger, fear. |

Never send raw ADC counts, PWM duty values or pin voltages over MQTT. Convert at the edge.

## 2. Odour values

- Each MQ-3 sensor has its own baseline, measured after warm-up while the air is clean,
  and slowly corrected for drift.
- The value sent is the **change from that sensor's baseline, scaled to 0–1**:
  0 = baseline (clean air), 1 = the calibrated maximum. Values below baseline are sent as 0;
  values above the maximum are clamped to 1.
- Before warm-up and calibration finish, odour values are sent as `null`.
- Never send raw ADC readings.

## 3. Time

- Every message carries the **sender's uptime in ms** (`up`): time since that program
  (or the ESP32) started. The ESP32 uses its 64-bit microsecond timer divided by 1000,
  so it does not wrap after 49 days.
- Payloads contain no wall-clock time. Every receiver that stores or displays a message
  records its own **wall-clock arrival time (UTC)** next to it. The logger stores both.
- Durations (`ttl`, `ttc`, `lat`, `rtt`) are in ms.

## 4. Every message

Every message carries the same three fields first:

| Key | Meaning |
|---|---|
| `v` | Schema version of this message type, integer starting at 1. |
| `seq` | Sequence number, counted separately per sender and per topic. Starts at 0 when the sender starts, goes up by 1 per message. |
| `up` | Sender's uptime in ms when the message was created. |

Because `seq` restarts at boot, a receiver knows the sender rebooted when `up` goes
**down**. That is not message loss. Full rules are in the contract, §6.

Messages on topics with more than one possible sender also carry `src`, the sender name:
`btn`, `bot`, or `dash-` + 6 hex for each dashboard tab (generated at page load). Receivers track
`seq` per sender name. See the contract, §2.

## 5. Topics

- Every topic starts with `flybot/<robot id>/`. The robot ID is the two-character
  string **`01`**, set by `FLYBOT_ROBOT_ID`, so a second robot (`02`) needs no code changes.
- Topic names are **lowercase**, segments separated by `/`, no spaces, no leading or
  trailing slash, no wildcards in published topics.
- Schema files are named after the topic with `/` replaced by `-`:
  `flybot/01/cmd/motor` → `cmd-motor.schema.json`.

## 6. JSON payloads

- **Short keys.** Lowercase, usually 1–4 characters (`ol`, `bat`, `ttl`). Every key is
  documented in the contract. There are no undocumented keys: the schemas reject them.
- **Compact JSON.** No spaces or newlines. UTF-8.
- **Fixed precision.** Numbers are rounded to the decimal places given in the contract
  (for example odour 3 decimals, distance 1 decimal). This keeps the 20 Hz messages small.
- **Clamp before sending.** Senders clamp values into the allowed range. Receivers reject
  messages outside the range.
- **Booleans** are `true`/`false`, never `0`/`1`. **`null`** is used only where the contract allows it.
- **Enum values** are lowercase words, joined with `_` if needed (`fallback_on`).
- Keep the high-rate payloads (`sensors`, `cmd/motor`, `cmd/manual`) under 128 bytes.

## 7. Configuration and secrets

- Thresholds, rates, neuron counts and credentials live in config files and environment
  variables, not in code.
- Secrets never go in the repository. Only `.env.example` and
  `firmware/include/secrets.example.h` are committed.

## 8. Git

- `main` always works. One branch per phase or feature, merged when it works.
- Small commits after each logical step, with conventional-commit prefixes
  (`docs:`, `chore:`, `feat:`, `fix:`). See [`CLAUDE.md`](../CLAUDE.md).
- A change to a message means the contract, the schema and the `v` field change in the same commit.
