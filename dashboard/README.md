# dashboard/

Next.js 16 + TypeScript web dashboard (Phase 9). Live view over MQTT WebSockets, history from the
logger's API, all behind a password login.

## Setup and running

```powershell
cd dashboard
$env:npm_config_cache = "D:\DevCache\npm"; $env:NEXT_TELEMETRY_DISABLED = "1"
npm install                       # node_modules here, cache on D:
copy env.local.example .env.local # then set DASHBOARD_PASSWORD and DASHBOARD_SESSION_SECRET
npm run build
npm run start                     # http://localhost:3000   (or: npm run dev)
```

Needs the broker (WebSockets on 9001), and for the history page the logger API
(`brain\.venv\Scripts\python -m flybot_brain.logger api`). `infra\start-all.ps1` starts everything.

**Settings.** Broker host, WebSocket port, robot ID and the `dashboard` MQTT credentials come from the
repository's `.env`; `DASHBOARD_PASSWORD`, `DASHBOARD_SESSION_SECRET` and `LOGGER_API_URL` from
`dashboard/.env.local`. Both are read on the server only.

## How it works

- **Login:** a password form; the server checks `DASHBOARD_PASSWORD` and sets an HttpOnly cookie
  signed with `DASHBOARD_SESSION_SECRET` (12 h). `proxy.ts` sends every other page to the login
  and answers 401 for every other API.
- **Broker settings** (including the `dashboard` MQTT password) reach the browser only from
  `/api/mqtt-config`, after login. The browser still holds that password once signed in; the
  known issue in the root README (proxy MQTT through the server) still applies.
- **Contract:** `lib/contract.ts` builds every message. Each tab has its own sender name
  (`dash-` + 6 hex) used in `src` and in the client ID `flybot-01-dash-xxxxxx`; `seq` per topic
  starts at 0; `up` is milliseconds since the page loaded. `cmd/mode` is published retained, QoS
  as in contract section 1. The same module is used by the smoke test.
- **Live page** (`/`): broker / robot / brain online, robot state, decision, battery, Wi-Fi, round
  trips, brain lag (`brain/status.lag`), tick and sensor health; hunger, fear and habituation
  gauges; the decision's "why" and learned preferences; 60 s charts of odour left/right, distance
  and light; spike counts per neuron group; the learned approach/avoid weights as heat maps
  (2,000 Kenyon cells each); controls; recent events.
- **Controls:** mode auto / manual / e-stop (`cmd/mode`), a drive pad (`cmd/manual` at 10 Hz while
  held, stop on release), reward / punish (`reward`), sleep / wake (`brain/sleep`), hunger / fear
  (`brain/control` `state`), lesion switches, snapshot save / load, reset state / weights
  (`brain/control`). The brain's acknowledgement for this tab's own commands is shown.
- **History page** (`/history`, through `/api/history/*` to the logger API): odour and light over a
  chosen window, a "day in the life" strip (meals, startles, sleep), latency histograms, trial
  success rates, the event timeline and message reliability.
- **Smoothness:** React re-renders at most 4 times a second; charts redraw from rolling buffers 5
  times a second; no chart library. Layout: CSS grid, large type, dark theme; one column on a phone.

## Smoke test

```powershell
infra\start-broker.cmd local       # in another window
cd dashboard; npm run build; cd ..
brain\.venv\Scripts\python dashboard\scripts\smoke_all.py
```

Starts brain (with logger, scratch database in `.tmp\smoke`), logger API (port 8765), the simulator
in real time and the built dashboard (port 3100), then checks the login flow and protected routes
over HTTP, and runs `scripts/smoke.ts` (Node, the dashboard's own contract module over WebSockets):
live data must arrive, and every control is published; an independent subscriber validates each
message against the contract schemas, and the brain must acknowledge every `brain/control` command.
Last run: 21/21 checks passed.
