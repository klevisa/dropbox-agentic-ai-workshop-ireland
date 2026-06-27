# Databricks notebook source
# MAGIC %md
# MAGIC # Chapter A — Explore (interact)
# MAGIC Run this **after** `bundle deploy` + `bundle run chapter_a_foundation`. It's the hands-on tour of
# MAGIC what Chapter A built — **DABs deploy, notebooks interact.**
# MAGIC
# MAGIC You'll see: the governance axis (masked vs. unmasked), the 5 UC-function tools, the CS-safe
# MAGIC views, and links to your two Genie spaces.

# COMMAND ----------
# MAGIC %run ./src/common

# COMMAND ----------
# Set these two to match how you deployed (catalog + your privileged_group). Your schema prefix is
# derived for you by workshop_context() (from ./src/common).
CATALOG = ""                           # <- set your workshop catalog
PRIVILEGED_GROUP = ""                  # <- the privileged_group you deployed with (for the membership check)

ctx = workshop_context(spark, catalog=CATALOG)
print(ctx)

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. The governance axis — are you privileged?
# MAGIC The masks unmask only for members of `privileged_group`. This shows whether *you* are in it — and
# MAGIC therefore whether the next query returns real values or `***REDACTED***` / NULL.

# COMMAND ----------
if PRIVILEGED_GROUP:
    am_member = spark.sql(f"SELECT is_account_group_member('{PRIVILEGED_GROUP}')").collect()[0][0]
    print(f"am I in privileged_group '{PRIVILEGED_GROUP}'? -> {am_member}")
else:
    print("Set PRIVILEGED_GROUP (top cell) to the group you deployed with to test membership.")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. Masked vs. unmasked
# MAGIC The SAME query returns different cells depending on the caller. If you're **not** privileged,
# MAGIC `customer_name` is `***REDACTED***` and `risk_score` is NULL — but `risk_band` always shows.

# COMMAND ----------
display(spark.sql(f"""
  WITH latest AS (SELECT account_id, risk_score, risk_band,
                         row_number() OVER (PARTITION BY account_id ORDER BY score_date DESC) rn
                  FROM {ctx.catalog}.{ctx.risk}.account_risk_scores)
  SELECT a.account_id, a.customer_name, a.segment, l.risk_score, l.risk_band
  FROM latest l JOIN {ctx.catalog}.{ctx.risk}.accounts a ON a.account_id = l.account_id
  WHERE l.rn = 1 ORDER BY l.risk_band DESC LIMIT 10"""))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. The 5 UC-function tools
# MAGIC These run with **your** identity, so the same masks apply inside them — same governance whether
# MAGIC called from SQL, Genie, an agent, or MCP. We pick **real example arguments from your data** so
# MAGIC every call returns rows (not every account has a risk score; only feed indicators enrich).

# COMMAND ----------
def first_value(query):
    """Return the first column of the first row of a query, or None if there are no rows."""
    rows = spark.sql(query).collect()
    return rows[0][0] if rows else None


# An account that actually has a risk score (so get_account_risk returns a row).
example_account = first_value(
    f"SELECT account_id FROM {ctx.catalog}.{ctx.risk}.account_risk_scores LIMIT 1")
# A real malware indicator that shows up in incident telemetry (so blast_radius isn't empty) AND
# is in the URLhaus feed (so pivot/enrich have a verdict).
example_indicator = first_value(f"""
    SELECT inc.indicator_value FROM {ctx.catalog}.{ctx.intel}.incidents inc
    JOIN {ctx.catalog}.{ctx.intel}.indicator_intel ii ON ii.indicator_value = inc.indicator_value
    LIMIT 1""")
print(f"example_account   = {example_account}")
print(f"example_indicator = {example_indicator}")

# COMMAND ----------
# Account tools
display(spark.sql(f"SELECT * FROM {ctx.catalog}.{ctx.tools}.get_account_risk('{example_account}')"))
display(spark.sql(f"SELECT * FROM {ctx.catalog}.{ctx.tools}.get_account_actions('{example_account}')"))

# COMMAND ----------
# Indicator tools — attribute the IOC to its campaign/actor, then scope which accounts saw it.
display(spark.sql(f"SELECT * FROM {ctx.catalog}.{ctx.tools}.pivot_indicator('{example_indicator}')"))
display(spark.sql(f"SELECT * FROM {ctx.catalog}.{ctx.tools}.blast_radius('{example_indicator}')"))

# COMMAND ----------
# enrich_indicator — table-backed URLhaus verdict (no external call). It matches the artifact value or
# a payload hash, NOT our internal IOC-id. So we enrich a real feed URL and a real payload hash; an
# unknown URL returns 'no_results'.
example_url = first_value(
    f"SELECT indicator_value FROM {ctx.catalog}.{ctx.intel}.indicator_intel WHERE urlhaus_type='url' LIMIT 1")
example_hash = first_value(
    f"SELECT payload_sha256 FROM {ctx.catalog}.{ctx.intel}.indicator_intel WHERE payload_sha256 <> '' LIMIT 1")
print(f"example_url  = {example_url}")
print(f"example_hash = {example_hash}")
if example_url:
    display(spark.sql(f"SELECT * FROM {ctx.catalog}.{ctx.tools}.enrich_indicator('{example_url}')"))
if example_hash:
    display(spark.sql(f"SELECT * FROM {ctx.catalog}.{ctx.tools}.enrich_indicator('{example_hash}')"))
display(spark.sql(f"SELECT * FROM {ctx.catalog}.{ctx.tools}.enrich_indicator('http://not-in-the-feed.invalid/')"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4. CS-safe (open) views — what the non-privileged tier gets
# MAGIC No PII, risk **bands** not scores, no sources/methods. These back the Open Genie space.

# COMMAND ----------
display(spark.sql(f"SELECT * FROM {ctx.catalog}.{ctx.cs}.account_action_explanations LIMIT 10"))
display(spark.sql(f"SELECT * FROM {ctx.catalog}.{ctx.cs}.threat_summary LIMIT 10"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 5. Your Genie spaces
# MAGIC Open the two spaces (Workspace ▸ Genie) and ask the curated questions — the **Open (Summary)**
# MAGIC space is bound to the CS views; the **Privileged (Detail)** space to base tables. Compare answers
# MAGIC for the same question as a privileged vs. non-privileged caller.

# COMMAND ----------
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
spaces = [s for s in w.api_client.do("GET", "/api/2.0/genie/spaces").get("spaces", [])
          if s.get("title", "").startswith(f"{ctx.prefix} · Threat Intel")]
for s in spaces:
    print(f"{s['title']:48s} {w.config.host}/genie/rooms/{s['space_id']}")
