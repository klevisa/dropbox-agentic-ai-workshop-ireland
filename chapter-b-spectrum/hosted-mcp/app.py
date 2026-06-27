"""V3 — the hosted, on-behalf-of-user (OBO) MCP server (a Databricks App). Chapter B.

The third point on the skills -> MCP spectrum. Same five tools as the V1 skill and V2 local MCP (all
defined in threatintel_core.py), but hosted as a Databricks App so autonomous agents can reach it —
and it runs **OBO**: every tool call executes its SQL AS THE CALLER (the forwarded user token), so the
UC functions' per-caller column masks apply automatically. The app's own service principal only fronts
ingress; it carries no data privileges.

OBO mechanics:
  - The app is deployed with `user_api_scopes: [sql]` (see databricks.yml), so Databricks injects the
    caller's token as the `X-Forwarded-Access-Token` header on each request.
  - We read that header and build a per-request WorkspaceClient pinned to the token, so the SQL runs as
    the caller. If the header is absent (local dev, or user authorization not enabled), we fall back to
    the app's own service principal — which can't see your schemas, so calls will return nothing.

Env (set by the DAB apps resource): WORKSHOP_CATALOG, USER_PREFIX, WORKSHOP_WAREHOUSE_ID.
DATABRICKS_HOST is injected by the Apps runtime.
"""
import os

from mcp.server.fastmcp import Context, FastMCP  # mcp >= 1.9.2 (per-request request_context.request)
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.service.sql import StatementParameterListItem

from threatintel_core import call_tool, tools_schema

CATALOG = os.environ["WORKSHOP_CATALOG"]
PREFIX = os.environ["USER_PREFIX"]
WAREHOUSE_ID = os.environ["WORKSHOP_WAREHOUSE_ID"]
HOST = os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_WORKSPACE_URL")
SCHEMA_TOOLS = tools_schema(CATALOG, PREFIX)

mcp = FastMCP("aiapps-threatintel-mcp", host="0.0.0.0", port=8000, streamable_http_path="/mcp")


def caller_workspace_client(ctx: Context) -> WorkspaceClient:
    """A WorkspaceClient bound to the calling user's forwarded token (OBO). Falls back to the app's
    service principal when no token is present (local dev, or user authorization not enabled)."""
    request = getattr(ctx.request_context, "request", None)
    token = request.headers.get("x-forwarded-access-token") if request is not None else None
    if token and HOST:
        return WorkspaceClient(config=Config(host=HOST, token=token, auth_type="pat",
                                             client_id=None, client_secret=None))
    return WorkspaceClient()


def make_run_sql(ctx: Context):
    """Return a run_sql(statement, param_name, value) -> list[dict] that executes AS THE CALLER.
    threatintel_core.call_tool() uses this; the tool definitions themselves live in that module."""
    workspace = caller_workspace_client(ctx)

    def run_sql(statement, param_name, value):
        result = workspace.statement_execution.execute_statement(
            warehouse_id=WAREHOUSE_ID, statement=statement,
            parameters=[StatementParameterListItem(name=param_name, value=value)], wait_timeout="30s")
        state = result.status.state.value if result.status and result.status.state else "UNKNOWN"
        if state != "SUCCEEDED":
            error = getattr(result.status, "error", None)
            raise RuntimeError(f"SQL {state}: {getattr(error, 'message', '') if error else ''}")
        columns = [c.name for c in result.manifest.schema.columns] if result.manifest and result.manifest.schema else []
        rows = result.result.data_array if (result.result and result.result.data_array) else []
        return [dict(zip(columns, row)) for row in rows]

    return run_sql


# Five thin, typed MCP tools. Each just forwards to the shared core; the SQL lives in threatintel_core.
@mcp.tool()
def get_account_risk(account_id: str, ctx: Context) -> list[dict]:
    """Latest risk score, band, and top contributing signal for an account (e.g. 'ACC-000888').
    The numeric score and customer name are masked unless the caller is in the privileged group."""
    return call_tool(make_run_sql(ctx), SCHEMA_TOOLS, "get_account_risk", account_id)


@mcp.tool()
def get_account_actions(account_id: str, ctx: Context) -> list[dict]:
    """Protective actions taken on an account and why (type, reason, who, when, linked investigation)."""
    return call_tool(make_run_sql(ctx), SCHEMA_TOOLS, "get_account_actions", account_id)


@mcp.tool()
def pivot_indicator(indicator: str, ctx: Context) -> list[dict]:
    """Pivot an indicator (value or IOC-id) to its campaign, threat actor, sibling indicators, and the
    URLhaus family/threat/tags — turning a lone IOC into an attributed cluster."""
    return call_tool(make_run_sql(ctx), SCHEMA_TOOLS, "pivot_indicator", indicator)


@mcp.tool()
def blast_radius(indicator: str, ctx: Context) -> list[dict]:
    """Which internal accounts have this indicator in their incident telemetry, with each account's
    latest risk band — the blast radius of an IOC."""
    return call_tool(make_run_sql(ctx), SCHEMA_TOOLS, "blast_radius", indicator)


@mcp.tool()
def enrich_indicator(indicator: str, ctx: Context) -> list[dict]:
    """Enrich an indicator (URL, IP, domain, md5, or sha256) against the URLhaus threat feed. Returns
    the verdict: query_status ('ok' = known-bad / 'no_results' = unknown), threat, url_status, tags,
    payload family."""
    return call_tool(make_run_sql(ctx), SCHEMA_TOOLS, "enrich_indicator", indicator)


# ASGI app for uvicorn (the Databricks App entrypoint). Serves the MCP at /mcp.
app = mcp.streamable_http_app()
