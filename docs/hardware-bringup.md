# Hardware bring-up checklist

Work through this in order when the parts arrive. Each stage builds on the one before, and each has a
"done when" check. **Wheels stay off the ground until stage 9.** Keep a multimeter at hand, and write
your measurements in the results table at the end (they go into the report).

The firmware is in `firmware/` (build and flash instructions in [`firmware/README.md`](../firmware/README.md)).
The serial console runs at 115200 baud; type `help` for the commands used below.

---

## 0. Before you start

- [ ] **Check the wiring against [`pin-map.md`](pin-map.md)** (from the component sheet; the firmware's
      copy is section A of `firmware/include/config.h`).
- [ ] Copy `firmware\include\secrets.example.h` to `firmware\include\secrets.h` and fill in your Wi-Fi,
      the laptop's IP address and `MQTT_ESP32_PASS` from the repository's `.env`. Never commit `secrets.h`.
- [ ] PlatformIO stays on D: (set in every new PowerShell window):
      ```powershell
      $env:PLATFORMIO_CORE_DIR = "D:\Tools\platformio"; $env:TMP = "D:\Tools\tmp"; $env:TEMP = "D:\Tools\tmp"
      ```

### Wiring

| Part | Connection | ESP32 GPIO |
|---|---|---|
| MQ-3 left, AO | divider 10 kΩ (top) / 20 kΩ (bottom): 5 V → 3.33 V max | 34 |
| MQ-3 right, AO | divider 10 kΩ / 20 kΩ | 35 |
| MQ-3 VCC / GND | 5 V rail / GND (each heater draws about 150 mA) | – |
| LDR | LDR from 3.3 V to the pin, 10 kΩ from the pin to GND | 36 (VP) |
| Battery | divider 100 kΩ (top) / 47 kΩ (bottom) from the pack + (after the BMS) | 39 (VN) |
| HC-SR04P VCC / GND | 3.3 V / GND | – |
| HC-SR04P Trig / Echo | direct (a 5 V HC-SR04 would need a 1 kΩ / 2 kΩ divider on Echo) | 18 / 19 |
| TB6612 PWMA, AIN1, AIN2 | left motor | 25, 26, 27 |
| TB6612 PWMB, BIN1, BIN2 | right motor | 32, 33, 4 |
| TB6612 STBY | plus a 10 kΩ pull-down to GND (motors off until the firmware runs) | 23 |
| TB6612 VM / VCC / GND | pack after BMS, fuse and switch / 3.3 V / GND | – |
| WS2812B DIN | through 330 Ω; LED powered from 5 V | 13 |
| Buzzer | 2N2222 low-side switch, 1 kΩ base resistor, buzzer on 5 V (passive piezo; `BUZZER_PASSIVE` false for an active one) | 5 |
| Reward / punish buttons | button to GND (internal pull-ups) | 14 / 15 |
| On-board LED | already on the board | 2 |
| Encoders (optional, LM393 on 3.3 V) | `ENCODERS_ENABLED` in config.h | 16 / 17 |
| I²C (optional OLED / VL53L0X) | reserved, no driver yet | 21 / 22 |

Power: 2S 18650 (6.0–8.4 V) → BMS, fuse, switch → buck converter 5.0 V → ESP32 `VIN`. The 3V3 rail feeds
the TB6612 VCC, the encoder modules, the LDR divider and the HC-SR04P; the 5 V rail feeds the MQ-3s, the
WS2812B and the buzzer.
The TB6612 motor supply (VM) comes straight from the pack. **All grounds are joined.**

---

## 1. Buck converter to 5.0 V (nothing else connected)

- [ ] Connect only the battery (or a bench supply at 7.4 V) to the buck converter input.
- [ ] Turn its trimmer until the output reads **5.0 V** (between 4.95 and 5.10 V) on the multimeter.
- [ ] Load it briefly with a 10 Ω / 5 W resistor (or the two MQ-3 modules, about 0.3 A): it must stay
      above 4.9 V. Check again at the lowest pack voltage you expect (6.4 V, the cut-off).
- [ ] Mark the trimmer (a dab of nail polish) so it cannot drift.

**Done when:** 5.0 V without load and above 4.9 V with 0.3 A, from 6.4 V to 8.4 V in.

> Never connect USB and the buck output at the same time unless your board has a diode on VIN (most
> DevKits do, check yours). If unsure, power from USB only while the computer is attached.

## 2. ESP32 alone: flash and serial

- [ ] ESP32 on USB only, nothing else wired.
- [ ] `D:\Tools\pio-venv\Scripts\pio run -e esp32dev -t upload` from `firmware\`, then
      `D:\Tools\pio-venv\Scripts\pio device monitor`.
- [ ] The monitor shows `FlyBot firmware 0.6.0`, the reset reason and a `boot id`. `status` answers.
- [ ] Without Wi-Fi in range the on-board LED blinks fast, and after 30 s the setup portal opens
      (hotspot `FlyBot-Setup-xxxx`). That is expected; press reset to continue.
- [ ] Type `freeze`: within 5 s the board restarts and reports `reset: watchdog` (plan step 6.10).

**Done when:** the board flashes, prints, and restarts itself after `freeze` (plan step 6.1).

## 3. Power from the buck converter, battery reading

- [ ] Wire the buck output to `5V`/`VIN` and GND, and the battery divider to GPIO 39. Power from the pack.
- [ ] `s` prints the sensors twice a second. Compare `bat=` with the multimeter across the pack.
- [ ] If they differ by more than 0.05 V, set `BATTERY_CAL` in config.h to (multimeter ÷ printed) and
      reflash. Check at a full (8.4 V) and a half-empty (7.4 V) pack.

**Done when:** `bat` is within 0.05 V of the multimeter.

## 4. MQ-3 left / right: the feasibility test (plan step 6.2)

This is the project's most important early check: can two MQ-3s tell left from right?

- [ ] Wire both MQ-3 modules (VCC to 5 V, AO through their dividers to GPIO 34 / 35). Mount them at their
      final positions, about 12 cm apart.
- [ ] **First use only:** let the heaters burn in for at least a few hours (the datasheet recommends
      24–48 h); readings drift a lot before that.
- [ ] Each session: power on in clean air, robot still. The firmware holds the robot in `boot` for 60 s
      (`ODOUR_WARMUP_MS`), measures each sensor's baseline, then reports `ol`/`or` (0–1). `raw` adds the
      sensor volts and baselines to `s`.
- [ ] Clean air: both values stay below 0.03 and steady; `raw` shows similar volts left and right
      (adjust the module trimmers so clean air reads 0.3–1.0 V).
- [ ] Hold a cotton ball with a little alcohol 10 cm to the **left** of the robot for 10 s: `ol` rises
      clearly above `or`. Move it to the **right**: `or` becomes the higher one within a few seconds.
- [ ] Repeat 5 times each side and note the peak values and response times in the results table.
- [ ] If the rise is far below 1.0 or saturates at once, change `ODOUR_FULL_SCALE_V` (a 1.2 V rise maps to
      1.0). `cal` restarts the warm-up and baseline without rebooting.

**Done when:** in at least 4 of 5 trials the sensor nearer the alcohol reads higher.
**If it fails:** stop and rethink the odour design before building further (plan, section "Risks").

## 5. Ultrasonic distance

- [ ] Wire the HC-SR04P (3.3 V, no divider). With `s` running, hold a book at 10, 30, 60, 100 and
      200 cm (ruler). `d` must be within about 2 cm (and 5 % beyond 1 m).
- [ ] Point it at open space (more than 4 m): `d=null` (no echo).
- [ ] Wave a pencil through the beam once: the filtered `d` must not jump (the median rejects single
      bad readings).

**Done when:** distances match the ruler and missing echoes show as null.

## 6. Light sensor

- [ ] `s` in normal room light, then with the LDR covered by a finger, then under a phone torch.
- [ ] Measure the voltage on GPIO 36 with the multimeter, covered and in room light, and put the two
      values in `LDR_DARK_V` and `LDR_BRIGHT_V` in config.h, so that covered reads about 0.0 and room light
      about 1.0. Without an LDR, set `LDR_FITTED` false: `lt` is then null.

**Done when:** covered < 0.1, room light > 0.6.

## 7. Buttons, RGB LED, buzzer

- [ ] `buzz boot`, `buzz escape`, `buzz low`: each plays (passive piezo: tones; active buzzer: beeps).
- [ ] The RGB LED is white and pulsing during warm-up, then shows the state (colours in
      `firmware-decisions.md`, decision 19). Too bright? Lower `MOOD_LED_BRIGHTNESS`.
- [ ] Press and release each button: without MQTT the buzzer plays the error tone (a reward needs the
      broker; it is sent on release).
- [ ] Within the first minute after a reset, hold both buttons for 3 s: the portal opens (blue blinking
      LED); no reward or punishment is sent for the chord. Reset. (Do not hold the buttons while powering
      on: GPIO 14 and 15 must not be held low while the chip boots.)

**Done when:** every sound plays, the LED changes with the state, both buttons respond.

## 8. Motors, wheels off the ground (plan step 6.3)

- [ ] Robot on a box, wheels free. TB6612 wired, STBY pull-down fitted, VM from the pack.
- [ ] Power on: the wheels must not twitch during boot (STBY is low until the firmware enables it).
- [ ] `m 0.3 0.3 1000`: both wheels turn **forward** for 1 s. If one turns backwards, set its
      `MOTOR_*_INVERT` in config.h and reflash (or swap its two wires).
- [ ] `m -0.3 -0.3 1000` backward; `m 0.4 -0.4 1000` turns clockwise (left forward, right back).
- [ ] Find the smallest command that turns each wheel (`m 0.1 0.1`, `m 0.15 0.15`, ...). If it is above
      0.2, set `MOTOR_MIN_DUTY` to that duty.
- [ ] `m 0.85 0.85 2000` and `m 1 1 2000` sound the same: the duty cap (0.85) protects the 6 V motors.
      Measure the motor current if you can (stall current should not trip the pack protection).

**Done when:** forward, backward and both turns work from the serial console.

## 9. Wi-Fi and MQTT against the laptop broker (plan step 6.4)

- [ ] On the laptop: `infra\start-broker.cmd` (LAN mode), and allow inbound TCP 1883 in the Windows
      firewall for the private network (see `infra/README.md`). Note the laptop's IP (`ipconfig`).
- [ ] `secrets.h` (or the portal) has that IP, port 1883, user `esp32` and `MQTT_ESP32_PASS`.
- [ ] **Wheels still off the ground:** with no brain running, the robot leaves `boot` after the warm-up,
      gets no `cmd/motor`, and after 0.5 s enters `fallback` and wanders (contract section 5). The wheels
      start turning; that is correct. To keep them still, e-stop from the dashboard first.
- [ ] Reset the robot: the console shows `[wifi] connected` and `[mqtt] connected`; the on-board LED
      stays on; the buzzer plays the connected chirp.
- [ ] Watch everything with the dashboard (`http://localhost:3000`) or, on the laptop:
      `D:\Tools\mosquitto\mosquitto_sub.exe -h 127.0.0.1 -u brain -P <MQTT_BRAIN_PASS> -t "flybot/01/#" -v`
  - [ ] one `events` `boot` with the right reset reason;
  - [ ] `status` (retained) on every state change and every 10 s, with `rssi`, `heap`, `bat`, `fw`;
  - [ ] `sensors` 20 times a second with `bid` (`ol`/`or` null during the 60 s warm-up);
  - [ ] `ping` once a second (`rtt` stays null until the brain answers).
- [ ] Unplug the robot's power: within about 8 s the retained `status` becomes `{"st":"offline",...}`.
- [ ] From the dashboard: **e-stop** → status `estop`, LED blinking red; **auto** → `cloud`; **manual**
      and hold the drive pad (wheels still off the ground) → the wheels follow; release → they stop.
- [ ] Press the reward and punish buttons: `reward` messages with `"src":"btn"` and the robot's `bid`.
- [ ] Optional: check every message against the contract with the logger:
      run the brain (it includes the logger) for a minute, then
      `brain\.venv\Scripts\python -m flybot_brain.logger stats`: 0 invalid on every robot topic.

**Done when:** live messages appear and a dashboard command moves the wheels (plan step 6.4).

## 10. Setup portal (plan step 6.12)

- [ ] Hold both buttons for 3 s within a minute of a reset (or type `portal`). Join `FlyBot-Setup-xxxx` from a phone with the
      portal password from `secrets.h`; the setup page opens (or browse to `http://192.168.4.1/`).
- [ ] Change the broker address to something wrong, save: the robot restarts and keeps trying
      (`[mqtt] connecting`). Open the portal again and put the right address back.
- [ ] `forget` (console) returns to the values in `secrets.h`.

**Done when:** Wi-Fi and broker can be changed from a phone without reflashing.

## 11. Reflex timing and the escape (plan steps 6.5 and 6.6)

- [ ] Robot still on the box, MQTT connected, `status`: note "longest step" and "longest period".
- [ ] Switch the Wi-Fi router off for a minute, then `status` again: the longest period stays about
      5 ms (and "late steps" does not grow). That is plan 6.5's "pulling Wi-Fi does not change the reflex
      timing".
- [ ] Wheels on the floor now, open space. Move a hand (or a book) briskly and steadily toward the sensor
      from about 60 cm: the robot reverses and turns away; an `escape` event arrives with `ttc`, `d`,
      `resp` and `lat` (detection to motors, measured on the robot). Note `lat` for 10 trials.
- [ ] Repeat every few seconds: `resp` gets smaller (habituation) until the robot only reports the
      stimulus (`resp` 0, no `lat`). A much faster approach gets the full response again. After a minute
      of rest the response recovers.
- [ ] Turning toward a wall must not trigger an escape (the turning hold-off).

**Done when:** a brisk approach from 60 cm triggers a dodge reliably.

## 12. The full robot with the brain (plan steps 6.7, 6.8, 6.11 and Phase 7)

- [ ] Start the brain (`python -m flybot_brain`, baseline controller first), the dashboard, and the robot.
      After warm-up the robot goes to `cloud` and follows the brain (status `rtt`/`crtt` now have values).
- [ ] **Stop the brain** (Ctrl+C): within 0.5 s the robot enters `fallback` (`fallback_on`,
      `why: cmd_timeout`) and wanders, turning away from obstacles. Start the brain again: after 3 good
      commands it returns to `cloud` (`fallback_off` with the time spent).
- [ ] **Unplug the router** mid-run: the robot keeps wandering safely (`fallback`, LED purple). Plug it
      back: it reconnects, sends the queued events, and returns to cloud control.
- [ ] Every state is reachable and shows in `status`: boot, cloud, manual, escape, fallback, sleep
      (cover the LDR: the brain sends `cmd/sleep`), estop.
- [ ] Low battery (bench supply instead of the pack, or a nearly empty pack): below 6.8 V one
      `low_battery` warning; below 6.4 V `low_battery` with `cut: true`, motors off, LED dim red.
- [ ] Run the odour-seeking demo: an alcohol source on the floor, the robot finds it.
- [ ] Note `rtt`, `crtt` and escape `lat` in the results table (the report compares cloud round trips with
      the on-robot reflex).

**Done when:** all of the above, and stopping the brain makes the robot enter fallback within half a second.

---

## Results table

| Check | Value | Notes |
|---|---|---|
| Buck output, no load / 0.3 A | V / V | |
| Battery reading vs multimeter | V vs V | `BATTERY_CAL` = |
| MQ-3 clean air, left / right | V / V | |
| MQ-3 alcohol left: `ol` peak / `or` | / | response time s |
| MQ-3 alcohol right: `or` peak / `ol` | / | response time s |
| Left/right correct (of 5 + 5) | | |
| Ultrasonic at 10 / 30 / 60 / 100 / 200 cm | | |
| LDR covered / room / torch | | |
| Smallest moving command, left / right | | `MOTOR_MIN_DUTY` = |
| Reflex longest period, Wi-Fi on / off | µs / µs | |
| Escape `lat` (10 trials) | min / median / max ms | |
| Habituation: escapes until `resp` 0 | | |
| `rtt` / `crtt` (median, laptop broker) | ms / ms | |
| Fallback after brain stop | ms | |
| Run time on a full pack | min | |
