# Layer 2 — live end-to-end (reproducible)

`run.py` deploys + runs Chapters A→B→C and asserts each gate, then tears down. SP setup/grants are
one-time (idempotent). Run Layer 0/1 (`tests/check.sh`) first.

## Config (no hardcoded specifics)

The harness reads everything from **`tests/config.env`** (gitignored). Copy the template and fill in
your values once:

```bash
cp tests/config.env.example tests/config.env   # then edit: catalog, warehouse, group, profiles, …
```

Bash scripts source it automatically; the Python harness auto-loads it (inline `WORKSHOP_X=…` or CLI
flags still override).

## Identities (all non-interactive, via `~/.databrickscfg` profiles)

| Role | config.env var | Used for |
|------|----------------|----------|
| Deployer (non-admin, privileged) | `WORKSHOP_PROFILE` | deploy + run A/B/C; surfaces participant-level permission gaps |
| Redaction (non-priv human) | `WORKSHOP_GROUPB_PROFILE` | direct-SQL masking contrast (a PAT is fine for SQL) |
| OBO privileged SP (M2M) | `WORKSHOP_SP_PRIV_PROFILE` | MCP / review-app OBO: sees unmasked |
| OBO non-priv SP (M2M) | `WORKSHOP_SP_REG_PROFILE` | MCP / review-app OBO: sees masked |
| Admin | `WORKSHOP_ADMIN_PROFILE` | ONE-TIME: create SPs, `USE CATALOG` grant |

PATs can't do OBO (apps reject them), so the OBO/app differential uses two **service principals**
(OAuth M2M); direct-SQL redaction uses the two human profiles.

## One-time setup (idempotent — re-run safely)

```bash
# 0. venv for the MCP OBO probe + pytest (if public PyPI is blocked, use your internal proxy)
python3 -m venv tests/e2e/.venv
tests/e2e/.venv/bin/pip install -r tests/e2e/requirements.txt

# 1. create the 2 SPs, group-assign, mint OAuth secrets, assign to workspace (ACCOUNT-admin auth).
#    Reads WORKSHOP_* from config.env; writes the SP M2M profiles into ~/.databrickscfg (secrets never printed).
python3 tests/e2e/setup_sps.py

# 2. admin-only, one-time: GRANT USE CATALOG to the two SPs (a participant can't self-issue this, and the
#    catalog is never dropped, so it persists). Pass the SP application ids printed by setup_sps:
python3 tests/e2e/grant_sps.py --admin-profile "$WORKSHOP_ADMIN_PROFILE" \
    --deployer-profile "$WORKSHOP_PROFILE" --prefix "$WORKSHOP_PREFIX" \
    --priv-app-id <id> --reg-app-id <id>
```

> The **deployer-side** grants (schema `USE/SELECT/EXECUTE` + app `CAN_USE`) are **re-applied
> automatically by `run.py`** before each OBO probe — teardown drops the schemas and destroys the apps
> every run, so they must be re-issued against the freshly-recreated objects. Mirrors a participant
> re-sharing their slice after redeploying; you don't run them by hand.

## The repeatable test

```bash
tests/check.sh                       # Layer 0/1 first

# full live run (deployer + both differentials), then teardown — all profiles/values from config.env
python3 tests/e2e/run.py

# fast re-assert against an already-deployed slice (no deploy/run, no compute)
python3 tests/e2e/run.py --asserts-only --no-teardown

python3 tests/e2e/run.py --teardown-only          # clean up
```

Flags: `--stages a,b,c`, `--asserts-only`, `--no-teardown`, `--teardown-only`, `--accuracy-threshold`
(default 80), and `--profile / --catalog / --warehouse / --privileged-group / --groupb-profile /
--sp-priv-profile / --sp-reg-profile` to override any `config.env` value.

## Gates

- **preflight:** deployer entitlements (info) + FM-endpoint queryable (ai_query).
- **A:** deploy/run; row counts; 5 UC functions return rows; masking — privileged unmasked vs groupb
  `***REDACTED***`/NULL; 2 Genie spaces.
- **B:** deploy; MCP app RUNNING; agent deploy job; agent serving endpoint exists.
- **B (agent + MCP OBO):** `agent_predict` queries the deployed agent with an incident id, asserting a
  valid play AND non-empty tool evidence. **MCP OBO** — via `mcp_probe.py` (venv), the privileged SP sees
  unmasked and the non-privileged SP sees `***REDACTED***` through the hosted MCP.
  *(Prereq: Model Serving on-behalf-of-user enabled — Settings ▸ Previews — for the agent's OBO.)*
- **C:** rules synthesized (each plan ends in `recommend_action`); approve; recommendations written;
  accuracy vs. hidden `scenario_label` ≥ threshold; **review-app OBO** — privileged SP unmasked,
  non-privileged SP `***REDACTED***` (same app, identity forwarded; badge gating too).

## Documented-manual (printed, not asserted)

- **MLflow traces** (a UI surface) — open the `/Users/<you>/aiapps-chapter-c-triage` experiment.

Both OBO differentials (MCP and review app) are automated via the two SPs; the MCP one needs the
`tests/e2e/.venv` (step 0). The unit suite also runs under real `pytest` from that venv.
