# Chapter C — Agent loops

The payoff chapter: a **two-agent loop around a human approval gate**, closing the workshop.

```
runbook_builder (LLM synthesis)  →  PROPOSED rules  →  [human approves]  →  triage_runner (autonomous)  →  recommendations
```

1. **`runbook_builder` job** — two-stage `ai_query`: *map* over `{prefix}_ti_intel.investigations` to
   extract `{symptoms, steps, containment, outcome}` per investigation, then *reduce* into ~6–10
   **runbook rules** written to `{prefix}_ti_intel.runbook_rules` as `PROPOSED`. Each rule's
   `action_plan` is a (Domain-Specific-Language) DSL step list validated against a fixed action enum.
2. **Human approval gate** — promote `PROPOSED → APPROVED` (in `explore.py`). The autonomous agent
   only ever runs approved rules - human in the loop.
3. **`triage_runner` job** — for each NEW incident: a row-parallel `ai_query` matches it to an approved
   rule (reading only the narrative, never the hidden `scenario_label`), then runs that rule's plan by
   calling the **Chapter A UC functions directly** (`{prefix}_ti_tools.*`). The terminal
   `recommend_action` is a **pure decision**; the **orchestrator** writes one auditable row to
   `{prefix}_ti_risk.triage_recommendations`. Unmatched incidents flip to `uncovered`.

**Option A (this build):** the triage agent calls the tools **directly as UC functions** — runs as
the participant (invoker-rights masking applies), no MCP dependency, no extra prereqs. Chapter B shows
the same tools reachable via the hosted OBO MCP; here we keep the loop self-contained.

Both jobs run on **serverless via `ai_query`** — no warehouse needed.

## The human approval gate — two ways

The gate (promote `proposed → approved`) can be done either in the notebook or in a **Review UI app**:

- **`explore.py`** — a notebook `UPDATE` (simplest; self-contained).
- **`review_console`** — a Databricks **App** (`review-app/`, OBO) that lists proposed rules with
  Approve/Reject buttons, a triage feed, and an incident drill-down. It runs each write **as the
  caller**, so a member of `privileged_group` sees unmasked detail + the buttons; everyone else is
  read-only and masked — the same governance axis as Chapter A, now in a browser. (Same OBO model as
  Chapter B's MCP; needs the workspace admin to enable user authorization once.)

## Run it

1. Edit **`config.yml`** — set `catalog`, `warehouse_id`, `privileged_group` (same as Chapters A/B).
2. ```bash
   databricks bundle deploy -t dev
   databricks bundle run runbook_builder -t dev     # propose rules
   ```
3. **Approve** — either open `explore.py` and run the approval cell, **or** start the Review UI and
   click Approve:
   ```bash
   databricks bundle run review_console -t dev      # then open the app URL (databricks apps list)
   ```
4. ```bash
   databricks bundle run triage_runner -t dev       # triage NEW incidents
   ```
5. Review the recommendations + the **accuracy vs. the hidden `scenario_label`** — in the Review UI's
   triage feed / incident drill, or in `explore.py` (the agent matched on narrative only).

## Notes
- The jobs run as **you** (dev-mode `run_as` = the deploying participant), so they inherit your
  `EXECUTE` on `{prefix}_ti_tools` and your masking. In production these would run as a service
  principal (the "autonomous, no-human" identity).
- Re-running `runbook_builder` refreshes the `PROPOSED` set (and you re-approve); `uncovered`
  incidents from a prior triage pass feed the next synthesis.
- Model: `ai_query` auto-picks a Claude FM endpoint; pin one via `model_endpoint` in `config.yml`.
- **Review UI OBO prereq (one-time, admin):** `review_console` uses `user_api_scopes: [sql]`, which
  only takes effect after a workspace admin enables user authorization; restart the app afterward.
  Until then it falls back to the app's service principal and the approve/reject writes fail. (Same
  prereq as Chapter B's MCP.) The app is named `review-<your-handle>`; find its URL with
  `databricks apps list`.
