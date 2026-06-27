# Chapter B — Skills ↔ MCP spectrum

The same five tools, exposed several ways, to show the **skill → local MCP → hosted-app** spectrum, to
land the **OBO** lesson, and to show what a **real agent** that calls those tools looks like. The tools
wrap the participant's Chapter A UC functions in `{prefix}_ti_tools` (`get_account_risk`,
`get_account_actions`, `pivot_indicator`, `blast_radius`, `enrich_indicator`).

| Version | Where it runs | As whom | Folder |
|---------|---------------|---------|--------|
| **V1 skill** | your terminal / a coding agent | **you** | `skill/` |
| **V2 local MCP** | your machine, typed MCP tools any client/agent can call | **you** | `local-mcp/` |
| **V3 hosted MCP** | a **Databricks App**, callable by agents 24/7 | **the caller, via OBO** | `hosted-mcp/` (deployed by this DAB) |
| **Agent** | a **Model Serving endpoint** — the LLM **chooses** the tools | the agent's SP (declared resources) | `triage-agent/` (deployed by this DAB) |

**One definition, three front doors.** The five tools are defined once in
`hosted-mcp/threatintel_core.py`; the skill, the local MCP, and the hosted app all import it, so the
tools never drift. Each front door only supplies *how the SQL runs* (CLI / SDK / OBO).

**The autonomous agent (`triage-agent/`).** This is the one place in the workshop with a *genuine* agent:
a Mosaic AI `ResponsesAgent` (`agent.py`) given the UC functions as tools, deployed to a **Model Serving
endpoint** by the DAB. You **call it with an incident id**; the LLM **decides itself** which tools to
call and when (a bounded tool-calling loop), then returns the **same JSON as Chapter C's `triage_runner`**
(play + rationale + evidence) — but with no pre-authored runbook. That's the contrast the workshop is
built around: **Chapter C is a governed *workflow* (router + approved plan); this is the agent end (the
LLM owns the control flow)** — flexible, but the discretion isn't bounded by an approved plan. It calls
the UC functions **directly** (not via the MCP), and it's deployed **on-behalf-of-user (OBO)**: since you
can call it ad hoc (e.g. the AI Playground), it runs each tool **as the caller**, so the Chapter A masks
apply to whoever asked — a privileged-group caller sees unmasked evidence, a non-member sees
`***REDACTED***`. `deploy.py` declares this with an MLflow `AuthPolicy` (the LLM as a system resource,
`sql.*` scopes on the caller's behalf); at runtime the agent builds a
`ModelServingUserCredentials` client per request.

**The point: V3 runs on-behalf-of-user.** The app's service principal only fronts ingress; each tool
call runs its SQL as the *caller's* identity (forwarded token), so the Chapter A column masks apply per
caller — a privileged caller sees scores, everyone else sees masked, **through the same MCP**.

## Deploy (the V3 hosted MCP)

1. Edit **`config.yml`** — set `catalog` and `warehouse_id` (no command-line flags).
2. Deploy + start:
   ```bash
   databricks bundle deploy -t dev
   databricks bundle run threatintel_mcp -t dev
   databricks apps list           # note your app: mcp-<your-handle>
   ```

### ⚠️ Admin prereq for OBO (one-time, workspace-wide)
The `user_api_scopes: [sql]` in `databricks.yml` only takes effect once a **workspace admin enables
user authorization** for the workspace. After it's enabled, the app must be **restarted**
(`databricks bundle run threatintel_mcp` again, or Stop→Start in the UI). Until then the app falls back
to its own service principal — which has no access to your schemas — so the tools return nothing.
**OBO is what makes it work.** (Source: Databricks Apps "Configure authorization".)

## Exercise it

- **Hosted MCP (V3) in the AI Playground.** Once the app is running, add it as an MCP tool and chat
  with it — no client script needed:
  1. Open **Playground** (left nav ▸ *Machine Learning ▸ Playground*).
  2. **Tools ▸ Add tools ▸ MCP server**, pick your app (`mcp-<your-handle>`), and add its five tools.
  3. Ask, e.g., *"enrich this URL …"* or *"what's the risk on ACC-000888?"* The Playground passes your
     token, so the MCP runs each tool **as you (OBO)** — masked or unmasked to match your group.
- **V1 skill** — `skill/`: run `python3 scripts/threatintel.py enrich_indicator "<url>"`, or load the
  skill in a coding agent and ask it to investigate.
- **V2 local MCP** — `local-mcp/server.py`: register it with any MCP client (stdio transport).
- **Agent** — deploy it, then query it with an incident id:
  ```bash
  databricks bundle run deploy_triage_agent -t dev     # logs -> registers to UC -> agents.deploy
  ```
  (That job is a thin runner around `triage-agent/deploy.py` — deploying an agent is imperative, so the
  DAB just runs the notebook; you can also open it and `Run All`.) It prints the endpoint; call it with
  `{"input":[{"role":"user","content":"INC-00187"}]}` and it returns the recommendation JSON — the
  autonomous counterpart to Chapter C's workflow.
- **Companion:** `explore.py` shows the UC functions every front door wraps, plus the governance/OBO story.

## Notes
- App names must be lowercase/hyphens, so the app is named with the **hyphen-form** of your handle
  (`mcp-klevis-aliaj`); schemas use the **underscore-form** prefix (`klevis_aliaj_ti_tools`). Both are
  derived from your email automatically.
- The app config (command + env) is declared in `databricks.yml` (`resources.apps…config`), not an
  `app.yaml`.
- `enrich_indicator` keys on the artifact (a URL or a payload hash), not an internal IOC-id — use
  `pivot_indicator` for id-based lookups.
