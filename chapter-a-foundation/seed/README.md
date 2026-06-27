# Seed — the single source of truth

The CSVs here are the authoritative workshop dataset. They **ride in the Chapter A bundle**: on
`bundle deploy` they sync to the workspace, and `01_build_data_and_governance.py` copies them into a
**per-user volume** (`{prefix}_ti_intel.seed`) and loads them into each participant's `{prefix}_ti_*`
tables. There is **no shared seed volume** — the data is tiny, so each participant materializes their
own copy.

| File | Rows | Into |
|------|------|------|
| `threat_actors.csv` | 40 | `{prefix}_ti_intel.threat_actors` |
| `campaigns.csv` | 146 | `{prefix}_ti_intel.campaigns` |
| `indicators.csv` | 5973 | `{prefix}_ti_intel.indicators` (synthetic + real Abuse.ch/URLhaus mix) |
| `indicator_intel.csv` | 2773 | `{prefix}_ti_intel.indicator_intel` (URLhaus verdict side-table) |
| `incidents.csv` | 300 | `{prefix}_ti_intel.incidents` (arriving queue for triage) |
| `investigations.csv` | 320 | `{prefix}_ti_intel.investigations` |
| `accounts.csv` | 2000 | `{prefix}_ti_risk.accounts` |
| `risk_signals.csv` | 2751 | `{prefix}_ti_risk.risk_signals` |
| `account_risk_scores.csv` | 11725 | `{prefix}_ti_risk.account_risk_scores` |
| `account_actions.csv` | 480 | `{prefix}_ti_risk.account_actions` |

`indicator_intel.csv` doubles as the **URLhaus verdict source** — the table-backed `enrich_indicator`
UC function looks indicators up in it (no external call).

## Regenerating

`generate_data.py` (stdlib-only, fixed seed 1337, pinned to 2026-06-01) regenerates everything
deterministically — scenario-driven so symptoms↔actions↔incidents correlate. It writes to `./data/`
relative to itself; copy the resulting CSVs back here flat. The current CSVs already include the
cohesion fix (only malware_delivery incidents claim a URLhaus family; others get synthetic IOCs with
no family attribution).

`real_urlhaus/` holds the real Abuse.ch/URLhaus IOC pull the generator weaves in.
