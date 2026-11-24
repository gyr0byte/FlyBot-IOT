# Overnight decisions (2026-11-25, autonomous session)

Decisions I would normally have asked about (CLAUDE.md rule 3), made myself during the overnight
session as instructed, so they can be reviewed and reversed. Each entry: what, the options, what I
chose and why. Hard limits kept: no contract changes, no pushing, no force operations, everything
on D:.

## Phase 8 – logger

1. **Where the logger runs.**
   Options: (a) inside the brain program on the brain's MQTT connection (as the plan and the logger
   stub say); (b) a separate program.
   Chosen: (a) by default, plus a standalone mode. The logger is its own module
   (`flybot_brain/logger/`); the brain's link hands it every inbound message (including the brain's
   own messages echoed back by the broker) before the brain handles it. `python -m
   flybot_brain.logger run` runs the same logger on its own connection (user `brain`, a different
   client ID) for logging without a brain. Why: matches the plan and the contract (the logger "uses
   the `brain` connection"); the standalone mode makes tests and simulator-only sessions easy.

2. **HTTP API library.** Options: FastAPI + uvicorn (suggested), standard-library http.server.
   Chosen: FastAPI + uvicorn (httpx for tests), installed into the brain venv with the pip cache on
   D:. Why: suggested, gives validation and an OpenAPI page for free, small dependency.

3. **Two extra tables beyond the plan's list.** `status` (robot and brain status messages, so
   online/offline history and robot state are available) and `reliability` (received / invalid /
   lost / duplicate counts per topic and sender, the plan's "counts invalid and missing messages").
   Rewards go into `events` (source = sender); control topics (`cmd/mode`, `cmd/sleep`,
   `brain/control`, `brain/sleep`) go into `commands` with their sender. Why: keeps the plan's
   tables and makes the reliability results queryable.

4. **What counts as "missing".** Sequence gaps per (topic, sender), using the contract's rules: a
   new `bid` is a reboot, not a gap; retained messages and the offline last will (fixed at seq 0)
   are not stream steps. Without the last-will rule every clean disconnect showed up as a duplicate.

5. **Experiment tools run brains with `--no-logger`.** Options: log experiments into the main
   database, a separate database per experiment, or not at all. Chosen: not at all (the tools keep
   their own CSVs). Why: lockstep experiments produce millions of rows at 12x real time and would
   bury the robot's own history; `brain.yaml` keeps the logger on for normal runs.

6. **Retention at start-up.** The brain prunes raw rows older than `logger.retention_days` (7)
   when it starts, rather than on a timer. Why: simplest, no extra thread; the VM restarts the brain
   on deploy. A manual `prune` command exists.

## Phase 9 – dashboard

7. **Login.** A single password (`DASHBOARD_PASSWORD`) and an HMAC-signed HttpOnly cookie (12 h),
   checked in `proxy.ts` (Next 16's name for middleware). The password and the cookie secret live
   in `dashboard/.env.local` (git-ignored; I generated random values for this laptop, see the
   summary), not in the repository's `.env`, so I did not touch your `.env`. Options considered:
   NextAuth (heavy, needs a provider), HTTP basic auth (no logout, browser prompt).

8. **MQTT credentials in the browser.** The browser connects to the broker directly, as the plan
   says, so it must hold the `dashboard` MQTT password. It gets it from `/api/mqtt-config` only after
   login. Proxying MQTT through the Next server (the root README's known issue) would remove that,
   but it is a bigger change; left as the known issue.

9. **No chart library.** Canvas line charts and heat maps, SVG on the history page. Why: small, no
   extra dependencies, easy to throttle (charts redraw 5 times a second, React at most 4).

10. **Dependencies.** next 16.4.6, react 19.3.1, mqtt 5.16.1; dev: typescript 5.9.3 (not 7: Next's
    build uses TypeScript's JS API), @types/*. node_modules in `dashboard/`, npm cache `D:\DevCache\npm`,
    Next telemetry off.

11. **Smoke test without a browser.** Options: Playwright (downloads browsers; would need
    `PLAYWRIGHT_BROWSERS_PATH` on D:, several hundred MB), or testing the dashboard's own message
    code from Node plus the pages over HTTP. Chosen: the latter. `scripts/smoke.ts` runs the same
    `lib/contract.ts` the pages use; an independent Python subscriber validates every message with
    the contract schemas. What it does not test: rendering and clicking in a real browser.

12. **History through the dashboard server.** The browser calls `/api/history/*`, which the Next
    server forwards to the logger API (127.0.0.1). The logger API therefore needs no CORS and is not
    exposed; only whitelisted endpoints are forwarded.

13. **One start script.** `infra/start-all.ps1` opens one window per part (broker, brain with
    logger, logger API, dashboard, optional simulator); closing a window stops that part. No
    background services (as decided in Phase 2).

## Phase 10 – Telegram bot

14. **Transport.** Options: python-telegram-bot (async framework, extra dependency), aiogram, or the
    Bot API directly. Chosen: the Bot API over HTTPS with the standard library (getUpdates long
    polling, sendMessage), behind a small interface with a fake for tests. Why: two calls are all we
    need, no new dependency, easy to test without a token.

15. **Several allowed chats without a new variable.** `TELEGRAM_ALLOWED_CHAT_ID` (already in
    `.env.example`) now accepts a comma-separated list. Why: the plan mentions adding a demo chat;
    keeping the existing name avoids touching `.env.example` semantics. Messages from other chats
    are ignored silently (logged), not answered.

16. **`/resume` added.** `/stop` publishes a retained `estop`, which only `cmd/mode` `auto` or
    `manual` clears (contract §5). Without `/resume` the phone could stop the robot but never
    restart it. `/resume` publishes `auto`; the bot is allowed to publish `cmd/mode`.

17. **Alerts beyond the list.** Also "brain went offline / back online" (from `brain/status`), and
    "caught up" after a lag alert, and "fallback ended". Why: the same transitions as the requested
    alerts, and a lag alert without its end is confusing. No alert on the first status after the bot
    starts (that is the current state, not a change).

18. **Learned-preference alert levels.** Announced when |pref| ≥ 0.5 (the dashboard and the
    learning results call that a clear preference), once per cue and sign; re-armed below 0.25.

19. **Where it runs.** In the brain program on its own connection (user `bot`), as the plan says;
    it starts only when a real-looking token and a chat ID are set, so the brain runs unchanged
    without them; `--no-bot` and the experiment tools switch it off.


## Verification

20. **A real-browser test after all (Playwright).** I had first skipped it (decision 11). With
    time left, I added Playwright as a dashboard dev dependency with its browser downloaded to
    `D:\DevCache\ms-playwright` (`PLAYWRIGHT_BROWSERS_PATH`); nothing went to the default location on
    C:. `smoke_all.py --browser` drives headless Chromium through the dashboard (see
    dashboard/README.md). Why: the plan's Phase 9 done-when is about the page itself ("every panel
    updates, every control works, stays smooth"), which the Node test could not show.

21. **Two dashboard bugs found by the browser test, fixed.** (a) After login the browser was
    redirected from the address it used (here 127.0.0.1) to `localhost`, because redirect URLs were
    built from `req.url`; the session cookie then did not apply. On the cloud VM every user would
    have been sent to `localhost`. Now login / logout answer with relative `Location` headers and the
    login guard builds its URL from the request's `Host` (or `X-Forwarded-Host`). (b) A React
    hydration mismatch (error #418): the live page rendered nothing on the server and everything in
    the browser; it now renders the same placeholder first. Also: chart lines at 0 or full scale sat
    on the canvas edge and were invisible; the charts now have 6 px of vertical padding.
