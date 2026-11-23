# CLAUDE.md

Standing rules for working in this repository. They apply in every session.

## 1. D: drive only

Never download, install, cache or write anything on the C: drive. Everything goes on D::
the project, Python virtual environments, pip cache, npm cache, PlatformIO packages and
toolchains, databases, logs and temp files.

- Before installing anything, say where it will go and how it stays on D: — for example
  by setting `PIP_CACHE_DIR`, the npm cache location (`npm config set cache D:\...` or
  `npm_config_cache`), and `PLATFORMIO_CORE_DIR` to folders on D:.
- Do not use the session scratchpad or system temp folders (they are on C:). Use a
  git-ignored folder on D: such as `D:\Python\FlyBot\.tmp\` for throwaway files.
- Run ad-hoc Python with `-B` so no bytecode cache is written elsewhere.
- If something can only install to C:, stop and ask first.

## 2. Commit per completed unit of work

Commit once per completed logical unit of work: a finished step from the implementation plan,
a working feature, or a fix. Do not commit per file. Aim for a handful of commits per phase, each
with a clear message saying what was done and why.
Use conventional-commit prefixes (`docs:`, `chore:`, `feat:`, `fix:`).
Never push unless asked.

## Project pointers

- Context: `docs/FlyBot_Project_Proposal.md`, `docs/FlyBot_Software_Implementation_Plan.md`.
- Decisions that override the plan: one monorepo, no Docker (native Mosquitto on Windows
  for development, systemd services on an Azure Ubuntu VM), SQLite, brain + logger +
  Telegram bot in one Python program, separate Python simulator, Next.js + TypeScript
  dashboard, ESP32 firmware on Arduino via PlatformIO using ESP-IDF `esp-mqtt`.
- The MQTT message contract is the source of truth: `docs/message-contract.md` and
  `docs/schemas/`. Change the contract and schema first, then code.
