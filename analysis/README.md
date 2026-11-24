# analysis/

Evaluation notebooks and scripts (Jupyter, pandas, matplotlib). Not yet created.

One notebook per test in the proposal: odour seeking (neural vs baseline), looming escape
timing, latency (edge vs local vs cloud), learning, Wi-Fi resilience, reliability (message
loss from sequence gaps, reconnects, battery life) and behaviour (hunger, fear, habituation,
sleep). Input comes from CSV exports of the SQLite database and from simulator batch runs.

Data files and exported databases are git-ignored; commit notebooks with outputs cleared.

## `brian2/` (Phase 5, step 5.12)

Brian2 rebuild of the designed circuit's receptor → antennal lobe → mushroom body path, compared
with the FlyBot engine. See [brain/README.md](../brain/README.md#brian2-check-phase-5-step-512).

```powershell
brain\.venv\Scripts\python analysis\brian2\engine_rates.py      # engine rates -> .local/experiments/brian2/engine.json
analysis\.venv\Scripts\python analysis\brian2\brian2_rates.py   # Brian2 rates, comparison -> report.md
```

The analysis venv holds Brian2 2.10.1 and PyYAML (installed with `PIP_CACHE_DIR` on D:). Brian2 uses
its numpy code target, so no compiler or code cache is needed.
