# FlyBot: A Cloud-Connected, Fruit-Fly-Inspired Neural Controller for a Low-Cost ESP32 Robot

**Module:** Cloud Computing and Internet of Things
**Institution:** Itahari International College (affiliated with London Metropolitan University)
**Student:** Gaurav
**Student ID:** _[add]_
**Lecturer:** _[add]_
**Date:** _[add]_

---

## 1. Abstract

This project proposes **FlyBot**, a small wheeled robot built around a single ESP32 microcontroller whose decisions are made by a **bio-inspired spiking neural network modelled on the fruit fly (*Drosophila melanogaster*) olfactory circuit**. The ESP32 acts as the robot's "body": it reads sensors and drives motors. The neural model acts as the robot's "brain" and runs on a cloud server, connected to the robot over MQTT. A live web dashboard shows the robot's state, neuron activity and learned connections.

The robot seeks odour sources, reflexively avoids fast-approaching objects, learns to prefer or avoid stimuli, and shows internal states such as hunger and fear. The project demonstrates a complete IoT pipeline (sensing, communication, cloud processing, actuation and visualisation) using inexpensive hardware, and explores the trade-off between edge and cloud computation.

> **Scope note:** FlyBot does not run a complete fruit fly brain. It uses a simplified network inspired by the real olfactory pathway. The full published fly brain model (about 140,000 neurons) is too large to drive a robot in real time, and is only considered as an optional offline demonstration.

## 2. Problem Statement and Motivation

Most student IoT projects collect sensor data and display it, or trigger fixed if-else rules. This works, but the system does not adapt, learn or change behaviour based on internal state.

Animals solve this with small brains. A fruit fly finds food by smell, escapes threats, learns which odours are good or bad, and changes its behaviour depending on hunger and fear. This project asks:

> *Can a low-cost IoT device exhibit adaptive, animal-like decision-making by connecting a cheap microcontroller to a cloud-hosted neural model inspired by a real insect brain?*

## 3. Aim and Objectives

**Aim:** To design and build a cloud-connected IoT robot that makes adaptive decisions using a fruit-fly-inspired neural controller.

**Objectives:**

1. Build an ESP32-based wheeled robot with at least three sensors (two odour sensors and one distance sensor, plus an optional light sensor).
2. Implement a real-time communication pipeline between the robot and a cloud server using MQTT.
3. Develop a simplified spiking neural network based on the fly olfactory circuit, hosted in the cloud.
4. Implement behaviours: odour seeking, looming-based escape, habituation, hunger/fear states and simple associative learning.
5. Build a live dashboard visualising sensor data, neuron activity, learned weights and robot state.
6. Evaluate the system in terms of latency, reliability and behavioural correctness, and discuss edge versus cloud processing.

## 4. System Overview

### 4.1 Architecture

```
          ┌────────────────────────── ROBOT (EDGE) ──────────────────────────┐
          │  MQ-3 (left)   MQ-3 (right)   HC-SR04   LDR (optional)            │
          │        \            |            |         /                      │
          │                 ┌───────────┐                                     │
          │                 │   ESP32   │── motors (L298N/TB6612) ── wheels   │
          │                 └─────┬─────┘── RGB LED / buzzer                  │
          │   Local reflexes: looming dodge, obstacle stop, Wi-Fi fallback    │
          └─────────────────────────┼─────────────────────────────────────────┘
                                    │  Wi-Fi + MQTT (sensor data up, commands down)
                          ┌─────────▼──────────┐
                          │    MQTT BROKER     │   (cloud)
                          └─────────┬──────────┘
                    ┌───────────────┼────────────────┐
          ┌─────────▼─────────┐ ┌───▼──────────┐ ┌───▼──────────────┐
          │  FLY-INSPIRED     │ │  DATABASE    │ │  WEB DASHBOARD   │
          │  NEURAL MODEL     │ │  (event log, │ │  + Telegram bot  │
          │  (Python/Brian2)  │ │   history)   │ │  (alerts, train) │
          └───────────────────┘ └──────────────┘ └──────────────────┘
```

### 4.2 Division of work: edge versus cloud

| Layer | Runs on | Responsibilities | Why |
|---|---|---|---|
| Fast reflexes | ESP32 (edge) | Looming detection and dodge, obstacle stop, motor control, fallback behaviour when Wi-Fi is lost | Cloud round-trip latency (tens to hundreds of ms) is too slow for an escape reflex |
| High-level brain | Cloud server | Spiking network, odour-seeking decisions, learning, hunger/fear state, sleep/wake | Needs more memory and compute than an ESP32 has, and is easy to inspect and modify remotely |
| Monitoring | Cloud and browser | Dashboard, logging, alerts, manual control | Remote access and history |

This mirrors real animals, where fast escape circuits are local and slower decisions involve the brain.

## 5. Biological Inspiration

The neural model is based on the structure of the *Drosophila* olfactory pathway:

- **Olfactory receptor inputs** (mapped to the MQ-3 sensor readings).
- **Antennal lobe** (signal processing and contrast).
- **Mushroom body** (sparse coding and associative learning, where reward or punishment changes connection strengths).
- **Output neurons** (approach/avoid decisions, converted into motor commands).

The connectivity of the real fly brain is publicly available through the FlyWire connectome (Dorkenwald et al., 2024), and a whole-brain computational model has been published (Shiu et al., 2024). This project uses a **much smaller, simplified circuit** (hundreds to a few thousand leaky integrate-and-fire neurons) so it can run in real time.

## 6. Planned Behaviours

| # | Behaviour | How it works | Where it runs |
|---|---|---|---|
| 1 | **Odour seeking** | Left and right MQ-3 readings drive left and right neuron groups. The stronger side steers the robot toward the source. | Cloud brain, command sent to ESP32 |
| 2 | **Looming escape** | Rapidly shrinking ultrasonic distance triggers an immediate back-off and turn. | ESP32 (edge), event reported to cloud |
| 3 | **Habituation** | Repeated harmless stimuli produce a weaker response over time. | Cloud brain |
| 4 | **Hunger and fear states** | Internal variables change how bold or cautious the robot is. A hungry robot takes more risk to reach the odour. | Cloud brain |
| 5 | **Associative learning** | Reward and punishment inputs (button or Telegram "good bot / bad bot") change synaptic weights, so the robot learns a preference. | Cloud brain |
| 6 | **Sleep and wake** | The light sensor sets a day/night cycle. Sleep can strengthen learned weights. | Cloud brain |
| 7 | **Wi-Fi loss resilience** | The ESP32 falls back to simple local wandering and obstacle avoidance, and resumes cloud control when reconnected. | ESP32 and cloud |

Optional demo features: live neuron "lesion" switches on the dashboard, a "why did it do that?" explanation view, and an RGB LED mood indicator.

## 7. Hardware

**Core requirement:** one ESP32 microcontroller, 2 to 3 sensors, and low-cost components.

| Component | Qty | Purpose | Approx. cost (NPR)* |
|---|---|---|---|
| ESP32 DevKit | 1 | Main controller | 1,000 to 1,800 |
| MQ-3 alcohol/odour sensor | 2 | Left and right smell sensing | about 600 total |
| HC-SR04 ultrasonic sensor | 1 | Obstacle and looming detection | 150 to 250 |
| LDR with resistor (optional) | 1 | Day/night cycle | about 20 |
| Gear motors with wheels | 2 | Locomotion | 600 to 900 |
| Motor driver (L298N or TB6612) | 1 | Drives the motors | 250 to 400 |
| 18650 cells, holder, charger | 2 | Power | 800 to 1,200 |
| Buck converter | 1 | Stable 5 V supply | 200 to 400 |
| Chassis, RGB LED, buzzer, buttons, wires | - | Body and indicators | 600 to 1,100 |
| **Estimated total** | | | **about 4,200 to 6,700** |

\*Estimates only. Prices vary by shop and should be checked before purchase.

## 8. Software and Cloud Stack

- **Firmware (ESP32):** Arduino framework or MicroPython, reading sensors, publishing via MQTT, running local reflexes and motor control.
- **Messaging:** MQTT (a cloud broker such as HiveMQ Cloud, or a self-hosted Mosquitto broker on a cloud VM).
- **Neural model:** Python, using Brian2 or a custom leaky integrate-and-fire implementation, running as a service that subscribes to sensor topics and publishes motor commands.
- **Storage:** a lightweight database for logging events and sensor history.
- **Dashboard:** a small web application (or Grafana) showing live readings, neuron raster activity, synaptic weights and robot state.
- **Alerts and interaction:** a Telegram bot for notifications and "good/bad" reward input.
- **Development approach:** build and test locally first, then deploy the same code to the cloud, since the brain communicates only through MQTT.

## 9. Implementation Plan

| Phase | Work | Output |
|---|---|---|
| 1 | Order parts. Test the ESP32 with sensors. Test odour discrimination and left/right difference with the two MQ-3 sensors. | Working sensor readings, early feasibility check |
| 2 | Build the chassis, wire the motors and power, and drive the robot from simple commands. | Moving robot |
| 3 | Set up MQTT end to end (ESP32 to broker to a Python script and back). | Working data pipeline |
| 4 | Implement the neural model locally and connect it to the robot (odour seeking). | Robot steers toward odour |
| 5 | Add the edge reflex (looming dodge), obstacle avoidance and Wi-Fi fallback. | Reliable reflex behaviours |
| 6 | Add hunger/fear states, habituation and learning. | Adaptive behaviour |
| 7 | Build the dashboard and Telegram bot. | Live visualisation and interaction |
| 8 | Move the brain and broker to the cloud. Run tests. Fix problems. | Cloud-hosted system |
| 9 | Prepare the demo, report and presentation. Optionally prepare an offline full-connectome demo. | Final submission |

_Exact dates to be set against the submission deadline._

## 10. Evaluation and Testing

| Test | Measure | Success criterion (to be finalised after prototyping) |
|---|---|---|
| Odour seeking | Percentage of trials in which the robot reaches the odour source from several start positions | Reaches the source in most trials in a still room |
| Looming escape | Detection-to-motor-response time on the ESP32 and success rate against an approaching object | Dodges reliably at brisk hand speed from a set distance |
| Cloud latency | Round-trip time from sensor reading to motor command | Measured and reported, compared with the edge reflex time |
| Learning | Preference for the rewarded stimulus before and after training | Clear, repeatable change in behaviour |
| Resilience | Behaviour when Wi-Fi is disconnected and reconnected | Robot keeps moving safely and resumes cloud control |
| Reliability | Messages lost, reconnects needed, run time on battery | Reported with results |

## 11. Risks and Mitigation

| Risk | Mitigation |
|---|---|
| MQ-3 sensors respond slowly, need warm-up, and are affected by air movement | Test early, calibrate each sensor against its own baseline, demo in a still room, move the robot slowly |
| Cheap sensors cannot tell different odours apart | Use alcohol versus a neutral reference. For the learning demo, use light or another stimulus as the "odour" if needed |
| Cloud latency or Wi-Fi problems during the demo | Keep reflexes on the ESP32, add a local fallback mode, and keep a local-server backup |
| Cloud free tiers may require a payment card or have limits | Use a free MQTT broker tier and run the model on a local PC as a fallback |
| Full fly brain is too large for real-time use | Use a simplified olfactory subcircuit live. Treat the full model as an offline demo only |
| Time lost on mechanics | Use a simple wheeled chassis and keep legs or other mechanics out of scope |

## 12. Expected Outcomes

- A working cloud-connected robot that finds odour sources, escapes approaching objects, and changes its behaviour with experience and internal state.
- A reusable IoT architecture: ESP32, MQTT, cloud model, database and dashboard.
- A measured comparison of edge versus cloud decision-making.
- A clear, honest demonstration of how biological ideas can be applied to low-cost IoT systems.

## 13. Limitations

- The neural controller is a simplified model inspired by the fruit fly, not a copy of its brain.
- The robot's sensing is far simpler than the fly's (two analogue odour values instead of thousands of receptors).
- Gas sensors are slow and sensitive to air movement, so the demo requires controlled conditions.
- Learning is limited to simple associations and is not a general-purpose intelligence.

## 14. Future Work

- Add more sensors or a second odour channel for better discrimination.
- Run a larger part of the connectome model on a GPU server.
- Extend to multiple robots sharing one cloud brain.
- Replace the wheeled body with a legged body.
- Use on-device inference for a small edge version of the model and compare it with the cloud version.

## 15. References

Dorkenwald, S., Matsliah, A., Sterling, A.R. et al. (2024) 'Neuronal wiring diagram of an adult brain', *Nature*, 634, pp. 124-138.

Shiu, P.K., Sterne, G.R., Spiller, N. et al. (2024) 'A Drosophila computational brain model reveals sensorimotor processing', *Nature*, 634, pp. 210-219.

Stimberg, M., Brette, R. and Goodman, D.F.M. (2019) 'Brian 2, an intuitive and efficient neural simulator', *eLife*, 8, e47314.

_Please verify these reference details (volume, pages) against the original sources before submission._
