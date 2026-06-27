# Databricks notebook source
# MAGIC %md
# MAGIC # Chapter A · 03 — Genie Spaces
# MAGIC Creates (or updates, by title) the participant's two governed Genie spaces:
# MAGIC * **Open (Summary)** — bound only to the `{prefix}_ti_cs.*` views (no PII, bands not scores).
# MAGIC * **Privileged (Detail)** — bound to the full `{prefix}_ti_intel.*` / `{prefix}_ti_risk.*` base tables.
# MAGIC
# MAGIC A Genie space only sees the tables you bind to it, so the *binding* is the main control — the open
# MAGIC space physically cannot reach PII because those columns aren't in its views. The column masks from
# MAGIC notebook 01 still apply per caller even in the privileged space.
# MAGIC
# MAGIC **We keep prose to a minimum and prefer structured metadata.** Genie infers columns and obvious
# MAGIC filters from the schema, so we add:
# MAGIC * **join specs** — typed table relationships (we don't declare foreign keys), so Genie joins
# MAGIC   correctly without being *told* the keys in prose;
# MAGIC * **SQL snippets** — reusable named `expressions` / `measures` / `filters` (e.g. "MITRE technique
# MAGIC   count", "severe active campaigns", "active actors");
# MAGIC * exactly **one text instruction** — the business rule Genie genuinely can't infer: that
# MAGIC   `account_risk_scores` is history, so an account's "current" risk is the latest `score_date` row.

# COMMAND ----------
# MAGIC %run ./common

# COMMAND ----------
import json
from databricks.sdk import WorkspaceClient

dbutils.widgets.text("catalog", "klevis_demo_catalog")
dbutils.widgets.text("user_prefix", "")
dbutils.widgets.text("warehouse_id", "")

WAREHOUSE_ID = dbutils.widgets.get("warehouse_id").strip()
if not WAREHOUSE_ID:
    raise ValueError("warehouse_id is REQUIRED — set var.warehouse_id in config.yml to a SQL warehouse.")

ctx = workshop_context(spark, catalog=dbutils.widgets.get("catalog"),
                       prefix_override=dbutils.widgets.get("user_prefix").strip())
w = WorkspaceClient()
PARENT_PATH = f"/Workspace/Users/{ctx.me}"
print(ctx)
print(f"warehouse={WAREHOUSE_ID}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Helpers to build and upsert a Genie space
# MAGIC A Genie space is created from a `serialized_space` JSON. Beyond the bound tables and a text
# MAGIC instruction, we add **join specs** (typed relationships) and **SQL snippets** (named
# MAGIC expressions/measures/filters). `make_join` and `make_snippet` build those entries; `serialize_space`
# MAGIC stamps each with a unique id and assembles the JSON. `upsert_space` creates or patches the space.

# COMMAND ----------
def _opaque_id(n):
    """Genie wants a unique, sortable, 32-hex id per instruction/join/snippet. A zero-padded counter
    works fine and keeps the order stable across re-runs."""
    return "%032x" % n


def _backtick(*parts):
    """Quote each identifier part and join with dots: _backtick(cat, schema, 'campaigns', 'severity')
    -> `cat`.`schema`.`campaigns`.`severity`. Snippet SQL uses fully-qualified, quoted column refs."""
    return ".".join(f"`{p}`" for p in parts)


def make_join(left_fqn, right_fqn, left_col, right_col, relationship="MANY_TO_ONE"):
    """A typed join relationship (shows in Genie's Joins panel — NOT a prose instruction). Each table
    is aliased by its bare name; the ON condition uses those aliases, plus a cardinality marker."""
    left_alias, right_alias = left_fqn.split(".")[-1], right_fqn.split(".")[-1]
    return {"left": {"identifier": left_fqn, "alias": left_alias},
            "right": {"identifier": right_fqn, "alias": right_alias},
            "sql": [f"`{left_alias}`.`{left_col}` = `{right_alias}`.`{right_col}`",
                    f"--rt=FROM_RELATIONSHIP_TYPE_{relationship}--"]}


def make_snippet(sql, display_name, instruction, synonyms):
    """A reusable named SQL snippet (an expression, measure, or filter). `sql` is a single fragment;
    `synonyms` are alternate phrasings that should trigger it."""
    return {"sql": [sql], "display_name": display_name,
            "instruction": [instruction], "synonyms": synonyms}


def serialize_space(tables, instructions="", examples=(), joins=(),
                    expressions=(), measures=(), filters=()):
    """Build a valid serialized_space (schema version 2) and stamp every item with a unique id.
      tables       : fully-qualified table/view names to bind
      instructions : a single text instruction (or "" for none)
      examples     : (question, sql) pairs
      joins        : make_join(...) entries (typed relationships)
      expressions/measures/filters : make_snippet(...) entries (reusable SQL snippets)
    """
    counter = {"n": 0}

    def next_id():
        counter["n"] += 1
        return _opaque_id(counter["n"])

    instructions_block = {
        "text_instructions": [{"id": next_id(), "content": [instructions]}] if instructions else [],
        "example_question_sqls": [{"id": next_id(), "question": [q], "sql": sql.splitlines(keepends=True)}
                                  for q, sql in examples],
    }
    if joins:
        instructions_block["join_specs"] = [{"id": next_id(), **j} for j in joins]
    snippet_buckets = {name: [{"id": next_id(), **s} for s in items]
                       for name, items in (("expressions", expressions), ("measures", measures),
                                           ("filters", filters)) if items}
    if snippet_buckets:
        instructions_block["sql_snippets"] = snippet_buckets
    return json.dumps({
        "version": 2,
        "data_sources": {"tables": [{"identifier": t} for t in sorted(tables)]},
        "instructions": instructions_block,
    })


def upsert_space(title, description, serialized_space):
    """Create the space, or patch it in place if one with this title already exists."""
    body = {"title": title, "description": description, "parent_path": PARENT_PATH,
            "warehouse_id": WAREHOUSE_ID, "serialized_space": serialized_space}
    existing = w.api_client.do("GET", "/api/2.0/genie/spaces").get("spaces", [])
    match = next((s for s in existing if s.get("title") == title), None)
    if match:
        space_id = match["space_id"]
        w.api_client.do("PATCH", f"/api/2.0/genie/spaces/{space_id}", body=body)
        print(f"  patched  {title} -> {space_id}")
        return space_id
    resp = w.api_client.do("POST", "/api/2.0/genie/spaces", body=body)
    space_id = resp.get("space_id") or resp.get("id")
    print(f"  created  {title} -> {space_id}")
    return space_id

# COMMAND ----------
# MAGIC %md
# MAGIC ## Open (Summary) space — CS-safe views only
# MAGIC The views carry no PII and no numeric scores, so no guardrail instructions are needed — the
# MAGIC binding enforces it. The one non-obvious fact: accounts appear as a masked `account_label`.

# COMMAND ----------
open_instructions = (
    "Accounts appear only as a masked account_label like 'Customer-001024' — use it as the account "
    "identifier; there is no separate account id or customer name in this space."
)
open_examples = [
    ("Why was a customer's account actioned?",
     f"SELECT account_label, action_type, reason_summary, risk_band, taken_at\n"
     f"FROM {ctx.catalog}.{ctx.cs}.account_action_explanations\n"
     f"WHERE account_label = 'Customer-001024'\nORDER BY taken_at DESC"),
    ("What are the most common account actions this period?",
     f"SELECT action_type, count(*) AS actions\n"
     f"FROM {ctx.catalog}.{ctx.cs}.account_action_explanations\nGROUP BY action_type ORDER BY actions DESC"),
    ("Which threat campaigns are currently active and severe?",
     f"SELECT campaign_name, actor_name, target_sector, severity, status\n"
     f"FROM {ctx.catalog}.{ctx.cs}.threat_summary\n"
     f"WHERE status = 'active' AND severity IN ('high','critical')\nORDER BY severity DESC"),
    ("Summarize recent investigations and their outcomes",
     f"SELECT title, severity, status, summary, opened_at\n"
     f"FROM {ctx.catalog}.{ctx.cs}.investigation_summaries\nORDER BY opened_at DESC LIMIT 20"),
]
open_tables = [f"{ctx.catalog}.{ctx.cs}.threat_summary",
               f"{ctx.catalog}.{ctx.cs}.investigation_summaries",
               f"{ctx.catalog}.{ctx.cs}.account_action_explanations"]
open_space_id = upsert_space(
    f"{ctx.prefix} · Threat Intel — Open (Summary)",
    "Summary-only threat intel. Governed CS-safe views: no PII, no numeric scores, no raw signals or sources/methods.",
    serialize_space(open_tables, open_instructions, open_examples))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Privileged (Detail) space — full base tables
# MAGIC The relationships go in as **join specs** and the reusable calculations as **SQL snippets**, so
# MAGIC the only prose left is the one rule Genie can't infer: risk scores are stored as history.

# COMMAND ----------
# The ONE text instruction — the non-inferable business rule.
privileged_instructions = (
    "account_risk_scores is history (one row per scoring run); for an account's CURRENT risk, take the "
    "row with the latest score_date per account_id.")

# Typed relationships (replace the old prose "join keys" sentence). All many-to-one.
_INTEL, _RISK = f"{ctx.catalog}.{ctx.intel}", f"{ctx.catalog}.{ctx.risk}"
privileged_joins = [
    make_join(f"{_INTEL}.indicators", f"{_INTEL}.campaigns", "campaign_id", "campaign_id"),
    make_join(f"{_INTEL}.campaigns", f"{_INTEL}.threat_actors", "actor_id", "actor_id"),
    make_join(f"{_RISK}.account_risk_scores", f"{_RISK}.accounts", "account_id", "account_id"),
    make_join(f"{_RISK}.risk_signals", f"{_RISK}.accounts", "account_id", "account_id"),
    make_join(f"{_RISK}.account_actions", f"{_RISK}.accounts", "account_id", "account_id"),
    make_join(f"{_INTEL}.investigations", f"{_RISK}.accounts", "related_account_id", "account_id"),
]

# Reusable SQL snippets (fully-qualified, quoted column refs — same shape as a Genie UI export).
privileged_expressions = [
    make_snippet(
        f"size(split({_backtick(ctx.catalog, ctx.intel, 'campaigns', 'mitre_ttps')}, ';')) AS technique_count",
        "MITRE technique count",
        "Number of MITRE ATT&CK techniques in a campaign (mitre_ttps is a ';'-delimited string).",
        ["number of techniques", "ttp count"]),
    make_snippet(
        f"DATE_TRUNC('MONTH', {_backtick(ctx.catalog, ctx.risk, 'account_risk_scores', 'score_date')}) AS score_month",
        "Score month",
        "Use when trending risk scores by month.",
        ["month of score"]),
]
privileged_measures = [
    make_snippet(
        f"SUM(CASE WHEN {_backtick(ctx.catalog, ctx.intel, 'campaigns', 'status')}='active' AND "
        f"{_backtick(ctx.catalog, ctx.intel, 'campaigns', 'severity')} IN ('high','critical') "
        f"THEN 1 ELSE 0 END) AS severe_active_campaigns",
        "Severe active campaigns",
        "Count of active campaigns at high or critical severity.",
        ["active critical campaigns"]),
]
privileged_filters = [
    make_snippet(
        f"{_backtick(ctx.catalog, ctx.intel, 'threat_actors', 'is_active')} = TRUE",
        "Active actors",
        "Restrict to currently-active threat actors.",
        ["currently active actors"]),
]
privileged_examples = [
    ("Top 10 highest-risk accounts right now",
     f"WITH latest AS (\n  SELECT account_id, risk_score, risk_band, top_signal,\n"
     f"         row_number() OVER (PARTITION BY account_id ORDER BY score_date DESC) rn\n"
     f"  FROM {ctx.catalog}.{ctx.risk}.account_risk_scores)\n"
     f"SELECT a.account_id, a.customer_name, a.segment, l.risk_score, l.risk_band, l.top_signal\n"
     f"FROM latest l JOIN {ctx.catalog}.{ctx.risk}.accounts a ON a.account_id = l.account_id\n"
     f"WHERE l.rn = 1 ORDER BY l.risk_score DESC LIMIT 10"),
    ("Which active threat actors target Financial Services?",
     f"SELECT DISTINCT a.actor_name, a.sophistication, a.motivation, c.campaign_name, c.severity\n"
     f"FROM {ctx.catalog}.{ctx.intel}.campaigns c JOIN {ctx.catalog}.{ctx.intel}.threat_actors a ON c.actor_id = a.actor_id\n"
     f"WHERE c.target_sector = 'Financial Services' AND a.is_active ORDER BY c.severity DESC"),
    ("What signals drove a given account's risk score?",
     f"SELECT signal_type, signal_value, weight, observed_at\n"
     f"FROM {ctx.catalog}.{ctx.risk}.risk_signals WHERE account_id = 'ACC-001024' ORDER BY observed_at DESC"),
]
privileged_tables = [
    f"{ctx.catalog}.{ctx.intel}.threat_actors", f"{ctx.catalog}.{ctx.intel}.campaigns",
    f"{ctx.catalog}.{ctx.intel}.indicators", f"{ctx.catalog}.{ctx.intel}.investigations",
    f"{ctx.catalog}.{ctx.risk}.accounts", f"{ctx.catalog}.{ctx.risk}.account_risk_scores",
    f"{ctx.catalog}.{ctx.risk}.risk_signals", f"{ctx.catalog}.{ctx.risk}.account_actions"]
privileged_space_id = upsert_space(
    f"{ctx.prefix} · Threat Intel — Privileged (Detail)",
    "Full-detail threat intelligence and account risk for privileged analysts. Base tables incl. IOCs, sources/methods, investigation notes, numeric scores, raw signals. Per-caller masks still enforce.",
    serialize_space(privileged_tables, instructions=privileged_instructions, examples=privileged_examples,
                    joins=privileged_joins, expressions=privileged_expressions,
                    measures=privileged_measures, filters=privileged_filters))

# COMMAND ----------
host = w.config.host
print("Open Genie:      ", f"{host}/genie/rooms/{open_space_id}")
print("Privileged Genie:", f"{host}/genie/rooms/{privileged_space_id}")
dbutils.notebook.exit(json.dumps({"open_space_id": open_space_id, "privileged_space_id": privileged_space_id}))
