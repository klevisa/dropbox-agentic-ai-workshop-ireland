# Databricks notebook source
# MAGIC %md
# MAGIC # Chapter B — Explore (the skills ↔ MCP spectrum)
# MAGIC The same five tools, three front doors — all wrapping your Chapter A UC functions in
# MAGIC `{prefix}_ti_tools`, all defined once in `hosted-mcp/threatintel_core.py`:
# MAGIC * **V1 skill** — your terminal / a coding agent, as **you** (`skill/`).
# MAGIC * **V2 local MCP** — typed MCP tools, still local, as **you** (`local-mcp/`).
# MAGIC * **V3 hosted MCP** — a Databricks App, callable by agents, **as the caller via OBO** (`hosted-mcp/`).
# MAGIC
# MAGIC This notebook shows what all three wrap, how to reach the hosted one, and then the **autonomous
# MAGIC agent** (`triage-agent/`) that *chooses* these tools itself — the counterpart to Chapter C's
# MAGIC governed workflow.

# COMMAND ----------
# MAGIC %md
# MAGIC ## 0. Install mlflow (serverless only)
# MAGIC Serverless Python doesn't ship `mlflow`, which §3 uses to query the agent endpoint. Install it and
# MAGIC restart the interpreter **first** — `%restart_python` clears earlier state, so this has to run before
# MAGIC `%run ./common` and everything below.
# MAGIC
# MAGIC Leave `PYPI_INDEX` blank to install from public PyPI. Only set it if your workspace blocks public
# MAGIC PyPI and you have an internal mirror (`pip` reads it via `PIP_INDEX_URL`).

# COMMAND ----------
import os
PYPI_INDEX = ""  # <- leave blank for public PyPI; set to your internal index only if PyPI is blocked
if PYPI_INDEX.strip():
    os.environ["PIP_INDEX_URL"] = PYPI_INDEX.strip()

# COMMAND ----------
# MAGIC %pip install -q mlflow
# MAGIC %restart_python

# COMMAND ----------
# MAGIC %run ./common

# COMMAND ----------
CATALOG = ""                           # <- set your catalog
ctx = workshop_context(spark, catalog=CATALOG)
SCHEMA_TOOLS = f"{ctx.catalog}.{ctx.tools}"
SCHEMA_INTEL = f"{ctx.catalog}.{ctx.intel}"
SCHEMA_RISK = f"{ctx.catalog}.{ctx.risk}"
print(f"you are {ctx.me}  ·  tools schema {SCHEMA_TOOLS}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. The tools themselves (what every version wraps)
# MAGIC These are the governed UC functions from Chapter A. Calling them directly here is the baseline;
# MAGIC the skill, the local MCP, and the hosted MCP each just invoke these. We pick **real arguments
# MAGIC from your data** so each call returns rows.

# COMMAND ----------
def first_value(query):
    """First column of the first row, or None if the query returns nothing."""
    rows = spark.sql(query).collect()
    return rows[0][0] if rows else None


# An account that actually has a risk score; a real malware indicator seen in incident telemetry.
example_account = first_value(f"SELECT account_id FROM {SCHEMA_RISK}.account_risk_scores LIMIT 1")
example_indicator = first_value(f"""
    SELECT inc.indicator_value FROM {SCHEMA_INTEL}.incidents inc
    JOIN {SCHEMA_INTEL}.indicator_intel ii ON ii.indicator_value = inc.indicator_value LIMIT 1""")
example_url = first_value(
    f"SELECT indicator_value FROM {SCHEMA_INTEL}.indicator_intel WHERE urlhaus_type='url' LIMIT 1")
print(f"account={example_account}  indicator={example_indicator}")

display(spark.sql(f"SELECT * FROM {SCHEMA_TOOLS}.get_account_risk('{example_account}')"))
display(spark.sql(f"SELECT * FROM {SCHEMA_TOOLS}.enrich_indicator('{example_url}')"))
display(spark.sql(f"SELECT * FROM {SCHEMA_TOOLS}.pivot_indicator('{example_indicator}')"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. The hosted V3 MCP (OBO)
# MAGIC The hosted app runs each tool's SQL **as the caller** (forwarded token), so the masks apply to
# MAGIC *your* identity — same governance, now reachable by autonomous agents.
# MAGIC
# MAGIC **Try it in the AI Playground** (no client script needed):
# MAGIC 1. Open **Playground** (*Machine Learning ▸ Playground*).
# MAGIC 2. **Tools ▸ Add tools ▸ MCP server**, pick your app (`mcp-<your-handle>`), add its five tools.
# MAGIC 3. Ask e.g. *"what's the risk on `ACC-000888`?"* — the Playground forwards your token, so the
# MAGIC    MCP runs the tool **as you (OBO)**, masked or unmasked to match your group.

# COMMAND ----------
# Confirm your app is up (metadata only — exercise the tools in the Playground).
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
resp = w.api_client.do("GET", "/api/2.0/apps")
apps = resp.get("apps", []) if isinstance(resp, dict) else []
mine = [a for a in apps if (a.get("name") or "").startswith("mcp-")]
for a in mine:
    print(f"{a.get('name'):28s} state={(a.get('compute_status') or {}).get('state')}  url={a.get('url')}")
if not mine:
    print("No mcp-* app yet — deploy Chapter B first (databricks bundle run threatintel_mcp -t dev).")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. The autonomous agent (it picks its own tools)
# MAGIC The skill, local MCP, and hosted MCP above are tool **surfaces** — *you* (or an agent) decide what
# MAGIC to call. `triage-agent/agent.py` is the **genuine agent**: deployed to a **Model Serving** endpoint,
# MAGIC you hand it just an **incident id** and *its* LLM decides which of the five UC-function tools to
# MAGIC call, and in what order, until it can recommend one containment play — a bounded tool-calling loop,
# MAGIC no pre-authored plan. It returns the same JSON shape as Chapter C's `triage_runner`, but Chapter C
# MAGIC runs an **approved plan** (a governed workflow) whereas this agent is **autonomous**.
# MAGIC
# MAGIC It's deployed **on-behalf-of-user (OBO)**: because you can call it ad hoc (the AI Playground, a
# MAGIC notebook, another agent), it runs each tool **as whoever asked** — so the Chapter A masks apply to
# MAGIC *you*. Ask it as a privileged-group member and its evidence is unmasked; ask as a non-member and the
# MAGIC same agent sees `***REDACTED***`. Governance follows the caller, even through an autonomous agent.
# MAGIC (`deploy.py` declares this with an MLflow `AuthPolicy` — LLM as a system resource, `sql.*` scopes
# MAGIC on the caller's behalf.)
# MAGIC
# MAGIC Deploy it first: `databricks bundle run deploy_triage_agent -t dev` (or step **B** of `deploy_workshop.py`).

# COMMAND ----------
# Find your agent endpoint (named agents_<catalog>-<your tools schema>-<model>) and query it with a real
# incident id. The endpoint scales from zero, so the first call after a deploy can take a few minutes.
import json
from databricks.sdk import WorkspaceClient
from mlflow.deployments import get_deploy_client

w = WorkspaceClient()
agent_eps = [e.name for e in w.serving_endpoints.list()
             if e.name.startswith("agents_") and ctx.tools in e.name]
example_incident = first_value(f"SELECT incident_id FROM {SCHEMA_INTEL}.incidents LIMIT 1")

if not agent_eps:
    print("No agent endpoint yet — deploy it: databricks bundle run deploy_triage_agent -t dev")
else:
    endpoint = agent_eps[0]
    print(f"querying {endpoint} with incident {example_incident} ...\n")
    try:
        resp = get_deploy_client("databricks").predict(
            endpoint=endpoint, inputs={"input": [{"role": "user", "content": example_incident}]})
        rec = json.loads(resp["output"][0]["content"][0]["text"])
        print(json.dumps(rec, indent=2))
        # The agent CHOSE these tools itself — different incidents will drive different tool paths.
        print(f"\ntools the agent chose to call: {rec.get('tools_called')}")
    except Exception as e:
        print(f"endpoint not READY yet ({str(e)[:140]}). It scales from zero — retry in a few minutes.")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4. The spectrum, summarized
# MAGIC | | runs where | as whom | who can call it |
# MAGIC |---|---|---|---|
# MAGIC | **V1 skill** | terminal / coding agent | you | a human, interactively |
# MAGIC | **V2 local MCP** | your machine | you | any MCP client on your machine |
# MAGIC | **V3 hosted MCP** | Databricks App | **the caller (OBO)** | humans **and** autonomous agents, 24/7 |
# MAGIC
# MAGIC Those three are tool **surfaces** — same five tools, same per-caller masks, escalating reach. The
# MAGIC **autonomous agent** (§3) is a different thing: a *consumer* that picks tools itself and runs as its
# MAGIC own SP. V3 is exactly the surface such an agent reaches through in production; Chapter C pairs the
# MAGIC same tools with a human-approved plan (a governed **workflow**) instead of letting the LLM choose.
