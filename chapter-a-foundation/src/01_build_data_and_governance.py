# Databricks notebook source
# MAGIC %md
# MAGIC # Chapter A · 01 — Data + Governance
# MAGIC Builds ONE participant's slice of the shared workshop catalog:
# MAGIC * schemas `{prefix}_ti_intel` / `{prefix}_ti_risk` / `{prefix}_ti_cs`
# MAGIC * copies the seed CSVs (which ride in THIS bundle, `./seed`) into a per-user volume, then loads
# MAGIC   the threat-intel + account-risk tables from them
# MAGIC * applies the governance layer: classification tags, column **masks**, and CS-safe views
# MAGIC
# MAGIC **One governance axis:** the masks unmask for members of `privileged_group`; everyone else is
# MAGIC masked. The *same* group test drives the masks here and the Genie spaces in notebook 03.

# COMMAND ----------
# MAGIC %run ./common

# COMMAND ----------
# Inputs come from the DAB as notebook widgets (set in config.yml). See databricks.yml.
dbutils.widgets.text("catalog", "klevis_demo_catalog")
dbutils.widgets.text("privileged_group", "")
dbutils.widgets.text("user_prefix", "")
dbutils.widgets.text("extra_unmask_users", "")
# seed_src = where the bundled CSVs deployed (the DAB passes ${workspace.file_path}/seed).
dbutils.widgets.text("seed_src", "")

CATALOG = dbutils.widgets.get("catalog")
PRIVILEGED_GROUP = dbutils.widgets.get("privileged_group").strip()
EXTRA_UNMASK_USERS = dbutils.widgets.get("extra_unmask_users")
SEED_SRC = dbutils.widgets.get("seed_src").strip().rstrip("/")

if not PRIVILEGED_GROUP:
    raise ValueError("privileged_group is REQUIRED — set var.privileged_group in config.yml to your "
                     "primary group (its members see unmasked data; everyone else is masked).")
if not SEED_SRC:
    raise ValueError("seed_src is empty — deploy via the DAB (it passes ${workspace.file_path}/seed).")

# workshop_context() comes from ./common — it derives your prefix and the four schema names once.
ctx = workshop_context(spark, catalog=CATALOG, prefix_override=dbutils.widgets.get("user_prefix").strip())
SEED_VOLUME = f"/Volumes/{ctx.catalog}/{ctx.intel}/seed"   # per-user volume (no shared volume)
print(ctx)
print(f"privileged_group={PRIVILEGED_GROUP}   seed_src={SEED_SRC}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. Schemas
# MAGIC The shared catalog is created by the admin ahead of time; we only create our own schemas in it.

# COMMAND ----------
def create_schemas():
    schemas = [(ctx.intel, "Threat intelligence feeds, IOCs, investigations, incidents"),
               (ctx.risk, "Account risk scoring service"),
               (ctx.cs, "Customer-Service-safe governed views")]
    for schema, comment in schemas:
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {ctx.catalog}.{schema} COMMENT '{comment}'")
    print(f"schemas ready: {', '.join(s for s, _ in schemas)}")


create_schemas()

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. Copy the seed CSVs into a per-user volume
# MAGIC The CSVs ride in this bundle (`./seed`) and deployed to `seed_src` (a `/Workspace` path). Both
# MAGIC `/Workspace` and `/Volumes` are mounted on the driver, so a plain file copy works — no shared
# MAGIC volume needed. The data is tiny, so each participant gets their own copy.

# COMMAND ----------
import shutil

CSV_TABLES = ["threat_actors", "campaigns", "indicators", "indicator_intel", "accounts",
              "risk_signals", "account_risk_scores", "investigations", "account_actions", "incidents"]


def copy_seed_csvs():
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {ctx.catalog}.{ctx.intel}.seed "
              f"COMMENT 'Per-user CSV seed (copied from the Chapter A bundle)'")
    for name in CSV_TABLES:
        shutil.copyfile(f"{SEED_SRC}/{name}.csv", f"{SEED_VOLUME}/{name}.csv")
    print(f"copied {len(CSV_TABLES)} CSVs -> {SEED_VOLUME}")


copy_seed_csvs()

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. Load the tables from the CSV seed
# MAGIC Each table is read with an **explicit schema**. (An inferred read would make every column a
# MAGIC STRING, which silently breaks the masks and views — dates must land as DATE, scores as INT, etc.)

# COMMAND ----------
from pyspark.sql.types import (StructType, StructField, StringType, IntegerType,
                               DoubleType, BooleanType, DateType, TimestampType)


def columns(*name_type_pairs):
    """Shorthand for a StructType: columns(("id", StringType()), ("n", IntegerType()), ...)."""
    return StructType([StructField(name, dtype) for name, dtype in name_type_pairs])


def load_table(csv_name, schema, target_table):
    """Read one seed CSV with an explicit schema and write it as a managed table."""
    df = (spark.read.format("csv")
          .option("header", "true").option("mode", "PERMISSIVE")
          .schema(schema).load(f"{SEED_VOLUME}/{csv_name}.csv"))
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target_table)
    print(f"  loaded {target_table:56s} {df.count():6d} rows  <- {csv_name}.csv")


load_table("threat_actors", columns(
    ("actor_id", StringType()), ("actor_name", StringType()), ("aliases", StringType()),
    ("sophistication", StringType()), ("motivation", StringType()), ("origin_region", StringType()),
    ("first_seen", DateType()), ("last_seen", DateType()), ("is_active", BooleanType()),
), f"{ctx.catalog}.{ctx.intel}.threat_actors")

load_table("campaigns", columns(
    ("campaign_id", StringType()), ("campaign_name", StringType()), ("actor_id", StringType()),
    ("target_sector", StringType()), ("mitre_ttps", StringType()), ("severity", StringType()),
    ("status", StringType()), ("start_date", DateType()), ("end_date", DateType()),
), f"{ctx.catalog}.{ctx.intel}.campaigns")

load_table("indicators", columns(
    ("indicator_id", StringType()), ("indicator_type", StringType()), ("indicator_value", StringType()),
    ("campaign_id", StringType()), ("confidence", IntegerType()), ("tlp", StringType()),
    ("source", StringType()), ("source_sensitivity", StringType()),
    ("first_seen", DateType()), ("last_seen", DateType()), ("is_active", BooleanType()),
), f"{ctx.catalog}.{ctx.intel}.indicators")

load_table("indicator_intel", columns(
    ("indicator_id", StringType()), ("indicator_value", StringType()), ("urlhaus_type", StringType()),
    ("host", StringType()), ("url_status", StringType()), ("threat", StringType()),
    ("tags", StringType()), ("family", StringType()), ("payload_file_type", StringType()),
    ("payload_md5", StringType()), ("payload_sha256", StringType()),
    ("urlhaus_reference", StringType()), ("date_added", StringType()),
), f"{ctx.catalog}.{ctx.intel}.indicator_intel")

load_table("accounts", columns(
    ("account_id", StringType()), ("customer_name", StringType()), ("email", StringType()),
    ("segment", StringType()), ("plan_tier", StringType()), ("region", StringType()),
    ("country", StringType()), ("signup_date", DateType()), ("status", StringType()),
), f"{ctx.catalog}.{ctx.risk}.accounts")

load_table("risk_signals", columns(
    ("signal_id", StringType()), ("account_id", StringType()), ("signal_type", StringType()),
    ("signal_value", DoubleType()), ("weight", DoubleType()), ("observed_at", TimestampType()),
), f"{ctx.catalog}.{ctx.risk}.risk_signals")

load_table("account_risk_scores", columns(
    ("score_id", StringType()), ("account_id", StringType()), ("score_date", DateType()),
    ("risk_score", IntegerType()), ("risk_band", StringType()), ("model_version", StringType()),
    ("top_signal", StringType()),
), f"{ctx.catalog}.{ctx.risk}.account_risk_scores")

load_table("investigations", columns(
    ("investigation_id", StringType()), ("title", StringType()), ("analyst", StringType()),
    ("related_account_id", StringType()), ("status", StringType()), ("severity", StringType()),
    ("opened_at", TimestampType()), ("closed_at", TimestampType()), ("summary", StringType()),
    ("detailed_notes", StringType()), ("source_methods", StringType()),
), f"{ctx.catalog}.{ctx.intel}.investigations")

load_table("account_actions", columns(
    ("action_id", StringType()), ("account_id", StringType()), ("action_type", StringType()),
    ("reason_summary", StringType()), ("taken_by", StringType()), ("taken_at", TimestampType()),
    ("related_investigation_id", StringType()),
), f"{ctx.catalog}.{ctx.risk}.account_actions")

load_table("incidents", columns(
    ("incident_id", StringType()), ("created_at", TimestampType()), ("narrative", StringType()),
    ("indicator_value", StringType()), ("indicator_type", StringType()), ("account_id", StringType()),
    ("status", StringType()), ("scenario_label", StringType()),
), f"{ctx.catalog}.{ctx.intel}.incidents")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4. Sink tables written by later chapters
# MAGIC Created empty here so Chapters B/C have a place to write (enrichment round-trip + triage output).

# COMMAND ----------
spark.sql(f"""CREATE TABLE IF NOT EXISTS {ctx.catalog}.{ctx.intel}.enrichment_results (
    enrichment_id STRING, indicator_id STRING, indicator_value STRING, query_status STRING,
    threat STRING, url_status STRING, tags STRING, payload_signature STRING,
    enriched_at TIMESTAMP, enriched_by STRING, source STRING)
  COMMENT 'Persisted URLhaus enrichment verdicts (written by the enrichment workflow / agent).'""")
spark.sql(f"""CREATE TABLE IF NOT EXISTS {ctx.catalog}.{ctx.risk}.triage_recommendations (
    recommendation_id STRING, incident_id STRING, account_id STRING, indicator_value STRING,
    matched_rule_id STRING, recommended_play STRING, rationale STRING, evidence STRING,
    recommended_at TIMESTAMP, recommended_by STRING)
  COMMENT 'Triage agent recommendations (recommend-only; never executes a destructive action).'""")
print("sink tables ready")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 5. Governance — classification tags
# MAGIC Maps each sensitive column to a classification value. These tags are documentation only here —
# MAGIC the masks below key off the privileged **group**, not the tag. (A future enhancement: drive the
# MAGIC masks from the tags via ABAC.) Each tag is wrapped so a strict metastore policy can't abort the build.

# COMMAND ----------
# (table, column, classification value)
TAGGED_COLUMNS = [
    (f"{ctx.catalog}.{ctx.risk}.accounts", "customer_name", "confidential"),
    (f"{ctx.catalog}.{ctx.risk}.accounts", "email", "confidential"),
    (f"{ctx.catalog}.{ctx.risk}.account_risk_scores", "risk_score", "internal"),
    (f"{ctx.catalog}.{ctx.risk}.risk_signals", "signal_value", "internal"),
    (f"{ctx.catalog}.{ctx.intel}.investigations", "detailed_notes", "restricted"),
    (f"{ctx.catalog}.{ctx.intel}.investigations", "source_methods", "restricted"),
    (f"{ctx.catalog}.{ctx.intel}.indicators", "source", "internal"),
]


def apply_classification_tags():
    for table, column, value in TAGGED_COLUMNS:
        try:
            spark.sql(f"ALTER TABLE {table} ALTER COLUMN {column} SET TAGS ('classification'='{value}')")
        except Exception as e:
            print(f"  WARN tag {table}.{column} skipped: {str(e)[:140]}")
    print("classification tags applied")


apply_classification_tags()

# COMMAND ----------
# MAGIC %md
# MAGIC ## 6. Governance — column masks (the one axis: privileged_group vs. everyone)
# MAGIC Two masking functions decide, per caller, whether to reveal a value. The predicate is
# MAGIC `is_account_group_member(privileged_group)` (plus an optional list of extra emails). Attaching a
# MAGIC mask to a column rewrites the value for non-privileged callers — the SAME query returns real data
# MAGIC for a privileged caller and redacted data for everyone else.

# COMMAND ----------
def build_unmask_predicate():
    """The SQL boolean that is TRUE for callers allowed to see unmasked values."""
    predicate = f"is_account_group_member('{PRIVILEGED_GROUP}')"
    extra_users = [u.strip() for u in EXTRA_UNMASK_USERS.split(",") if u.strip()]
    if extra_users:
        in_list = ", ".join(f"'{u}'" for u in extra_users)
        predicate += f" OR current_user() IN ({in_list})"
    return predicate


def apply_masks():
    unmask = build_unmask_predicate()
    # A mask is just a UC function: given a value, return it or a redacted placeholder.
    spark.sql(f"""CREATE OR REPLACE FUNCTION {ctx.catalog}.{ctx.risk}.mask_pii(val STRING)
      RETURN CASE WHEN {unmask} THEN val ELSE '***REDACTED***' END""")
    spark.sql(f"""CREATE OR REPLACE FUNCTION {ctx.catalog}.{ctx.risk}.mask_score(val INT)
      RETURN CASE WHEN {unmask} THEN val ELSE NULL END""")
    # Attach the masks to the sensitive columns.
    spark.sql(f"ALTER TABLE {ctx.catalog}.{ctx.risk}.accounts ALTER COLUMN customer_name SET MASK {ctx.catalog}.{ctx.risk}.mask_pii")
    spark.sql(f"ALTER TABLE {ctx.catalog}.{ctx.risk}.accounts ALTER COLUMN email SET MASK {ctx.catalog}.{ctx.risk}.mask_pii")
    spark.sql(f"ALTER TABLE {ctx.catalog}.{ctx.risk}.account_risk_scores ALTER COLUMN risk_score SET MASK {ctx.catalog}.{ctx.risk}.mask_score")
    print(f"masks applied (unmask when: {unmask})")


apply_masks()

# COMMAND ----------
# MAGIC %md
# MAGIC ## 7. Governance — Customer-Service-safe views (the open tier)
# MAGIC These views are the "everyone" tier: campaign-level threat overview, investigation outcomes, and
# MAGIC why an account was actioned — with no IOC values, no sources/methods, risk **bands** not scores,
# MAGIC and account labels instead of names. They back the Open Genie space in notebook 03.

# COMMAND ----------
def create_cs_safe_views():
    spark.sql(f"""CREATE OR REPLACE VIEW {ctx.catalog}.{ctx.cs}.threat_summary
      COMMENT 'Open/CS-safe: campaign-level threat overview. No IOC values, no sources/methods.' AS
      SELECT c.campaign_id, c.campaign_name, a.actor_name, a.motivation, c.target_sector,
             c.severity, c.status, c.start_date, c.end_date, size(split(c.mitre_ttps, ';')) AS technique_count
      FROM {ctx.catalog}.{ctx.intel}.campaigns c
      JOIN {ctx.catalog}.{ctx.intel}.threat_actors a ON c.actor_id = a.actor_id""")
    spark.sql(f"""CREATE OR REPLACE VIEW {ctx.catalog}.{ctx.cs}.investigation_summaries
      COMMENT 'Open/CS-safe: investigation outcomes only. Excludes detailed_notes and source_methods.' AS
      SELECT investigation_id, title, status, severity, summary, opened_at, closed_at
      FROM {ctx.catalog}.{ctx.intel}.investigations""")
    spark.sql(f"""CREATE OR REPLACE VIEW {ctx.catalog}.{ctx.cs}.account_action_explanations
      COMMENT 'Open/CS-safe: why an account was actioned. Account label, risk BAND only, no score, no PII.' AS
      SELECT act.action_id, concat('Customer-', substr(act.account_id, 5)) AS account_label,
             act.action_type, act.reason_summary, act.taken_at, latest.risk_band
      FROM {ctx.catalog}.{ctx.risk}.account_actions act
      LEFT JOIN (SELECT account_id, risk_band,
                        row_number() OVER (PARTITION BY account_id ORDER BY score_date DESC) AS rn
                 FROM {ctx.catalog}.{ctx.risk}.account_risk_scores) latest
      ON act.account_id = latest.account_id AND latest.rn = 1""")
    print("CS-safe views created")


create_cs_safe_views()

# COMMAND ----------
dbutils.notebook.exit(
    f"{ctx.catalog}: {ctx.intel}/{ctx.risk}/{ctx.cs} built and governed (privileged_group={PRIVILEGED_GROUP}).")
