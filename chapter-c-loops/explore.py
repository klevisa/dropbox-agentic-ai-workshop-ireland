# Databricks notebook source
# MAGIC %md
# MAGIC # Chapter C — Explore (the two-agent loop + the human gate)
# MAGIC The arc: **synthesize a runbook → a human approves → an autonomous agent triages**.
# MAGIC
# MAGIC 1. `databricks bundle run runbook_builder -t dev`  → proposes rules (run this first, in the terminal)
# MAGIC 2. **this notebook**: review the proposed rules, then **approve** them (the governance gate)
# MAGIC 3. `databricks bundle run triage_runner -t dev`  → triages NEW incidents against APPROVED rules
# MAGIC 4. **this notebook**: review recommendations + accuracy vs. the hidden `scenario_label`

# COMMAND ----------
# MAGIC %run ./src/common

# COMMAND ----------
CATALOG = ""                           # <- set your catalog
ctx = workshop_context(spark, catalog=CATALOG)
print(ctx)

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. Review the PROPOSED runbook (after running `runbook_builder`)
# MAGIC The synthesis job generalized the historical investigations into a small rule set. Each rule's
# MAGIC `action_plan` is a validated sequence of tool calls ending in a `recommend_action`.

# COMMAND ----------
display(spark.sql(f"""SELECT rule_id, scenario_hint, evidence_count, confidence, status, symptom_pattern, action_plan
    FROM {ctx.catalog}.{ctx.intel}.runbook_rules ORDER BY rule_id"""))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. The human approval gate
# MAGIC The autonomous triage agent runs **only APPROVED rules**. Review the rules above, then approve.
# MAGIC This cell approves the whole proposed set at once. (For a per-rule, in-browser version of this
# MAGIC gate — Approve/Reject buttons, OBO — deploy the **`review_console`** app instead; see the README.)
# MAGIC This is the governance beat — nothing acts until a human signs off.

# COMMAND ----------
def approve_proposed_rules():
    spark.sql(f"""UPDATE {ctx.catalog}.{ctx.intel}.runbook_rules
        SET status='approved', reviewed_by=current_user(), reviewed_at=current_timestamp()
        WHERE status='proposed'""")
    display(spark.sql(f"""SELECT rule_id, scenario_hint, status, reviewed_by
        FROM {ctx.catalog}.{ctx.intel}.runbook_rules ORDER BY rule_id"""))


approve_proposed_rules()
# Now run:  databricks bundle run triage_runner -t dev

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. Review the recommendations (after running `triage_runner`)
# MAGIC One auditable row per triaged incident: the matched rule, the recommended play, and the gathered
# MAGIC evidence (enrich / pivot / blast_radius / risk).
# MAGIC
# MAGIC > **Governance teaching point.** This `evidence` is a **materialized snapshot** captured when the
# MAGIC > triage job ran **as you (a privileged caller)**, so it holds *unmasked* values — and a column mask
# MAGIC > on the source tables does **not** retroactively follow a copy. Contrast it with the agent and the
# MAGIC > review app's *live* account panel, which query the source **per caller (OBO)** and so redact for
# MAGIC > non-privileged users. The lesson: masking is per-caller on the **source**; once a privileged job
# MAGIC > **persists** its output you've created a new dataset that must be governed (and granted) on its own.

# COMMAND ----------
display(spark.sql(f"""SELECT incident_id, account_id, matched_rule_id, recommended_play, recommended_at, evidence
    FROM {ctx.catalog}.{ctx.risk}.triage_recommendations ORDER BY recommended_at DESC LIMIT 25"""))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4. Accuracy vs. the hidden ground truth
# MAGIC Each incident carries a hidden `scenario_label` the agent never sees (it matches on the narrative
# MAGIC only). Each scenario has an expected containment play — let's see how often the autonomous loop
# MAGIC landed on it.

# COMMAND ----------
display(spark.sql(f"""
WITH expected AS (
  SELECT * FROM VALUES
    ('account_takeover','forced_password_reset'), ('malware_delivery','account_suspended'),
    ('data_exfiltration','external_sharing_disabled'), ('credential_stuffing','rate_limited'),
    ('phishing_wave','session_revoked'), ('insider_activity','manual_review'),
    ('api_token_abuse','rate_limited'), ('benign','cleared_no_action')
  AS t(scenario_label, expected_play))
SELECT inc.scenario_label,
       count(*) AS triaged,
       sum(CASE WHEN tr.recommended_play = e.expected_play THEN 1 ELSE 0 END) AS correct,
       round(100.0 * sum(CASE WHEN tr.recommended_play = e.expected_play THEN 1 ELSE 0 END)/count(*), 1) AS pct
FROM {ctx.catalog}.{ctx.risk}.triage_recommendations tr
JOIN {ctx.catalog}.{ctx.intel}.incidents inc ON inc.incident_id = tr.incident_id
LEFT JOIN expected e ON e.scenario_label = inc.scenario_label
GROUP BY inc.scenario_label ORDER BY triaged DESC"""))

# COMMAND ----------
# MAGIC %md
# MAGIC That's the loop: an LLM **wrote** the runbook from history, a human **approved** it, and an
# MAGIC autonomous agent **applied** it to new incidents — matching the hidden ground truth without ever
# MAGIC seeing it. Re-run `runbook_builder` later to fold `uncovered` incidents into a fresh proposed set.
