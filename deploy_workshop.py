# Databricks notebook source
# MAGIC %md
# MAGIC # Deploy the workshop — from inside your workspace (no laptop, no local CLI)
# MAGIC
# MAGIC This notebook **deploys and runs** each chapter's Databricks Asset Bundle for you. It's the
# MAGIC one-button alternative to typing `databricks bundle deploy` / `bundle run` in the web terminal —
# MAGIC it bootstraps the CLI, authenticates as you, and runs the right commands per chapter.
# MAGIC
# MAGIC ### How to use it
# MAGIC 1. Clone this repo into your workspace as a **Git folder** (this notebook sits at the repo root).
# MAGIC 2. Fill the **widgets** at the top: `catalog`, `privileged_group`, `warehouse_id` (your values).
# MAGIC 3. Pick a **step** and **Run All**. Work through the steps in order, exploring each chapter's
# MAGIC    `explore.py` in between:
# MAGIC    - **A · Foundation** → explore `chapter-a-foundation/explore.py`
# MAGIC    - **B · Spectrum** → explore `chapter-b-spectrum/explore.py`
# MAGIC    - **C · Propose** (runbook + review app) → **approve rules** in the review app or
# MAGIC      `chapter-c-loops/explore.py`
# MAGIC    - **C · Triage** (run *after* you approve) → explore the recommendations
# MAGIC
# MAGIC The widget values are passed to the bundle as `--var` overrides, so you don't have to edit any
# MAGIC `config.yml`. (Editing `config.yml` + the web terminal is still the other supported path.)

# COMMAND ----------
# MAGIC %run ./_deploy_lib

# COMMAND ----------
dbutils.widgets.dropdown("step", "A · Foundation", [
    "A · Foundation",
    "B · Spectrum",
    "C · Propose (runbook + review app)",
    "C · Triage (after you approve)",
], "Step (run in order)")
dbutils.widgets.text("catalog", "", "catalog (admin pre-created)")
dbutils.widgets.text("privileged_group", "", "privileged_group (your primary group)")
dbutils.widgets.text("warehouse_id", "", "warehouse_id (a SQL warehouse you can use)")
dbutils.widgets.text("model_endpoint", "", "model_endpoint (blank = auto-pick Claude)")

# COMMAND ----------
# MAGIC %md
# MAGIC **Fill in the widgets above**, then run the next cell to capture their values.

# COMMAND ----------
STEP = dbutils.widgets.get("step")
CATALOG = dbutils.widgets.get("catalog").strip()
GROUP = dbutils.widgets.get("privileged_group").strip()
WAREHOUSE = dbutils.widgets.get("warehouse_id").strip()
MODEL = dbutils.widgets.get("model_endpoint").strip()

# COMMAND ----------
# Bootstrap the CLI + auth once, locate the chapter folders.
cli = ensure_cli()
env = auth_env()
root = repo_root()
print("repo root:", root)


def deploy_and_run(chapter, deploy_vars, runs):
    """Deploy one chapter's bundle with the given --var overrides, then `bundle run` each key in `runs`.

    `runs` is a list of (resource_key, friendly_note). The same `--var` overrides are passed to BOTH
    `bundle deploy` and `bundle run`: for Apps, `bundle run` re-resolves the app's env block, so an
    unset `${var.warehouse_id}` (the committed config.yml ships blank) would otherwise fail with
    "Must specify environment variable source using either `value` or `valueFrom`."
    """
    cwd = f"{root}/{chapter}"
    var_flags = [f"--var={k}={v}" for k, v in deploy_vars.items() if (v or "").strip()]
    run_cli(cli, ["bundle", "deploy", "-t", "dev", *var_flags], cwd, env)
    for key, note in runs:
        print(f"\n--- {note} ---")
        run_cli(cli, ["bundle", "run", key, "-t", "dev", *var_flags], cwd, env)


# COMMAND ----------
if STEP == "A · Foundation":
    require(catalog=CATALOG, privileged_group=GROUP, warehouse_id=WAREHOUSE)
    # One job (chapter_a_foundation) runs all three tasks: data+governance, UC functions, Genie spaces.
    deploy_and_run(
        "chapter-a-foundation",
        {"catalog": CATALOG, "privileged_group": GROUP, "warehouse_id": WAREHOUSE},
        [("chapter_a_foundation", "Build data + governance + tools + Genie")],
    )
    print("\n✅ Chapter A done. Next: open chapter-a-foundation/explore.py, then come back for step B.")

elif STEP == "B · Spectrum":
    require(catalog=CATALOG, warehouse_id=WAREHOUSE)
    # Start the hosted OBO MCP app, then run the (imperative) agent-deploy job.
    deploy_and_run(
        "chapter-b-spectrum",
        {"catalog": CATALOG, "warehouse_id": WAREHOUSE, "model_endpoint": MODEL},
        [("threatintel_mcp", "Start the hosted OBO MCP app"),
         ("deploy_triage_agent", "Deploy the triage agent to a serving endpoint")],
    )
    print("\n✅ Chapter B done. Next: open chapter-b-spectrum/explore.py (add the MCP in the AI "
          "Playground; query the agent), then come back for step C · Propose.")
    print("   Agent traces: open the '/Users/<you>/aiapps-chapter-b-triage' experiment ▸ Traces.")

elif STEP == "C · Propose (runbook + review app)":
    require(catalog=CATALOG, privileged_group=GROUP, warehouse_id=WAREHOUSE)
    # Synthesize PROPOSED rules and start the review app. Triage is a SEPARATE step — it runs only
    # AFTER a human approves the rules (the workshop's human-in-the-loop gate).
    deploy_and_run(
        "chapter-c-loops",
        {"catalog": CATALOG, "privileged_group": GROUP, "warehouse_id": WAREHOUSE, "model_endpoint": MODEL},
        [("runbook_builder", "Synthesize proposed runbook rules"),
         ("review_console", "Start the OBO Review console app")],
    )
    print("\n✅ Proposed rules written + review app started. NEXT (human gate): approve rules in the "
          "review app (or chapter-c-loops/explore.py). THEN re-run this notebook with step = "
          "'C · Triage (after you approve)'.")

elif STEP == "C · Triage (after you approve)":
    require(catalog=CATALOG, privileged_group=GROUP, warehouse_id=WAREHOUSE)
    # Re-deploy (idempotent — keeps vars consistent) then run triage against APPROVED rules.
    deploy_and_run(
        "chapter-c-loops",
        {"catalog": CATALOG, "privileged_group": GROUP, "warehouse_id": WAREHOUSE, "model_endpoint": MODEL},
        [("triage_runner", "Triage incidents against approved rules")],
    )
    print("\n✅ Triage done. Open chapter-c-loops/explore.py to see recommendations + accuracy, and the "
          "MLflow experiment '/Users/<you>/aiapps-chapter-c-triage' for traces.")

else:
    raise ValueError(f"Unknown step: {STEP}")
