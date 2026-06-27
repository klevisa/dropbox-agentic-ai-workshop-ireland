"""Shared tool logic for ALL THREE Chapter B front doors — the skill, the local MCP, and the hosted
MCP all import THIS module, so the five tools are defined exactly once and never drift apart.

Each tool is a thin wrapper over a Chapter A governed UC function in `{prefix}_ti_tools`:
    SELECT * FROM <catalog>.<prefix>_ti_tools.<function>(:arg)

What differs between the three front doors is only *who runs the SQL and how*:
  - the skill runs it via the `databricks` CLI, as you;
  - the local MCP runs it via the SDK, as you;
  - the hosted MCP runs it via the SDK with the caller's forwarded token (OBO).
So each front door supplies its own `run_sql(statement, param_name, value) -> list[dict]` callable,
and reuses the tool definitions below.
"""
import re

# tool name -> (UC function name, SQL parameter name, one-line description shown to agents/clients)
TOOLS = {
    "get_account_risk": (
        "get_account_risk", "acct",
        "Latest risk score, band, and top contributing signal for an account (e.g. 'ACC-000888'). "
        "The score and customer name are masked unless the caller is in the privileged group."),
    "get_account_actions": (
        "get_account_actions", "acct",
        "Protective actions taken on an account and why (type, reason, who, when, linked investigation)."),
    "pivot_indicator": (
        "pivot_indicator", "ind",
        "Pivot an indicator (value or IOC-id) to its campaign, threat actor, sibling indicators, and "
        "URLhaus family/threat/tags — turning a lone IOC into an attributed cluster."),
    "blast_radius": (
        "blast_radius", "ind",
        "Which internal accounts have this indicator in their incident telemetry, with each account's "
        "latest risk band — the blast radius of an IOC."),
    "enrich_indicator": (
        "enrich_indicator", "ind",
        "Enrich an indicator (URL, IP, domain, md5, or sha256) against the URLhaus threat feed. Returns "
        "the verdict: query_status ('ok' = known-bad / 'no_results' = unknown), threat, url_status, "
        "tags, payload family."),
}


def derive_prefix(email):
    """Per-participant schema prefix: the email local-part with non-alphanumerics turned into '_'.
    klevis.aliaj@databricks.com -> klevis_aliaj"""
    return re.sub(r"[^a-zA-Z0-9]", "_", email.split("@")[0]).lower()


def tools_schema(catalog, prefix):
    """The fully-qualified schema holding the UC-function tools."""
    return f"{catalog}.{prefix}_ti_tools"


def tool_statement(schema_tools, tool):
    """The single SQL statement a tool runs, plus its parameter name. Same for every front door."""
    if tool not in TOOLS:
        raise ValueError(f"unknown tool {tool!r}; valid tools: {', '.join(TOOLS)}")
    function, param, _ = TOOLS[tool]
    return f"SELECT * FROM {schema_tools}.{function}(:{param})", param


def call_tool(run_sql, schema_tools, tool, value):
    """Run one tool by name. `run_sql(statement, param_name, value) -> list[dict]` is supplied by the
    front door — that's where the identity/transport difference lives (CLI / SDK / OBO)."""
    statement, param = tool_statement(schema_tools, tool)
    return run_sql(statement, param, value)
