# Tests

A pyramid: cheap deterministic checks first; the expensive live run last.

## Layer 0 + 1 (built — no workspace, runs in seconds)

```bash
tests/check.sh            # py_compile + bundle validate (×3) + unit tests
python3 tests/run.py      # just the unit tests (no pytest needed)
pytest tests/             # same tests, if pytest is installed
```

- **Layer 0 — static gate:** byte-compiles every notebook/module and runs `databricks bundle validate`
  for all three chapters (read-only; profile from `WORKSHOP_PROFILE` in `tests/config.env`, or `DATABRICKS_PROFILE`).

> **Config:** the harness carries no hardcoded workspace values. Copy `tests/config.env.example` →
> `tests/config.env` (gitignored) and fill in your catalog / warehouse / group / profiles. Bash scripts
> source it; the Python e2e harness auto-loads it.
- **Layer 1 — unit tests** of the pure, load-bearing logic (35 tests):
  - `test_chapter_c_dsl.py` — the runbook DSL: `validate_plan`, `repair_plan` (incl. the `recommend_action`
    slimming and arg/alias fixes), `to_sql_string`, `extract_json`, `resolve_ref`/`resolve_args`, `derive_prefix`.
  - `test_chapter_b_core.py` — `threatintel_core`: the 5-tool registry, `tool_statement`/`call_tool`, prefix.
  - `test_agent_logic.py` — the agent's `_decision`, `_incident_id_from`, `TOOL_SPECS` (mlflow/databricks stubbed).

The pure logic was refactored into importable modules so these can `import` it: the Chapter C DSL now
lives in `chapter-c-loops/src/common.py` (the notebooks pull it in via `%run ./common`); `threatintel_core`
was already a plain module. `_loaders.py` loads them without a workspace; `conftest.py` wraps them as
pytest fixtures and `run.py` is the no-dependency runner.

## Layer 2 (not built yet — live, ordered end-to-end + teardown)

`tests/e2e/run.py` deploys and runs the full sequence against your prefix, asserts at each gate, then
tears down. All values come from `tests/config.env` (see `tests/e2e/README.md`):

- deploy as a **privileged** identity (`WORKSHOP_PROFILE`, in `WORKSHOP_PRIVILEGED_GROUP`); catalog
  `WORKSHOP_CATALOG`; warehouse `WORKSHOP_WAREHOUSE`.
- **Gating/redaction check** uses a second identity `WORKSHOP_GROUPB_PROFILE` (must NOT be in the
  privileged group): the same query/UC-function/MCP/app call returns `***REDACTED***` / NULL for it.
  - Prereqs (admin, one-time): workspace user authorization (apps OBO) **and** Model Serving
    on-behalf-of-user (Beta, for the agent).
- Teardown: **`tests/teardown.sh`** (`PROFILE`/`CATALOG`/`PREFIX`/`WAREHOUSE`, or `WORKSHOP_*` from
  `config.env`) destroys the three bundles, the agent endpoint, the `{prefix}_ti_*` schemas, the Genie
  spaces, and the Chapter C MLflow experiment.

Gates the e2e would assert: A → schemas/tables/row-counts, masking (groupa vs groupb), 5 UC functions
return rows, 2 Genie spaces exist; B → MCP app up + one tool call, agent endpoint returns valid JSON;
C → `runbook_builder` proposes valid rules, approve, `triage_runner` writes recommendations and clears an
accuracy threshold vs. the hidden `scenario_label`, traces exist.
