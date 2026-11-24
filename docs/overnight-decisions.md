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

