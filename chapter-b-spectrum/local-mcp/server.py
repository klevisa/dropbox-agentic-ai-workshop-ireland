"""V2 — local MCP server. Chapter B.

The middle point on the skills -> MCP spectrum: the SAME five tools as the V1 skill and the V3 hosted
app, packaged as typed MCP tools over stdio so any MCP client (Claude Code/Desktop, an IDE, an agent
framework) can call them — still a local process running as YOUR identity (the SDK's default auth),
not a hosted service.

The tool definitions are imported from the SAME `threatintel_core.py` the hosted app uses (one
definition, three front doors) — only this wrapper and the identity differ (local-as-you here vs.
hosted-OBO there).

Run locally (needs `mcp>=1.9.2,<2` + `databricks-sdk`, and the databricks CLI authenticated):
    WORKSHOP_CATALOG=<cat> WORKSHOP_WAREHOUSE_ID=<wh> python server.py
Then register it with an MCP client per that client's docs (stdio transport).
"""
import os
import sys

from mcp.server.fastmcp import FastMCP
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem

# Import the shared tool definitions from the hosted-mcp folder (one source of truth).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hosted-mcp"))
from threatintel_core import call_tool, derive_prefix, tools_schema  # noqa: E402

workspace = WorkspaceClient()                    # runs as YOU (default CLI/SDK auth)
CATALOG = os.environ["WORKSHOP_CATALOG"]
WAREHOUSE_ID = os.environ["WORKSHOP_WAREHOUSE_ID"]
PREFIX = os.environ.get("USER_PREFIX") or derive_prefix(workspace.current_user.me().user_name)
SCHEMA_TOOLS = tools_schema(CATALOG, PREFIX)

mcp = FastMCP("aiapps-threatintel-mcp-local")


def run_sql(statement, param_name, value):
    """Execute a tool's SQL via the SQL Statements API, as you. Used by threatintel_core.call_tool()."""
    result = workspace.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=statement,
        parameters=[StatementParameterListItem(name=param_name, value=value)], wait_timeout="30s")
    if not (result.status and result.status.state and result.status.state.value == "SUCCEEDED"):
        raise RuntimeError(f"SQL failed: {getattr(result.status, 'state', '?')}")
    columns = [c.name for c in result.manifest.schema.columns] if result.manifest and result.manifest.schema else []
    rows = result.result.data_array if (result.result and result.result.data_array) else []
    return [dict(zip(columns, row)) for row in rows]


@mcp.tool()
def get_account_risk(account_id: str) -> list[dict]:
    """Latest risk score, band, and top signal for an account (e.g. 'ACC-000888')."""
    return call_tool(run_sql, SCHEMA_TOOLS, "get_account_risk", account_id)


@mcp.tool()
def get_account_actions(account_id: str) -> list[dict]:
    """Protective actions taken on an account and why."""
    return call_tool(run_sql, SCHEMA_TOOLS, "get_account_actions", account_id)


@mcp.tool()
def pivot_indicator(indicator: str) -> list[dict]:
    """Pivot an indicator to its campaign, actor, sibling indicators, and URLhaus family/threat/tags."""
    return call_tool(run_sql, SCHEMA_TOOLS, "pivot_indicator", indicator)


@mcp.tool()
def blast_radius(indicator: str) -> list[dict]:
    """Which internal accounts have this indicator in their incident telemetry (+ latest risk band)."""
    return call_tool(run_sql, SCHEMA_TOOLS, "blast_radius", indicator)


@mcp.tool()
def enrich_indicator(indicator: str) -> list[dict]:
    """URLhaus verdict for an indicator (query_status ok/no_results, threat, url_status, tags, family)."""
    return call_tool(run_sql, SCHEMA_TOOLS, "enrich_indicator", indicator)


if __name__ == "__main__":
    mcp.run()
