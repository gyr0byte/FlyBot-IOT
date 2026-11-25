# Pin map (ESP32 DevKit, WROOM-32)

From the component sheet. The firmware's copy is section A of
[`firmware/include/config.h`](../firmware/include/config.h); change both together.

| GPIO | Connection | Notes |
|---|---|---|
| 34 | MQ-3 left AO via 10k/20k divider | ADC1_CH6, input only |
| 35 | MQ-3 right AO via 10k/20k divider | ADC1_CH7, input only |
| 36 (VP) | LDR divider: LDR to 3.3 V, 10k to GND | ADC1_CH0, input only |
| 39 (VN) | Battery via 100k/47k divider | ADC1_CH3, input only |
| 25 | TB6612 PWMA | left motor speed |
| 26 | TB6612 AIN1 | |
| 27 | TB6612 AIN2 | |
| 32 | TB6612 PWMB | right motor speed |
| 33 | TB6612 BIN1 | |
| 4 | TB6612 BIN2 | |
| 23 | TB6612 STBY | 10k pull-down |
| 18 | HC-SR04P TRIG | |
| 19 | HC-SR04P ECHO | 1k/2k divider only if a 5 V HC-SR04 is used |
| 16 | Left encoder (optional) | LM393 module |
| 17 | Right encoder (optional) | LM393 module |
| 21 | I²C SDA (optional OLED / VL53L0X) | reserved, no driver yet |
| 22 | I²C SCL | reserved |
| 13 | WS2812B data | 330 Ω series |
| 5 | Buzzer via 2N2222 | 1k base resistor |
| 14 | Reward button to GND | internal pull-up |
| 15 | Punish button to GND | internal pull-up |
| 2 | On-board LED | connection status |

**Power:** buck converter 5.0 V to VIN. The 3V3 rail feeds the TB6612 VCC, the LM393 encoder modules,
the LDR divider and the HC-SR04P. The 5 V rail feeds both MQ-3s, the WS2812B and the buzzer. TB6612 VM
comes from the pack after the BMS, fuse and switch. All grounds are common.

## Differences from the pin map I had derived (firmware decision 1) and what they changed

| Signal | Before | Now | What changed |
|---|---|---|---|
| MQ-3 left | 36 | **34** | Only the pin number; both are ADC1 input-only pins |
| MQ-3 right | 39 | **35** | Only the pin number |
| LDR | 34 | **36** | Only the pin number; same divider orientation (brighter = higher) |
| Battery | 35, 100k/33k | **39, 100k/47k** | `BATTERY_DIVIDER` is now 147/47 = 3.13 (was 4.03). A full pack (8.4 V) gives 2.69 V at the pin, above the ESP32 ADC's most accurate range (up to about 2.45 V at 11 dB), so readings above about 7.7 V are a little less accurate. The warning (6.8 V → 2.17 V) and cut-off (6.4 V → 2.05 V) levels are inside the accurate range, which is what matters. Calibrate `BATTERY_CAL` near 7.0 V (bring-up stage 3) |
| Ultrasonic | HC-SR04 on 5 V, echo divider 1k/2k | **HC-SR04P on 3.3 V**, no divider | Wiring only; same pins (18/19). The firmware is unchanged |
| Encoders | 13 / 14 | **16 / 17** | Only the pin numbers (16/17 are free on a WROOM-32; on a WROVER board they belong to the PSRAM) |
| I²C | not planned | **21 / 22 reserved** | New constants `PIN_I2C_SDA` / `PIN_I2C_SCL`; nothing uses them yet |
| WS2812B | 16 | **13** | Only the pin number |
| Buzzer | 17, direct | **5, through a 2N2222** | Same logic (high = on), now able to drive a 5 V buzzer. GPIO 5 is a strapping pin and briefly outputs a signal while the chip boots, so the buzzer may tick at power-on; harmless. A passive piezo still plays tones through the transistor; set `BUZZER_PASSIVE` false for an active buzzer |
| Reward / punish buttons | 21 / 22 | **14 / 15** | GPIO 14 outputs a signal and GPIO 15 is a strapping pin during boot, so they must not be held low while the chip starts. **The setup-portal gesture changed** from "both buttons held at power-on" to "both buttons held for 3 s within the first minute after boot". Rewards are now sent when a button is released, and pressing both together sends neither (it is the portal gesture). Pressing a single button at boot is harmless (GPIO 15 low only silences the boot messages) |
| Motors, STBY, on-board LED | 25/26/27, 32/33/4, 23, 2 | same | Nothing |
| Power | HC-SR04 and logic as I assumed | as above | Wiring table in the bring-up checklist updated; the WS2812B still gets 3.3 V data on a 5 V supply (usually works; if the LED flickers, feed it through a level shifter or power it at 4.5 V through a diode) |

Every change is confined to `config.h` (pins, `BATTERY_DIVIDER`, the new I²C constants and the portal
window) plus the button handling in `src/tasks.cpp`. The firmware builds cleanly with the new map.
