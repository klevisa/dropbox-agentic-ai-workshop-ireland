---
name: threat-intel
description: Investigate a threat indicator or an account's risk against the governed Databricks threat-intel data. Use when asked to check whether an indicator (URL/IP/domain/file hash) is known-bad, attribute it to a campaign or threat actor, find which internal accounts were exposed to it, or look up an account's risk score and the protective actions taken on it. Triggers on requests like "is this URL malicious", "enrich this hash", "who is behind this IOC", "which accounts saw this indicator", "what's this account's risk", "why was this account suspended".
---

# Threat-intel investigation

Five read-only tools for investigating indicators and account risk. They run as **you** (your
`databricks` CLI profile) against your governed Unity Catalog schemas, so you see exactly what your
identity is permitted to — risk scores and customer names are unmasked only if you're in the
privileged group. All verdicts are local (no external calls).

Run a tool with:

```bash
python3 scripts/threatintel.py <tool> "<arg>"
```

## Tools and when to use each

| Tool | Argument | Use it to… |
|------|----------|------------|
| `enrich_indicator` | a URL / IP / domain / md5 / sha256 | Decide if an artifact is known-bad (URLhaus verdict: threat, url_status, tags, family). |
| `pivot_indicator` | an indicator value **or** an IOC-id | Attribute an indicator to its campaign, threat actor, and sibling indicators. |
| `blast_radius` | an indicator value | Find which internal accounts have this indicator in their incident telemetry. |
| `get_account_risk` | an account id (e.g. `ACC-000888`) | Look up an account's latest risk score, band, and top contributing signal. |
| `get_account_actions` | an account id | See protective actions taken on an account and why. |

## A typical investigation

1. **Triage the artifact** — `enrich_indicator "<url-or-hash>"`. If `query_status` is `no_results`,
   it's unknown to the feed; if `ok`, read the `threat` / `family`.
2. **Attribute it** — `pivot_indicator "<indicator>"` to get the campaign, actor, and siblings.
3. **Scope it** — `blast_radius "<indicator>"` to see which accounts were exposed.
4. **Assess the accounts** — `get_account_risk "<account-id>"` and `get_account_actions "<account-id>"`
   for any account the blast radius surfaced.

Enrichment keys on the **artifact** (a URL or a hash), not on an internal IOC-id — use
`pivot_indicator` when you only have an IOC-id.

## Requirements

- The `databricks` CLI authenticated (set `--profile` or `DATABRICKS_PROFILE`).
- `WORKSHOP_CATALOG` and `WORKSHOP_WAREHOUSE_ID` set (or pass `--catalog` / `--warehouse`).
- `EXECUTE` on `{prefix}_ti_tools.*` (granted to you in Chapter A); your prefix is derived from your
  email automatically.
