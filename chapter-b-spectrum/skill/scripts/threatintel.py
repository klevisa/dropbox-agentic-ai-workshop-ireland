#!/usr/bin/env python3
"""Threat-intel investigation tools, run from your terminal as YOU (your databricks CLI profile).

This is the skill's backend. It imports the SAME tool definitions (threatintel_core.py) that the local
MCP and the hosted MCP use — one definition, three front doors. Here the front door is the `databricks`
CLI, so each tool's SQL runs through the SQL Statements API under your profile. Verdicts are
table-backed (no external calls).

Usage:
    python3 threatintel.py <tool> <arg>
    tools: get_account_risk | get_account_actions | pivot_indicator | blast_radius | enrich_indicator

Config via env (or flags): WORKSHOP_CATALOG, WORKSHOP_WAREHOUSE_ID, DATABRICKS_PROFILE.
Your schema prefix is derived from your current_user() automatically.
"""
import argparse
import json
import os
import subprocess
import sys

# Import the shared tool definitions from the hosted-mcp folder (one source of truth).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "hosted-mcp"))
from threatintel_core import TOOLS, call_tool, derive_prefix, tools_schema  # noqa: E402


def cli_json(args):
    """Run a databricks CLI command and parse its JSON stdout."""
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def main():
    parser = argparse.ArgumentParser(description="Threat-intel investigation tools (runs as you).")
    parser.add_argument("tool", choices=list(TOOLS), help="which tool to run")
    parser.add_argument("arg", help="the account id or indicator to pass")
    parser.add_argument("--profile", default=os.environ.get("DATABRICKS_PROFILE", "DEFAULT"))
    parser.add_argument("--catalog", default=os.environ.get("WORKSHOP_CATALOG"))
    parser.add_argument("--warehouse", default=os.environ.get("WORKSHOP_WAREHOUSE_ID"))
    args = parser.parse_args()

    me = cli_json(["databricks", "current-user", "me", "-p", args.profile, "-o", "json"])["userName"]
    schema_tools = tools_schema(args.catalog, derive_prefix(me))

    def run_sql(statement, param_name, value):
        body = {"warehouse_id": args.warehouse, "statement": statement, "wait_timeout": "30s",
                "parameters": [{"name": param_name, "value": value}]}
        resp = cli_json(["databricks", "api", "post", "/api/2.0/sql/statements",
                         "-p", args.profile, "--json", json.dumps(body)])
        if resp.get("status", {}).get("state") != "SUCCEEDED":
            raise SystemExit(f"SQL failed: {resp.get('status', {})}")
        columns = [c["name"] for c in resp.get("manifest", {}).get("schema", {}).get("columns", [])]
        return [dict(zip(columns, row)) for row in resp.get("result", {}).get("data_array", [])]

    rows = call_tool(run_sql, schema_tools, args.tool, args.arg)
    print(json.dumps(rows, indent=2, default=str))


if __name__ == "__main__":
    main()
