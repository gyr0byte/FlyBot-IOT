# Overnight summary (2026-11-25, 02:37 – 03:35)

Autonomous session: Phases 8, 9 and 10 built, tested and committed. No contract changes, nothing
pushed, no force operations, everything on D:. Every decision I would normally have asked about is
in [overnight-decisions.md](overnight-decisions.md), with options and reasons, for you to review.

## What was built

| Phase | What | Where |
|---|---|---|
| 8 | **Logger**: a module of the brain program on the brain's MQTT connection; validates every message, counts invalid / lost / duplicate per topic and sender, writes SQLite in batches (sensors, events, commands, brain_state, weights, latency, status, trials, reliability); retention; CSV export; read-only FastAPI (sensors, events, latency stats, trials, day-in-the-life timeline, reliability) | `brain/flybot_brain/logger/` |
| 9 | **Dashboard**: Next.js 16 + TypeScript; password login; live overview (online status, robot state, decision, battery, Wi-Fi, round trips, brain lag, hunger / fear / habituation, why, learned preferences); 60 s charts; spike bars; approach/avoid weight heat maps; all controls; history page from the logger API; throttled redraws; projector and phone layout | `dashboard/` |
| 10 | **Telegram bot**: module on its own connection (user `bot`); /status /good /bad /sleep /wake /stop /resume /learned; alerts; allowed chats only; fake Telegram for tests | `brain/flybot_brain/bot/` |
| – | **One start script** for broker + brain (with logger) + logger API + dashboard (+ simulator) | `infra/start-all.ps1` |

## Commits (all local, not pushed)

| Commit | What |
|---|---|
| `bc37d2c` | feat(brain): logger module, CSV export and read API (Phase 8) |
| `302139d` | feat(dashboard): Next.js live dashboard, history and controls (Phase 9) |
| `3b0a5f0` | feat(brain): Telegram bot module (Phase 10) |
| `5875620` | fix(dashboard): host-preserving redirects, hydration, chart edges; real-browser smoke test |

Plus this summary (committed last). Nothing pushed: `git log origin/main..HEAD` lists these.

## How to run everything

```powershell
# once: dashboard dependencies and build (npm cache on D:)
cd dashboard
$env:npm_config_cache = "D:\DevCache\npm"; $env:NEXT_TELEMETRY_DISABLED = "1"
npm install; npm run build; cd ..

# every time: one window per part (close a window to stop that part)
powershell -ExecutionPolicy Bypass -File infra\start-all.ps1          # broker, brain + logger, logger API, dashboard
powershell -ExecutionPolicy Bypass -File infra\start-all.ps1 -Sim     # ... plus the simulator live view
```

Then open **http://localhost:3000** and sign in with `DASHBOARD_PASSWORD` from
`dashboard\.env.local`. I generated a random password and session secret there for this laptop
(git-ignored); change them if you like (template: `dashboard\env.local.example`).

Useful extras:
- `brain\.venv\Scripts\python -m flybot_brain.logger stats` (row counts, lost / invalid messages),
  `... export --out D:\somewhere` (one CSV per table), API docs at http://127.0.0.1:8000/docs.
- `brain\.venv\Scripts\python dashboard\scripts\smoke_all.py --browser` (the end-to-end check below).

## What you need to do in the morning (Telegram)

1. Telegram → **@BotFather** → `/newbot` → pick a name and a username ending in `bot` → copy the token.
2. Send your new bot any message.
3. Get your chat ID: open `https://api.telegram.org/bot<token>/getUpdates` and read `"chat":{"id": ...}`
   (or ask **@userinfobot**).
4. In the repository `.env` set `TELEGRAM_BOT_TOKEN=<token>` and `TELEGRAM_ALLOWED_CHAT_ID=<id>`
   (several IDs comma-separated).
5. Start the brain; it logs "telegram bot running for 1 chat(s)". Send `/status`.

## Test results

| Suite | Result |
|---|---|
| brain (`brain\.venv\Scripts\python -m pytest`) | **371 passed** (232 brain incl. 13 logger and 18 bot tests, 139 contract) |
| simulator (`sim\.venv\Scripts\python -m pytest`) | **288 passed** (149 simulator, 139 contract; broker tests included) |
| dashboard typecheck and build (`npx tsc --noEmit`, `npm run build`) | clean, no warnings |
| end-to-end smoke (`smoke_all.py --browser`): broker + brain + logger + API + simulator + dashboard + headless Chromium | **45/45 checks passed** |

The end-to-end check covers: the login flow (wrong and right password, protected pages and APIs, host-
preserving redirects); live data reaching the page (every overview panel, gauges, charts, spike
bars, heat maps); every control clicked in the browser and every resulting message contract-valid
(validated by an independent subscriber); every `brain/control` command acknowledged by the brain;
the retained `cmd/mode`; 60 frames/s while live; no sideways scrolling at phone width; the history
page; no browser console errors. Screenshots: `.tmp\smoke\dashboard-desktop.png`,
`dashboard-phone.png`, `history-desktop.png`.

**Plan done-when checks:**
- **Phase 8** ("a 10-minute run is fully recorded, and the export opens cleanly in a notebook"):
  10 minutes of real-time running (brain + logger + simulator, 10 robot boots) recorded 12,039
  sensor messages at exactly 20.0 Hz, 11,039 commands, 3,016 brain states, events, weights, status
  and latency, with **0 invalid, 0 lost, 0 duplicates** on every topic; the CSV export opens in pandas.
- **Phase 9** ("every panel updates, every control works, the page stays smooth"): the browser test
  above, over about a minute rather than 10 minutes.
- **Phase 10** ("every command works from your phone and each alert arrives once per event"): the
  commands and the once-per-event rule are tested with a fake Telegram, and the bot's MQTT side on
  the real broker (every command accepted by the ACL and contract-valid). Not yet with real Telegram.

## Unfinished / not done

- **Telegram with a real token:** not possible without one (steps above). Everything else about the
  bot is tested.
- **`infra\start-all.ps1`** was only checked with `-DryRun` (it opens windows; the broker was
  already running and a second one would have clashed). The commands it runs are the ones the
  smoke test ran.
- **The 10-minute smoothness run in a browser:** the browser test measures about a minute.
- **`trials` table:** the logger has the table and `Logger.add_trial`, and the API reports success
  rates, but no tool writes trials into it yet (the simulator's batch tools still write CSVs).
- **Known issue unchanged:** the browser holds the `dashboard` MQTT password after login
  (decision 8; root README "Known issues").
- **Not started:** Phase 7 (real robot integration, needs the firmware), Phase 11 (cloud: the
  dashboard already honours `X-Forwarded-Host/-Proto` for a reverse proxy, and supports `wss://`
  via `MQTT_USE_TLS` / `MQTT_WS_URL`), Phase 12–13, and Phase 5.13 (FlyWire circuit).

## Decisions

See [overnight-decisions.md](overnight-decisions.md). In short:

1. Logger inside the brain program on the brain's connection (plus a standalone mode).
2. FastAPI + uvicorn for the read API (installed in the brain venv, pip cache on D:).
3. Two extra tables (`status`, `reliability`).
4. "Missing" = sequence gaps per topic and sender; reboots, retained messages and the offline last
   will are not gaps.
5. Experiment tools run brains with `--no-logger` (and `--no-bot`).
6. Retention (7 days of raw rows) applied when the brain starts.
7. Dashboard login: one password, signed HttpOnly cookie; secrets in `dashboard\.env.local`
   (generated for this laptop), your `.env` untouched.
8. The browser connects to MQTT directly (gets the credentials after login): the known issue remains.
9. No chart library (canvas / SVG).
10. Versions: Next 16.4.6, React 19.3.1, mqtt 5.16.1, TypeScript 5.9.3 (not 7).
11. Node-level smoke test first, then (20) a real browser via Playwright, browsers on D:.
12. History through the dashboard server (whitelisted proxy to the logger API on 127.0.0.1).
13. One start script, one window per part, no services.
14. Telegram Bot API with the standard library (no framework), fake client for tests.
15. `TELEGRAM_ALLOWED_CHAT_ID` accepts a comma-separated list.
16. `/resume` added (clears an e-stop from the phone).
17. Extra alerts: brain offline / online, caught up, fallback ended; no alert on first sight.
18. "Learned" alert at |preference| ≥ 0.5, re-armed below 0.25.
19. Bot inside the brain program, only when a real token and chat are configured.
21. Two dashboard bugs found by the browser test and fixed (redirect host, hydration), plus chart edges.
