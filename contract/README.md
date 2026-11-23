# contract/: the message contract in code

`flybot_contract` is shared by the brain, the simulator and tools, so there is exactly one
implementation of:

| Module | What |
|---|---|
| `contract.py` | `Contract` (topics, schema validation, encode/decode), `Outbox` (v / seq / up per topic, validated before a seq is used), `offline_status` (the last-will payload). Schemas are read from `docs/schemas/`, the source of truth. |
| `streams.py` | `StreamTracker`: per-sender sequence rules from contract §6 (duplicates, late messages, loss, wrap, restarts, idle senders). |
| `clock.py` | `RobotClock`: monotonic robot time from `sensors.up` (contract §7.1): a reboot step counts 0, and gaps over 500 ms are capped at 100 ms. |

It is installed **editable** into each program's venv, so it always reads this checkout:

```powershell
sim\.venv\Scripts\python -m pip install -e contract
brain\.venv\Scripts\python -m pip install -e contract
```

(Set `PIP_CACHE_DIR`, `TMP` and `TEMP` to D: first, as in the other READMEs.)

`tests/test_contract_docs.py` checks that `docs/message-contract.md` and `docs/schemas/` agree:
- schemas are valid, strict and pin their version;
- every example matches;
- the §1 version column is correct;
- the §4 permission table matches the ACL text;
- dashboard topics require a tab `src`;
- 59 invalid messages are rejected.

It runs with every `pytest` run in `sim/` and `brain/`.
