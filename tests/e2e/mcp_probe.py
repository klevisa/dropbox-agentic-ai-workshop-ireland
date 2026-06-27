#!/usr/bin/env python3
"""Call the hosted OBO MCP app as a given profile's identity and print one tool's result as JSON.

Run with the venv python that has `mcp` installed (tests/e2e/.venv). run.py invokes this once per SP for
the MCP OBO differential: the app forwards the caller's token, so the same tool returns masked vs unmasked
depending on the SP's group. Token is minted via the M2M client-credentials grant (stdlib).
"""
import argparse
import asyncio
import base64
import configparser
import json
import os
import urllib.request

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def m2m_token(profile):
    cfg = configparser.ConfigParser()
    cfg.read(os.path.expanduser("~/.databrickscfg"))
    s = cfg[profile]
    host = s["host"].rstrip("/")
    req = urllib.request.Request(f"{host}/oidc/v1/token",
                                 data=b"grant_type=client_credentials&scope=all-apis", method="POST")
    basic = base64.b64encode(f"{s['client_id']}:{s['client_secret']}".encode()).decode()
    req.add_header("Authorization", f"Basic {basic}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["access_token"]


async def call_tool(url, token, tool, args):
    """Return ALL result rows as a JSON array string. FastMCP emits one content block per row, so we
    parse and collect them (a single-row tool would otherwise look like a bare object, not a list)."""
    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            rows = []
            for c in (res.content or []):
                try:
                    rows.append(json.loads(c.text))
                except (ValueError, AttributeError):
                    pass
            return json.dumps(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-url", required=True, help="https://mcp-<handle>...databricksapps.com/mcp")
    ap.add_argument("--profile", required=True, help="M2M SP profile in ~/.databrickscfg")
    ap.add_argument("--tool", default="get_account_risk")
    ap.add_argument("--arg-name", default="account_id")
    ap.add_argument("--arg-value", required=True)
    a = ap.parse_args()
    token = m2m_token(a.profile)
    print(asyncio.run(call_tool(a.app_url, token, a.tool, {a.arg_name: a.arg_value})))


if __name__ == "__main__":
    main()
