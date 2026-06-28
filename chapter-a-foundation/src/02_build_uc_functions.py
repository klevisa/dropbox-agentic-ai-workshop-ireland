# Databricks notebook source
# MAGIC %md
# MAGIC # Chapter A · 02 — UC Function Tools
# MAGIC Creates the **5 governed UC functions** in `{prefix}_ti_tools` — the read/compute "tools" as
# MAGIC Unity Catalog objects. As UC functions they:
# MAGIC * are governed by `GRANT EXECUTE`,
# MAGIC * run with the **caller's** identity, so the column masks from notebook 01 apply per caller
# MAGIC   automatically (no on-behalf-of plumbing inside the function), and
# MAGIC * are callable the same way from SQL, Genie, a Mosaic AI agent, and an MCP server.
# MAGIC
# MAGIC `enrich_indicator` returns the URLhaus verdict by looking the indicator up in the local
# MAGIC `indicator_intel` table — **no external connection, no http_request, no service principal**. In
# MAGIC production this would call the real URLhaus API; table-backed here keeps the tool/agent story
# MAGIC identical with zero egress plumbing.

# COMMAND ----------
# MAGIC %run ./common

# COMMAND ----------
dbutils.widgets.text("catalog", "klevis_demo_catalog")
dbutils.widgets.text("user_prefix", "")

ctx = workshop_context(spark, catalog=dbutils.widgets.get("catalog"),
                       prefix_override=dbutils.widgets.get("user_prefix").strip())
print(ctx)

spark.sql(f"""CREATE SCHEMA IF NOT EXISTS {ctx.catalog}.{ctx.tools}
  COMMENT 'Governed UC functions exposed as agent/Genie/MCP tools (per-caller masking applies)'""")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Account tools: `get_account_risk`, `get_account_actions`

# COMMAND ----------
# get_account_risk — latest score/band/top signal + customer (the score + name are masked per caller).
spark.sql(f"""CREATE OR REPLACE FUNCTION {ctx.catalog}.{ctx.tools}.get_account_risk(
  acct STRING COMMENT 'Account id, e.g. ACC-000888')
RETURNS TABLE (account_id STRING, customer_name STRING, segment STRING,
               risk_score INT, risk_band STRING, top_signal STRING)
COMMENT 'Latest risk score, band, and top contributing signal for an account. The numeric score and customer name are masked unless the caller is in the privileged group (column masks apply per caller).'
RETURN
  SELECT a.account_id, a.customer_name, a.segment, latest.risk_score, latest.risk_band, latest.top_signal
  FROM (SELECT account_id, risk_score, risk_band, top_signal,
               row_number() OVER (PARTITION BY account_id ORDER BY score_date DESC) AS rn
        FROM {ctx.catalog}.{ctx.risk}.account_risk_scores) latest
  JOIN {ctx.catalog}.{ctx.risk}.accounts a ON a.account_id = latest.account_id
  WHERE latest.rn = 1 AND a.account_id = acct""")

# get_account_actions — protective actions taken on an account and why.
spark.sql(f"""CREATE OR REPLACE FUNCTION {ctx.catalog}.{ctx.tools}.get_account_actions(
  acct STRING COMMENT 'Account id')
RETURNS TABLE (action_type STRING, reason_summary STRING, taken_by STRING,
               taken_at TIMESTAMP, related_investigation_id STRING)
COMMENT 'Protective actions taken on an account and why (type, reason, who, when, linked investigation).'
RETURN
  SELECT action_type, reason_summary, taken_by, taken_at, related_investigation_id
  FROM {ctx.catalog}.{ctx.risk}.account_actions WHERE account_id = acct ORDER BY taken_at DESC""")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Indicator tools: `pivot_indicator`, `blast_radius`

# COMMAND ----------
# pivot_indicator — turn a lone IOC into an attributed cluster: its campaign, actor, sibling
# indicators, and the URLhaus family/threat/tags. Accepts the indicator value OR its IOC-id.
spark.sql(f"""CREATE OR REPLACE FUNCTION {ctx.catalog}.{ctx.tools}.pivot_indicator(
  ind STRING COMMENT 'Indicator value (URL/IP/domain/hash) or IOC-id')
RETURNS TABLE (indicator_value STRING, indicator_type STRING, campaign_id STRING,
               campaign_name STRING, campaign_severity STRING, actor_id STRING,
               actor_name STRING, actor_aliases STRING, family STRING, threat STRING,
               tags STRING, sibling_count BIGINT, sibling_indicators ARRAY<STRING>)
COMMENT 'Pivot an indicator to its campaign, threat actor, sibling indicators (same campaign), and the URLhaus family/threat/tags. Turns a lone IOC into an attributed cluster.'
RETURN
  WITH hit AS (
    SELECT i.indicator_id, i.indicator_value, i.indicator_type, i.campaign_id,
           c.campaign_name, c.severity AS campaign_severity, c.actor_id,
           ta.actor_name, ta.aliases AS actor_aliases, ii.family, ii.threat, ii.tags
    FROM {ctx.catalog}.{ctx.intel}.indicators i
    LEFT JOIN {ctx.catalog}.{ctx.intel}.campaigns c ON i.campaign_id = c.campaign_id
    LEFT JOIN {ctx.catalog}.{ctx.intel}.threat_actors ta ON c.actor_id = ta.actor_id
    LEFT JOIN {ctx.catalog}.{ctx.intel}.indicator_intel ii ON i.indicator_id = ii.indicator_id
    WHERE i.indicator_value = ind OR i.indicator_id = ind
    LIMIT 1)
  SELECT h.indicator_value, h.indicator_type, h.campaign_id, h.campaign_name, h.campaign_severity,
         h.actor_id, h.actor_name, h.actor_aliases, h.family, h.threat, h.tags,
         (SELECT count(*) FROM {ctx.catalog}.{ctx.intel}.indicators s
            WHERE s.campaign_id = h.campaign_id AND s.indicator_value <> h.indicator_value) AS sibling_count,
         (SELECT slice(collect_list(s.indicator_value), 1, 8) FROM {ctx.catalog}.{ctx.intel}.indicators s
            WHERE s.campaign_id = h.campaign_id AND s.indicator_value <> h.indicator_value) AS sibling_indicators
  FROM hit h""")

# blast_radius — which internal accounts have this indicator in their incident telemetry.
spark.sql(f"""CREATE OR REPLACE FUNCTION {ctx.catalog}.{ctx.tools}.blast_radius(
  ind STRING COMMENT 'Indicator value (URL/IP/domain/hash)')
RETURNS TABLE (account_id STRING, segment STRING, risk_band STRING, hits BIGINT, last_seen TIMESTAMP)
COMMENT 'Scope the blast radius of an indicator: which internal accounts have it in their incident telemetry, with each account latest risk band.'
RETURN
  SELECT inc.account_id, a.segment, sc.risk_band, count(*) AS hits, max(inc.created_at) AS last_seen
  FROM {ctx.catalog}.{ctx.intel}.incidents inc
  LEFT JOIN {ctx.catalog}.{ctx.risk}.accounts a ON a.account_id = inc.account_id
  LEFT JOIN (SELECT account_id, risk_band,
                    row_number() OVER (PARTITION BY account_id ORDER BY score_date DESC) AS rn
             FROM {ctx.catalog}.{ctx.risk}.account_risk_scores) sc ON sc.account_id = inc.account_id AND sc.rn = 1
  WHERE inc.indicator_value = ind
  GROUP BY inc.account_id, a.segment, sc.risk_band ORDER BY hits DESC""")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Enrichment tool: `enrich_indicator`
# MAGIC Looks the **artifact** up in the local `indicator_intel` feed — by its value (URL / IP / domain)
# MAGIC or by a payload hash (md5 / sha256). This is what an external URLhaus lookup would key on, so it
# MAGIC does **not** match on our internal IOC-id (use `pivot_indicator` for id-based lookups). Returns
# MAGIC exactly one row: `query_status = 'ok'` (known-bad) with the verdict, or `'no_results'` (unknown).

# COMMAND ----------
spark.sql(f"""CREATE OR REPLACE FUNCTION {ctx.catalog}.{ctx.tools}.enrich_indicator(
  ind STRING COMMENT 'The artifact to enrich: a URL, IP, domain, md5, or sha256')
RETURNS TABLE (indicator STRING, query_status STRING, threat STRING, url_status STRING, tags STRING, family STRING)
COMMENT 'Enrich an indicator against the URLhaus threat feed (table-backed by indicator_intel). Matches on the artifact value or a payload hash. Returns the verdict: query_status ok=known-bad / no_results=unknown, plus threat, url_status, tags, payload family.'
RETURN
  WITH hit AS (
    SELECT indicator_value, url_status, threat, tags, family
    FROM {ctx.catalog}.{ctx.intel}.indicator_intel
    WHERE indicator_value = ind OR payload_md5 = ind OR payload_sha256 = ind
    LIMIT 1)
  SELECT ind AS indicator,
         CASE WHEN h.indicator_value IS NOT NULL THEN 'ok' ELSE 'no_results' END AS query_status,
         h.threat, h.url_status, h.tags, h.family
  FROM (SELECT 1 AS x) d LEFT JOIN hit h ON TRUE""")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Grant EXECUTE
# MAGIC These are your own tools, so we grant EXECUTE to you. The per-caller masks still enforce inside
# MAGIC each function regardless of who can run it.

# COMMAND ----------
TOOL_FUNCTIONS = ["get_account_risk", "get_account_actions", "pivot_indicator",
                  "blast_radius", "enrich_indicator"]
for fn in TOOL_FUNCTIONS:
    spark.sql(f"GRANT EXECUTE ON FUNCTION {ctx.catalog}.{ctx.tools}.{fn} TO `{ctx.me}`")
# Let the whole workshop call these tools too (USE SCHEMA + EXECUTE cascades to all functions in the
# schema). They run invoker-rights, so the caller's own group drives the masks inside — same governance.
spark.sql(f"GRANT USE SCHEMA ON SCHEMA {ctx.catalog}.{ctx.tools} TO `account users`")
spark.sql(f"GRANT EXECUTE ON SCHEMA {ctx.catalog}.{ctx.tools} TO `account users`")
print(f"{len(TOOL_FUNCTIONS)} UC functions created in {ctx.catalog}.{ctx.tools}; granted to {ctx.me} "
      f"+ EXECUTE to `account users`")
dbutils.notebook.exit(f"{ctx.catalog}.{ctx.tools}: {len(TOOL_FUNCTIONS)} UC functions built (table-backed enrich, no connection).")
