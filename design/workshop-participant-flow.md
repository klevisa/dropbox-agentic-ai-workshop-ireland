# Participant flow — Agentic AI Apps Workshop

How the **Agentic AI Apps Workshop** is delivered: what the **account/workspace admin** provisions once,
what **each participant** deploys, who can **see what**, and which components run **on-behalf-of-user
(OBO)** vs as a **service principal (SP)**. Everyone runs the same scenario (a security team's
threat-intel app) in **their own workspace**, under their own schema prefix.

---

## 0. Cast of characters

| Role | Who | Count | Admin? |
|------|-----|-------|--------|
| **Account/workspace admin** | A platform admin (separate from the workshop) | 1 | Yes — does the one-time setup below |
| **Instructor** | Runs the session | 1 | **No** — coordinates the setup *with* the admin; has no special workshop permissions |
| **Participant** | Workshop attendee | ~20 | No — an ordinary user |
| **App SP** | Auto-created per Databricks App (the MCP + Review apps) | ~2 per participant | n/a (hosts only; no data privileges) |

> **No one in the workshop is privileged.** There is no shared "seceng" persona and no anointed user.
> "Privileged" is purely **group membership relative to a given deployment** — see §3.

---

## 1. One-time setup — the account/workspace admin (not the instructor)

The workshop runs in the **participants' own workspace**, and **nobody in the workshop has admin rights**.
The **instructor coordinates with the account/workspace admin** (a separate platform owner) to get this
done **by hand, once, before the session**. There is no instructor deploy.

| # | What | Why it needs the admin |
|---|------|------------------------|
| 1 | **Create the shared catalog** (its name is passed to every chapter as `var.catalog`) | Catalog create = metastore admin |
| 2 | **Grant `USE CATALOG` + `CREATE SCHEMA`** on it to the workshop participants — or just to **`account users`** | Granting on the catalog |
| 3 | Confirm participants can reach a **SQL warehouse** | — |
| 4 | **Enable workspace user authorization (OBO)** (for Chapters B & C apps) | Workspace-level setting |
| 5 | **Enable on-behalf-of-user authentication for Model Serving** (Beta, **Settings ▸ Previews**) — for the Chapter B agent's OBO | Workspace preview toggle (separate from #4) |

> That's the **entire** shared layer — **no mock service, no UC connection, no service principal, no seed
> volume, no per-user catalog.** The seed rides in the Chapter A bundle; participants use their own SQL
> warehouse; `enrich_indicator` is table-backed (no external call). The only shared object is the catalog.

---

## 2. "Privileged" is group membership — validate it with a partner

Every governance decision (column masks, the privileged Genie space, the Review app's Approve/Reject
gate) keys off **`is_account_group_member('<group>')`** — never a hardcoded persona. So **privilege is
relative to each deployment**, and proving it works is a **two-person exercise**.

The working assumption, which makes the security story demonstrable: **participants belong to a healthy
mix of account groups.** Pair up across that mix.

```
User A ∈ klevis_seceng                    User B ∈ klevis_dart  (NOT in klevis_seceng)
  deploys with privileged_group=klevis_seceng
  GRANTs B read on {A}_ti_* schemas
        │
        ├─ A queries (member)      ───►  sees UNMASKED  (customer_name, numeric risk_score, base tables)
        └─ B queries (non-member)  ───►  sees MASKED     (***REDACTED***, NULL/banded, CS views only)
                                          …through the SAME tables, UC functions, MCP, and Genie.
  (swap roles: B deploys with privileged_group=klevis_dart, grants A — A is now the non-member)
```

So when a participant sets `privileged_group`, they pick **a group they belong to** — ideally one where a
**partner is *not* a member**, so the two of them can watch the masks hold (and fail-closed) across every
front door. No participant ever needs admin rights to do this.

---

## 3. Participant — what each person deploys

Each participant clones the repo as a **Git folder in their own workspace** and deploys **3 DABs in
dependency order**, exploring each in a **companion notebook** before moving on. **DABs deploy;
notebooks interact.** No laptop required.

> **How they deploy (no laptop):** either the repo-root **`deploy_workshop.py`** notebook (fill widgets,
> pick a step, Run All — it bootstraps the public CLI, authenticates via the notebook context token, and
> runs `bundle deploy`/`bundle run` for that chapter) **or** the in-workspace **web terminal** (CLI
> pre-installed + pre-authenticated). At the end they run **`teardown_workshop.py`**. The serverless
> notebook's *pre-installed* CLI is guarded (web-terminal-only), so the notebook downloads the public
> CLI to `~/bin`; if GitHub egress is blocked, use the web terminal.

### DAB A — Foundation  *(deploy 1st)*
Builds the participant's own world inside the shared catalog:
- Their 4 schemas: `{catalog}.{user}_ti_intel` / `_ti_risk` / `_ti_cs` / `_ti_tools`
- Per-user seed volume (CSVs ride in the bundle) + data load
- **Governance**: column masks + CS-safe views keyed on `is_account_group_member('${privileged_group}')`
- The **5 UC functions** (`get_account_risk`, `get_account_actions`, `pivot_indicator`, `blast_radius`, `enrich_indicator`)
- **2 Genie spaces**: an **open** space (cs views only) + a **privileged** space (intel + risk base tables)

→ *Companion notebook:* masked-vs-unmasked governance (pair up per §2), query data, ask Genie.

### DAB B — Skills ↔ MCP spectrum  *(2nd)*
- Deploys the **V3 hosted MCP app** (wraps DAB-A's UC functions) → **OBO**: honors the caller's identity (the app SP only hosts it)
- A **genuine LLM agent** (`agent.py`) deployed to a Model Serving endpoint **on-behalf-of-user (OBO)** — its tools run as the caller
- Bundles the **V1 skill** + **V2 local MCP** (V1 shown via the in-workspace coding agent; V2 read-only reference)

→ *Companion notebook:* call the UC functions; run the V1 skill via the in-workspace agent; query the agent; call the hosted MCP.

### DAB C — Agent loops  *(3rd)*
- Deploys 2 jobs (**runbook-builder**, **triage-runner**) + the **OBO Review UI app**

→ *Companion notebook:* run runbook synthesis → **approve (human gate)** → run triage → see recommendations.

> Cross-DAB references are **by UC object name** — there's no native cross-bundle wiring, so **order is
> enforced by the instructions**, not the tooling. Deploy A → explore → B → explore → C.

---

## 4. Who can see what — member vs non-member

Governance is **identity-based**, enforced by Unity Catalog column masks + governed views, and it follows
the caller through **every** front door (Genie, UC function, MCP, agent, notebook) because the masks live
on the data, not the app — and the OBO components forward the caller's identity rather than substituting
their own.

| Data / capability | Member of the deployment's `privileged_group` | Non-member (e.g. a partner in a different group) |
|-------------------|-----------------------------------------------|--------------------------------------------------|
| `{user}_ti_risk` numeric **risk_score** | **Sees the score** | Masked / banded only |
| Customer identifiers (`customer_name`, etc.) | **Unmasked** | Masked |
| `{user}_ti_intel` base tables (indicators, campaigns, actors, incidents) | **Full access** | Via CS-safe views only |
| `{user}_ti_cs` CS-safe views | Yes | **Yes — all the non-member gets** |
| Privileged Genie space (intel + risk base tables) | Yes | No |
| Open Genie space (cs views only) | Yes | **Yes** |

Two practical notes:
- The **same UC function** returns different cells to different callers because it runs with **invoker
  rights** — the mask evaluates against *whoever called it*. One function, two views.
- **One axis only: in the group vs. not.** Every tier decision keys off the same
  `is_account_group_member('${privileged_group}')` test. (Runbook guidance that used to live in a
  `search_knowledge` RAG tool is codified into the agent's instructions — Vector Search was dropped.)

---

## 5. OBO vs SP — which identity each piece carries

The heart of the demo's story: **the same capability, run as different identities, gets governed
differently.**

| Component | Runs as | OBO or SP | Why |
|-----------|---------|-----------|-----|
| **Notebooks** (the companion notebooks) | The **participant** | n/a (direct user identity) | Interactive exploration as themselves |
| **UC functions** (the 5 tools) | The **caller** (invoker rights) | n/a — inherits caller | Per-caller masking; the whole point |
| **Genie spaces** | The **querying user** | n/a — inherits caller | Governance follows the asker |
| **V1 skill** | The **participant** (via in-workspace coding agent) | runs as user | Laptop-style front door, in-workspace |
| **V2 local MCP** | (reference only) | would run as user | Shown as code; not executed in workshop |
| **V3 hosted MCP app** | Hosting = app SP (the door); **data access = the caller**, via OBO | **OBO** | The app SP only fronts ingress; OBO re-mints the **caller's** token so the MCP honors *their* grants & masks — never the app's |
| **Agent** (`triage-agent/`, Model Serving) | **The caller**, via OBO (only the LLM call uses the agent's own SP) | **OBO** | The one genuine agent: a `ResponsesAgent` whose LLM chooses the UC-function tools itself, then runs them **as the caller** so the masks apply to whoever asked — it's meant to be invoked ad hoc (the Playground, a notebook). `deploy.py` declares an MLflow `AuthPolicy`: LLM as a `SystemAuthPolicy` resource, `sql.*` `UserAuthPolicy` scopes for the caller. Autonomous counterpart to the `triage-runner` workflow |
| **runbook-builder job** | Job owner (an **SP**) | **SP** | Batch `ai_query` synthesis; runs unattended as its own identity |
| **triage-runner job** | Job owner (an **SP**) | **SP** | Autonomous loop; owns the single governed **write** to `triage_recommendations` |
| **Review UI app** (Chapter C, `review-<handle>`) | The **logged-in human**, via OBO | **OBO** | The reviewer's identity drives masking + whether Approve/Reject shows |

### The OBO vs SP contrast, in one sentence each
- **OBO path (V3 MCP app, the triage agent, + Review UI):** the front doors a *caller* reaches through.
  None of them use their own grants for data — the caller's identity is forwarded (the MCP/Review app
  re-mint the **caller's** token, `X-Forwarded-Access-Token`; the agent builds a
  `ModelServingUserCredentials` client per request) — so masks evaluate against whoever called: a human
  reviewer, a Playground user, an interactive agent, **or** an SP job. A member sees scores; a non-member
  sees masked. *Grant the user, and the surface honors it.*
- **SP path (the jobs):** the autonomous side. The runbook-builder and triage-runner run unattended as
  **their own service principals**, with the grants you gave those SPs. The triage-runner is also itself a
  *caller* of the OBO MCP — so even the autonomous loop is governed as the SP it runs as.

> The app SP exists only to **host** the app; it carries **no data privileges of its own**. OBO re-mints
> for both human and SP callers, so a single OBO MCP serves the interactive demo *and* the autonomous
> triage loop while keeping per-caller enforcement.

---

## 6. End-to-end timeline

```
ACCOUNT/WORKSPACE ADMIN (before workshop — coordinated by the instructor)
  └─ by hand: CREATE CATALOG <name> + grant USE_CATALOG/CREATE_SCHEMA to participants
     + enable workspace user authorization (OBO).  (no instructor deploy)

PARTICIPANT (in their own workspace, no admin)
  1. git-clone repo → workspace Git folder
  2. set config: catalog + privileged_group = <a group I'm in> + warehouse_id
  3. deploy DAB A ──► explore notebook A   (masked vs unmasked — pair with a partner in a diff group)  [as me]
  4. deploy DAB B ──► explore notebook B   (UC fns, V1 skill, agent, hosted MCP)   [MCP=OBO: acts as caller]
  5. deploy DAB C ──► explore notebook C:
        runbook-builder job  ──►  HUMAN APPROVES rules  ──►  triage-runner job      [jobs run as SP]
  6. teardown_workshop.py at the end.

GOVERNANCE VALIDATION (pairwise, §2)
  └─ A deploys with privileged_group=klevis_seceng, grants B; A sees unmasked, B (klevis_dart) sees masked.
     Swap to see it from the other side. Same masks across direct SQL, the OBO MCP, and the Review app.
```

---

## 7. Quick reference — what's shared vs per-user

| | Shared (admin, once) | Per-user (each participant) |
|---|---|---|
| Catalog | `workshop_catalog` | — |
| Schemas | — | `{user}_ti_intel` / `_ti_risk` / `_ti_cs` / `_ti_tools` |
| Data | — (seed rides in the Chapter A bundle) | per-user seed volume + loaded tables |
| Enrichment | — | table-backed `enrich_indicator` (reads own `indicator_intel`) |
| UC functions | — | own 5 functions |
| Genie | — | own 2 spaces |
| MCP app + agent | — | own V3 OBO app (app SP hosts only) + agent endpoint |
| Jobs | — | own runbook-builder + triage-runner |
| Review UI | — | own OBO `review-<handle>` app |

**Not in the workshop at all:** any secret scope, the mock URLhaus service, the UC connection + service
principal + OAuth, and Vector Search / a knowledge corpus — `enrich_indicator` is a table-backed UC
function, so the only shared object is the catalog.
