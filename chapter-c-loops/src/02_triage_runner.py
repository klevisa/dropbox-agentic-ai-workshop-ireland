# Databricks notebook source
# MAGIC %md
# MAGIC # Chapter C · 02 — Autonomous triage-runner
# MAGIC Closes the loop after the runbook is synthesized and a human APPROVES rules. On each run:
# MAGIC 1. load the APPROVED runbook rules,
# MAGIC 2. pick up a batch of NEW incidents (oldest first),
# MAGIC 3. **match** — per-incident, row-parallel `ai_query`: map each incident to its best `rule_id` or
# MAGIC    'NONE', reading ONLY the observed narrative (never the hidden `scenario_label`),
# MAGIC 4. for each match, run the rule's DSL `action_plan` by calling the **Chapter A UC functions
# MAGIC    directly** (`{prefix}_ti_tools.*`). The job runs as the participant, so invoker-rights masking
# MAGIC    applies. The terminal `recommend_action` is a PURE decision computed here (no tool call); the
# MAGIC    **orchestrator** then writes ONE auditable row to `{prefix}_ti_risk.triage_recommendations`,
# MAGIC    and the incident flips to `triaged`,
# MAGIC 5. unmatched incidents flip to `uncovered` (they feed the next runbook pass).
# MAGIC
# MAGIC The only runtime LLM judgment is the constrained rule lookup; the chosen plan then runs
# MAGIC deterministically. (Option A: tools called directly as UC functions — no MCP dependency. Chapter
# MAGIC B shows the same tools reachable via the hosted OBO MCP.)
# MAGIC
# MAGIC We also add **MLflow tracing**: **each incident gets one trace** that opens with the `match`
# MAGIC decision (which rule it routed to) and then nests a span per tool call — viewable in the
# MAGIC experiment's **Traces** tab. (The match itself is computed once for the whole batch by a
# MAGIC row-parallel `ai_query`; the per-incident `match` span records that incident's decision, not the
# MAGIC batched LLM latency.) Serverless doesn't ship mlflow, so we install it and restart Python first.

# COMMAND ----------
# MAGIC %pip install -q mlflow

# COMMAND ----------
# MAGIC %restart_python

# COMMAND ----------
# MAGIC %run ./common

# COMMAND ----------
import json
import uuid

import mlflow
from databricks.sdk import WorkspaceClient

dbutils.widgets.text("catalog", "klevis_demo_catalog")
dbutils.widgets.text("model_endpoint", "")
dbutils.widgets.text("limit", "40")

ctx = workshop_context(spark, catalog=dbutils.widgets.get("catalog"))
LLM = get_llm_endpoint(WorkspaceClient(), override=dbutils.widgets.get("model_endpoint").strip())
BATCH_LIMIT = int(dbutils.widgets.get("limit"))

# Send traces to a per-participant experiment; each triaged incident appears under its Traces tab.
EXPERIMENT = f"/Users/{ctx.me}/aiapps-chapter-c-triage"
mlflow.set_experiment(EXPERIMENT)
print(ctx)
print(f"model={LLM}  batch_limit={BATCH_LIMIT}  traces -> {EXPERIMENT}")

# ACTIONS, resolve_ref(), resolve_args() come from ./common (the shared, unit-tested DSL contract).

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. Load the APPROVED rules

# COMMAND ----------
def load_approved_rules():
    rows = spark.sql(f"""SELECT rule_id, scenario_hint, symptom_pattern, action_plan
        FROM {ctx.catalog}.{ctx.intel}.runbook_rules WHERE status='approved' ORDER BY rule_id""").collect()
    rules = {r["rule_id"]: {"rule_id": r["rule_id"], "scenario_hint": r["scenario_hint"],
                            "symptom_pattern": r["symptom_pattern"],
                            "action_plan": json.loads(r["action_plan"]) if r["action_plan"] else []}
             for r in rows}
    assert rules, "no APPROVED rules — run 01_build_runbook, then approve in explore.py before triage."
    print(f"approved rules: {len(rules)} ({', '.join(sorted(rules))})")
    return rules


rules = load_approved_rules()

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. Load a batch of NEW incidents
# MAGIC We deliberately do NOT select `scenario_label` — that's hidden ground truth for the eval in
# MAGIC explore.py. The agent must match on the narrative alone.

# COMMAND ----------
def load_new_incidents(limit):
    rows = spark.sql(f"""SELECT incident_id, narrative, indicator_value, indicator_type, account_id
        FROM {ctx.catalog}.{ctx.intel}.incidents WHERE status='new' ORDER BY created_at LIMIT {limit}""").collect()
    incidents = [{"incident_id": r["incident_id"], "narrative": r["narrative"],
                  "indicator_value": r["indicator_value"], "indicator_type": r["indicator_type"],
                  "account_id": r["account_id"]} for r in rows]
    print(f"new incidents in batch: {len(incidents)}")
    return incidents


incidents = load_new_incidents(BATCH_LIMIT)

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. Match each incident to a rule (row-parallel `ai_query`)
# MAGIC One constrained `ai_query` per incident: given the approved rules and the incident's narrative,
# MAGIC return the single best `rule_id` (or 'NONE'). `responseFormat` keeps the answer to just a rule_id.

# COMMAND ----------
MATCH_INSTRUCTIONS = (
    "You are a SOC triage router. Below is a small APPROVED runbook: each rule is one line "
    "`rule_id (scenario_hint): symptom_pattern`. Given ONE incident's free-text narrative (appended), "
    "choose the single rule whose symptom_pattern best matches the OBSERVED symptoms in the narrative. "
    "Match ONLY on observed symptoms. If none is a reasonable match, return 'NONE'. Be decisive.\n\n"
    "APPROVED RUNBOOK RULES:\n")
MATCH_RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {"name": "incident_rule_match", "strict": True,
    "schema": {"type": "object", "required": ["rule_id"], "properties": {"rule_id": {"type": "string"}}}}}


def match_incidents_to_rules(rules, limit):
    """Return {incident_id: rule_id-or-'NONE'} for the batch, via a row-parallel ai_query."""
    rules_blob = "\n".join(f"{r['rule_id']} ({r['scenario_hint']}): {r['symptom_pattern']}"
                           for r in rules.values())
    match_prompt = (MATCH_INSTRUCTIONS + rules_blob +
                    "\n\nReturn the best rule_id (one above, or 'NONE'). INCIDENT NARRATIVE:\n")
    prompt_sql = to_sql_string(match_prompt)
    response_format_sql = to_sql_string(json.dumps(MATCH_RESPONSE_FORMAT))
    rows = spark.sql(f"""
        SELECT incident_id,
               ai_query('{LLM}', concat({prompt_sql}, narrative), responseFormat => {response_format_sql}) AS match
        FROM {ctx.catalog}.{ctx.intel}.incidents WHERE status='new' ORDER BY created_at LIMIT {limit}""").collect()
    matches = {}
    for row in rows:
        try:
            rule_id = extract_json(row["match"]).get("rule_id")
        except Exception:
            rule_id = None
        matches[row["incident_id"]] = rule_id if rule_id in rules else "NONE"
    matched = sum(1 for v in matches.values() if v != "NONE")
    print(f"matched {matched}/{len(rows)}; {len(rows) - matched} NONE")
    return matches


matches = match_incidents_to_rules(rules, BATCH_LIMIT)

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4. Trace + run each incident
# MAGIC `triage_one` gives every incident **one trace**: a `match` span (the routing decision) followed by
# MAGIC the tool spans for matched incidents. For a match we walk the rule's plan — each non-terminal step
# MAGIC calls a Chapter A UC function directly; `recommend_action` is computed here (no write); then the
# MAGIC orchestrator writes one recommendation row. Unmatched incidents get a match-only trace and flip to
# MAGIC `uncovered`.

# COMMAND ----------
@mlflow.trace(span_type="TOOL")     # each tool call becomes a child span (inputs=args, outputs=rows)
def run_action(action, args):
    """Run one DSL action. recommend_action is a PURE decision (just the play + optional rationale);
    the rest call the UC functions directly."""
    if action == "recommend_action":
        return {"recommended_play": args.get("play"), "rationale": args.get("rationale", "")}
    arg_value = (args.get("indicator") if action in ("enrich_indicator", "pivot_indicator", "blast_radius")
                 else args.get("account_id"))
    rows = spark.sql(f"SELECT * FROM {ctx.catalog}.{ctx.tools}.{action}({to_sql_string(str(arg_value))})").collect()
    return [r.asDict() for r in rows]


def write_recommendation(decision, incident_id, account_id, indicator_value, matched_rule_id, evidence):
    """The single write: one auditable recommendation row per triaged incident. The incident, account,
    and rule ids are supplied by the orchestrator (it knows them); the decision supplies only play + why."""
    recommendation_id = uuid.uuid4().hex
    evidence_json = json.dumps(evidence, default=str)
    spark.sql(f"""INSERT INTO {ctx.catalog}.{ctx.risk}.triage_recommendations
        SELECT {to_sql_string(recommendation_id)}, {to_sql_string(incident_id)},
               {to_sql_string(account_id)}, {to_sql_string(indicator_value)},
               {to_sql_string(matched_rule_id)}, {to_sql_string(decision.get('recommended_play'))},
               {to_sql_string(decision.get('rationale', ''))}, {to_sql_string(evidence_json)},
               current_timestamp(), current_user()""")


def set_incident_status(incident_id, status):
    spark.sql(f"UPDATE {ctx.catalog}.{ctx.intel}.incidents SET status='{status}' "
              f"WHERE incident_id={to_sql_string(incident_id)}")


def _record_match_span(incident, matched_rule_id, rules):
    """Open a 'match' span recording THIS incident's routing decision, so each incident's trace starts
    with match → (then the tool calls). The actual LLM inference ran earlier in ONE batched, row-parallel
    ai_query over the whole batch, so we record the decision here (input narrative -> chosen rule) rather
    than the per-row latency/tokens, which aren't observable from Python once the batch has run."""
    hint = rules[matched_rule_id]["scenario_hint"] if matched_rule_id in rules else None
    with mlflow.start_span(name="match", span_type="CHAIN") as span:
        span.set_inputs({"incident_id": incident["incident_id"], "narrative": incident["narrative"]})
        span.set_attribute("inference", "batched ai_query (row-parallel) — decision recorded here")
        span.set_outputs({"matched_rule_id": matched_rule_id, "scenario_hint": hint})


@mlflow.trace(span_type="CHAIN")    # ONE trace per incident: match span first, then the tool spans
def triage_one(incident, matched_rule_id, rules):
    """Trace one incident end to end: record the match decision, then (if matched) run the rule's plan —
    each run_action call nests as a child span. Returns (status, play, evidence_keys) for the summary."""
    mlflow.update_current_trace(tags={"incident_id": incident["incident_id"],
                                      "matched_rule_id": matched_rule_id})
    _record_match_span(incident, matched_rule_id, rules)

    if matched_rule_id == "NONE" or matched_rule_id not in rules:
        set_incident_status(incident["incident_id"], "uncovered")
        return ("uncovered", None, [])

    rule = rules[matched_rule_id]
    context = {"incident": incident, "steps": {}}
    evidence, decision = {}, None
    for step in rule["action_plan"]:
        action = step.get("action")
        if action not in ACTIONS:
            continue
        output = run_action(action, resolve_args(step.get("args"), context))
        context["steps"][action] = output
        if action == "recommend_action":
            decision = output          # the decision, not evidence
        else:
            evidence[action] = output  # every other step is a tool call -> its output is evidence
    if decision and decision.get("recommended_play"):
        write_recommendation(decision, incident["incident_id"], incident["account_id"],
                             incident["indicator_value"], rule["rule_id"], evidence)
    set_incident_status(incident["incident_id"], "triaged")
    return ("triaged", decision.get("recommended_play") if decision else None, list(evidence.keys()))


def run_triage(incidents, matches, rules):
    matched, uncovered, samples = 0, 0, []
    for incident in incidents:
        rule_id = matches.get(incident["incident_id"], "NONE")
        status, play, evidence_keys = triage_one(incident, rule_id, rules)
        if status == "triaged":
            matched += 1
            if len(samples) < 5:
                samples.append((incident["incident_id"], rule_id, rules[rule_id]["scenario_hint"],
                                play, evidence_keys))
        else:
            uncovered += 1
    print("\n===== TRIAGE RUN SUMMARY =====")
    print(f"  triaged: {matched}   uncovered: {uncovered}")
    for incident_id, rule_id, scenario, play, evidence_keys in samples:
        print(f"    {incident_id}  rule={rule_id} ({scenario})  play={play}  evidence={evidence_keys}")
    return matched, uncovered


matched, uncovered = run_triage(incidents, matches, rules)

# COMMAND ----------
display(spark.sql(f"""SELECT incident_id, account_id, matched_rule_id, recommended_play, recommended_at
    FROM {ctx.catalog}.{ctx.risk}.triage_recommendations ORDER BY recommended_at DESC LIMIT 20"""))
print(f"traces ({matched}) -> open experiment {EXPERIMENT} and its Traces tab "
      f"(filter by tag incident_id, e.g. INC-00187)")
dbutils.notebook.exit(f"triaged={matched} uncovered={uncovered} -> {ctx.catalog}.{ctx.risk}.triage_recommendations")
