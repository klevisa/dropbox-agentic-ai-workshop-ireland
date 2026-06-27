# Databricks notebook source
# MAGIC %md
# MAGIC # Chapter C · 01 — Runbook synthesis (two-stage LLM)
# MAGIC Turns the historical `{prefix}_ti_intel.investigations` (whose `detailed_notes` read
# MAGIC symptoms → steps → containment → outcome) into a small, reviewable RUNBOOK. Two stages, both via
# MAGIC `ai_query` on serverless (no warehouse, no mlflow):
# MAGIC
# MAGIC - **Stage 1 — extract (map, row-parallel)** → `incident_features`: one structured record per
# MAGIC   investigation `{symptoms[], investigative_steps[], containment_actions[], outcome}`.
# MAGIC - **Stage 2 — synthesize (reduce, one call)** → `runbook_rules`: generalize the distinct
# MAGIC   symptom→action patterns into ~6–10 rules. Each rule's `action_plan` is a JSON array of DSL
# MAGIC   steps, validated against a fixed action list before it's written, all `status='proposed'`.
# MAGIC
# MAGIC The only non-determinism is here (LLM synthesis) plus the human approval gate. At runtime the
# MAGIC triage agent (notebook 02) just looks up an APPROVED rule and runs its plan against the Chapter A
# MAGIC UC functions.

# COMMAND ----------
# MAGIC %run ./common

# COMMAND ----------
import json
from datetime import datetime

from databricks.sdk import WorkspaceClient

dbutils.widgets.text("catalog", "klevis_demo_catalog")
dbutils.widgets.text("model_endpoint", "")   # blank = auto-pick a Claude FM endpoint

ctx = workshop_context(spark, catalog=dbutils.widgets.get("catalog"))
LLM = get_llm_endpoint(WorkspaceClient(), override=dbutils.widgets.get("model_endpoint").strip())
print(ctx)
print(f"model={LLM}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## The DSL contract
# MAGIC A rule's `action_plan` is a list of steps. Each step names one **action** (a Chapter A UC
# MAGIC function, or the terminal `recommend_action`) and its **args**. Args may reference fields of the
# MAGIC incident being triaged via `$incident.<field>`. We validate every plan against this contract and
# MAGIC repair the common mistakes, so only well-formed plans get written.

# COMMAND ----------
# The DSL contract — ACTIONS, INCIDENT_REFS, ACTION_PLAYS, validate_plan(), repair_plan() — lives in
# ./common (imported above via %run) so it's shared with 02_triage_runner AND unit-testable. See common.

# COMMAND ----------
# MAGIC %md
# MAGIC ## Stage 1 — extract structured features per investigation (row-parallel `ai_query`)
# MAGIC One `ai_query` call per investigation, run in parallel by the engine. `responseFormat` forces the
# MAGIC model to return JSON matching our schema, which we then explode into typed columns.

# COMMAND ----------
EXTRACT_PROMPT = (
    "You are a SOC analyst extracting structured features from a security investigation record. "
    "From the SUMMARY and NOTES below, extract exactly four fields. "
    "(1) symptoms — observed behavioral signals as SHORT snake_case tokens from this vocabulary ONLY "
    "(omit any not present): credential_stuffing, impossible_travel, data_staging, mass_file_download, "
    "new_device_burst, mfa_fatigue, off_hours_admin, geo_velocity, anomalous_share_external, "
    "suspicious_oauth_grant, api_token_abuse, bulk_account_creation, malware_callback. Do NOT include risk-score/band. "
    "(2) investigative_steps — snake_case tokens from: get_account_risk, blast_radius, enrich_indicator, "
    "pivot_indicator (only those actually performed). "
    "(3) containment_actions — tokens from this vocabulary ONLY: forced_password_reset, session_revoked, "
    "mfa_enforced, account_suspended, external_sharing_disabled, manual_review, rate_limited, "
    "cleared_no_action. (4) outcome — the one-line resolution verbatim. "
    "Return ONLY the tokens; no prose. Investigation:\n")

# responseFormat for Stage 1: force a JSON object with our four fields.
EXTRACT_RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {"name": "incident_features", "strict": True,
    "schema": {"type": "object", "required": ["symptoms", "investigative_steps", "containment_actions", "outcome"],
        "properties": {"symptoms": {"type": "array", "items": {"type": "string"}},
            "investigative_steps": {"type": "array", "items": {"type": "string"}},
            "containment_actions": {"type": "array", "items": {"type": "string"}},
            "outcome": {"type": "string"}}}}}
# The Spark type we parse each model response back into.
FEATURE_TYPE = ("struct<symptoms:array<string>, investigative_steps:array<string>, "
                "containment_actions:array<string>, outcome:string>")


def extract_features():
    """Run Stage 1 and write {prefix}_ti_intel.incident_features (one row per investigation)."""
    # to_sql_string() (from ./common) safely embeds the prompt + JSON schema into the SQL statement.
    prompt_sql = to_sql_string(EXTRACT_PROMPT)
    response_format_sql = to_sql_string(json.dumps(EXTRACT_RESPONSE_FORMAT))
    spark.sql(f"""
    CREATE OR REPLACE TABLE {ctx.catalog}.{ctx.intel}.incident_features AS
    WITH extracted AS (
      SELECT investigation_id, related_account_id, title, severity,
        ai_query('{LLM}',
          concat({prompt_sql}, 'SUMMARY: ', coalesce(summary,''), '\\nNOTES: ', coalesce(detailed_notes,'')),
          responseFormat => {response_format_sql}) AS raw_extract
      FROM {ctx.catalog}.{ctx.intel}.investigations)
    SELECT investigation_id, related_account_id, title, severity,
      f.symptoms, f.investigative_steps, f.containment_actions, f.outcome, raw_extract
    FROM extracted
    LATERAL VIEW EXPLODE(array(from_json(raw_extract, '{FEATURE_TYPE}'))) t AS f
    """)
    count = spark.table(f"{ctx.catalog}.{ctx.intel}.incident_features").count()
    print(f"incident_features rows: {count}")


extract_features()

# COMMAND ----------
# MAGIC %md
# MAGIC ## Stage 2 — synthesize the runbook (reduce; one `ai_query` call)
# MAGIC Roll the per-investigation features up into distinct `symptom → containment` patterns (with how
# MAGIC often each occurred), then ask the model once to generalize them into a small set of rules.

# COMMAND ----------
def collect_patterns():
    """Group the extracted features into distinct symptom→containment patterns with support counts."""
    rows = spark.sql(f"""
    WITH normalized AS (
      SELECT array_sort(array_distinct(symptoms)) AS symptoms,
             array_sort(array_distinct(containment_actions)) AS containment, outcome
      FROM {ctx.catalog}.{ctx.intel}.incident_features)
    SELECT concat_ws(',', symptoms) AS symptom_signature,
           concat_ws(',', containment) AS containment_signature,
           count(*) AS support, collect_set(outcome) AS outcomes
    FROM normalized GROUP BY 1, 2 ORDER BY support DESC""").collect()
    patterns = [{"symptoms": r["symptom_signature"], "containment_actions": r["containment_signature"],
                 "support": r["support"], "outcomes": list(r["outcomes"])} for r in rows]
    print(f"distinct symptom->containment patterns: {len(patterns)}")
    return patterns


evidence = collect_patterns()

# COMMAND ----------
SYNTH_SYSTEM = ("You are a senior detection engineer building an incident-response RUNBOOK from "
    "historical investigation evidence. Generalize many investigations into a SMALL set of rules "
    "(roughly 6-10), one per coherent threat scenario. Deduplicate aggressively. Output ONLY valid "
    "JSON, no markdown.")
SYNTH_INSTRUCTIONS = """
Each rule is an object with EXACTLY these fields:
  "rule_id": "RB-001","RB-002",... (zero-padded, unique)   "name": short title
  "scenario_hint": one of [account_takeover, malware_delivery, data_exfiltration, credential_stuffing,
                   phishing_wave, insider_activity, api_token_abuse, benign]
  "symptom_pattern": human-readable trigger symptoms
  "action_plan": JSON array of DSL steps (grammar below)
  "rationale": why this plan fits   "evidence_count": integer   "confidence": float 0-1

DSL step: {"action":"<ACTION>","args":{...},"when":"<optional>"}. ALLOWED ACTIONS + EXACT args:
  enrich_indicator    args:{"indicator":"$incident.indicator_value"}
  pivot_indicator     args:{"indicator":"$incident.indicator_value"}
  blast_radius        args:{"indicator":"$incident.indicator_value"}
  get_account_risk    args:{"account_id":"$incident.account_id"}
  get_account_actions args:{"account_id":"$incident.account_id"}
  recommend_action    args:{"play":"<PLAY>"}   (optionally also "rationale":"<one line why>")
recommend_action carries ONLY the decision — the incident, account, and rule it applies to are known
to the runtime, so do NOT repeat them in its args.
The ONLY $incident refs (for the tool actions above): $incident.indicator_value, $incident.account_id,
  $incident.indicator_type, $incident.incident_id (you may also reference "$steps.<action>.<field>").
  indicator-centric actions take "indicator"; account-centric take "account_id".
PLAY in account_suspended, rate_limited, forced_password_reset, mfa_enforced, manual_review,
  external_sharing_disabled, session_revoked, cleared_no_action.
Produce ONE rule per distinct scenario; mirror this logic EXACTLY (note: NO search/knowledge step):
  malware_delivery   -> enrich_indicator -> pivot_indicator -> blast_radius -> recommend_action(account_suspended)
  account_takeover   -> get_account_risk -> blast_radius -> recommend_action(forced_password_reset)
  data_exfiltration  -> get_account_risk -> blast_radius -> recommend_action(external_sharing_disabled)
  credential_stuffing-> get_account_risk -> recommend_action(rate_limited)
  phishing_wave      -> get_account_risk -> recommend_action(session_revoked)
  insider_activity   -> get_account_risk -> blast_radius -> recommend_action(manual_review)
  api_token_abuse    -> get_account_risk -> recommend_action(rate_limited)
  benign             -> get_account_risk -> recommend_action(cleared_no_action)
Every plan must END with exactly one recommend_action step.
Return a JSON object: {"rules":[ ...rule objects... ]}.

EVIDENCE (distinct symptom -> containment patterns, with support counts):
"""

# responseFormat for Stage 2: force a JSON object with a "rules" array.
RULES_RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {"name": "runbook", "strict": True,
    "schema": {"type": "object", "required": ["rules"], "properties": {"rules": {"type": "array", "items": {
        "type": "object", "required": ["rule_id", "name", "scenario_hint", "symptom_pattern",
            "action_plan", "rationale", "evidence_count", "confidence"],
        "properties": {"rule_id": {"type": "string"}, "name": {"type": "string"},
            "scenario_hint": {"type": "string"}, "symptom_pattern": {"type": "string"},
            "action_plan": {"type": "array", "items": {"type": "object", "required": ["action", "args"],
                "properties": {"action": {"type": "string"}, "args": {"type": "object"},
                    "when": {"type": "string"}}}},
            "rationale": {"type": "string"}, "evidence_count": {"type": "integer"},
            "confidence": {"type": "number"}}}}}}}}


def synthesize_rules(evidence):
    """Run Stage 2: one ai_query call that generalizes the evidence into runbook rules (raw JSON text)."""
    full_prompt = SYNTH_SYSTEM + "\n\n" + SYNTH_INSTRUCTIONS + json.dumps(evidence, indent=2)
    prompt_sql = to_sql_string(full_prompt)
    response_format_sql = to_sql_string(json.dumps(RULES_RESPONSE_FORMAT))
    return spark.sql(
        f"SELECT ai_query('{LLM}', {prompt_sql}, responseFormat => {response_format_sql}) AS rules"
    ).collect()[0]["rules"]


raw_rules = synthesize_rules(evidence)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Validate, then write the proposed rules
# MAGIC Repair each plan, drop any that still don't validate, and write the survivors as `proposed`.

# COMMAND ----------
def validate_rules(raw_rules):
    """Parse the model's JSON, repair + validate each plan, and return the rules worth writing."""
    parsed = extract_json(raw_rules)
    rules_in = parsed["rules"] if isinstance(parsed, dict) else parsed
    good, dropped = [], []
    for idx, rule in enumerate(rules_in):
        rule_id = rule.get("rule_id") or f"RB-{idx + 1:03d}"
        plan = repair_plan(rule.get("action_plan"))
        problems = validate_plan(plan)
        if problems:
            dropped.append((rule_id, problems)); continue
        good.append({"rule_id": rule_id, "name": rule.get("name") or rule_id,
            "scenario_hint": rule.get("scenario_hint") or "",
            "symptom_pattern": rule.get("symptom_pattern") or "", "action_plan": json.dumps(plan),
            "rationale": rule.get("rationale") or "", "evidence_count": int(rule.get("evidence_count") or 0),
            "confidence": float(rule.get("confidence") or 0.0)})
    print(f"validated rules: {len(good)}   dropped: {len(dropped)}")
    for rule_id, problems in dropped:
        print("  DROPPED", rule_id, problems)
    assert good, "no rules survived validation — inspect the raw LLM output"
    return good


good_rules = validate_rules(raw_rules)

# COMMAND ----------
from pyspark.sql.types import (StructType, StructField, StringType, IntegerType, DoubleType, TimestampType)

RUNBOOK_SCHEMA = StructType([
    StructField("rule_id", StringType()), StructField("name", StringType()),
    StructField("scenario_hint", StringType()), StructField("symptom_pattern", StringType()),
    StructField("action_plan", StringType()), StructField("rationale", StringType()),
    StructField("evidence_count", IntegerType()), StructField("confidence", DoubleType()),
    StructField("status", StringType()), StructField("created_by", StringType()),
    StructField("created_at", TimestampType()), StructField("reviewed_by", StringType()),
    StructField("reviewed_at", TimestampType())])


def write_proposed_rules(good_rules):
    """Create runbook_rules if needed, then REPLACE the whole runbook with this fresh proposed set.

    A synthesis run regenerates the runbook, so we clear ALL prior rules — proposed AND approved — not
    just the proposed ones. (Clearing only 'proposed' would let each re-run pile its RB-001… on top of a
    prior APPROVED set, producing duplicate rule_ids with conflicting plays that corrupt triage matching.)
    Re-running therefore refreshes the proposals and you re-approve, as the README describes.
    """
    created_at = datetime.now()   # real timestamp — provenance per synthesis run
    rows = [(g["rule_id"], g["name"], g["scenario_hint"], g["symptom_pattern"], g["action_plan"],
             g["rationale"], g["evidence_count"], g["confidence"], "proposed", "runbook-builder",
             created_at, None, None) for g in good_rules]
    spark.sql(f"""CREATE TABLE IF NOT EXISTS {ctx.catalog}.{ctx.intel}.runbook_rules (
        rule_id STRING, name STRING, scenario_hint STRING, symptom_pattern STRING, action_plan STRING,
        rationale STRING, evidence_count INT, confidence DOUBLE, status STRING, created_by STRING,
        created_at TIMESTAMP, reviewed_by STRING, reviewed_at TIMESTAMP)
      COMMENT 'Synthesized runbook. status proposed->approved|rejected (human gate). action_plan is a validated DSL step array.'""")
    spark.sql(f"DELETE FROM {ctx.catalog}.{ctx.intel}.runbook_rules")   # full replace — see docstring
    spark.createDataFrame(rows, RUNBOOK_SCHEMA).write.mode("append").saveAsTable(
        f"{ctx.catalog}.{ctx.intel}.runbook_rules")
    print(f"runbook_rules written: {len(rows)} proposed (table replaced)")


write_proposed_rules(good_rules)
display(spark.sql(f"""SELECT rule_id, name, scenario_hint, evidence_count, confidence, status
    FROM {ctx.catalog}.{ctx.intel}.runbook_rules ORDER BY rule_id"""))
dbutils.notebook.exit(
    f"{len(good_rules)} proposed rules in {ctx.catalog}.{ctx.intel}.runbook_rules — approve, then run triage.")
