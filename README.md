# Databricks Agentic AI Apps Workshop

A hands-on workshop (~20 participants) on building **governed, agentic AI applications on Databricks**.
Everyone works through the same scenario — a security team's threat-intel app — in their **own
Databricks workspace** under their own schema prefix. No laptop required.

The transferable patterns:
- **Identity-based governance** — column masks + governed views keyed on **group membership**: members of
  a chosen group see unmasked data, everyone else sees masked.
- **UC functions as governed tools** — per-caller masking via invoker rights; one tool, two views.
- **Genie** over governed data (a privileged space over base tables + an open space over CS-safe views).
- **The skills → MCP spectrum** — the same tool logic as a skill, a local MCP, and a hosted (on-behalf-of-user) **OBO** MCP.
- **Agent vs. workflow** — the same tools used two ways: a **genuine agent** that chooses its own tools
  (Chapter B `agent.py`) vs. a **governed workflow** that runs an approved plan (Chapter C) — and why
  you'd pick the bounded one in production.
- **Agentic loops + human gate** — an LLM synthesizes a runbook, a human approves it, an autonomous
  loop applies it; runtime observability via **MLflow tracing**.
- **OBO vs. SP** — when a component honors the caller (OBO) vs. runs as its own identity (SP).

## Layout

```
agentic-ai-apps-workshop/
  README.md            # this
  deploy_workshop.py   # in-workspace runner: deploys + runs each chapter's DAB (no laptop/CLI)
  teardown_workshop.py # in-workspace runner: bundle destroy + drop schemas + delete Genie spaces + endpoint/experiment
  _deploy_lib.py       # shared CLI-bootstrap/auth helper for the two runners above
  design/              # workshop-participant-flow.md — who does what, as whom (OBO/SP, who-sees-what)
  chapter-a-foundation/   # DAB A — schemas, data, governance, UC-function tools, Genie
  chapter-b-spectrum/     # DAB B — hosted OBO MCP app, local MCP, skill (one shared core) + an LLM agent
  chapter-c-loops/        # DAB C — runbook-builder + triage-runner jobs + OBO Review UI app
```

Each chapter = one DAB bundle (`databricks.yml` + `src/`) **plus** a companion `explore` notebook.
**DABs deploy; notebooks interact.**

## Who runs what (no one is an admin)

The workshop runs entirely in the **participants' own workspace** — **nobody in the workshop has admin
rights.** Two roles, neither of which deploys anything privileged:

- **Instructor** — runs the session and **coordinates with the account/workspace admin** (a separate
  person) on the one-time setup below. The instructor has no special permissions in the workshop itself.
- **Participants (~20)** — each clones the repo and deploys all three chapters into their own schemas.
  They are ordinary users; **no participant is a "privileged user."**

### One-time setup — the account/workspace admin (not the instructor)

Before the session, the admin does this **by hand, once**:
1. **Creates the shared catalog** (e.g. `klevis_demo_catalog` / `workshop_catalog`).
2. Grants **`USE CATALOG` + `CREATE SCHEMA`** on it to the workshop participants — or simply to
   **`account users`** if that's easier.
3. Confirms participants can reach a **SQL warehouse**.
4. (For Chapters B & C) Enables **workspace user authorization (OBO)** so the apps can forward the
   caller's identity.
5. (For the Chapter B **agent**) Enables **on-behalf-of-user authentication for Model Serving** — a
   Beta toggled at **Settings ▸ Previews** (separate from #4). The triage agent runs its tools as the
   *caller* (OBO); without this preview it falls back to its own service principal, which has no data
   grants, and its tool calls fail. (Verified live 2026-06-25: with the preview on, the agent's evidence
   comes back unmasked for a privileged caller.)

That's the entire shared layer — no instructor deploy, no service principal, no seed volume. Everything
else, each participant builds in their own schemas.

### "Privileged" is just group membership — validate it with a partner

Governance is keyed on `is_account_group_member('<group>')`, **not** on any pre-anointed person. **No one
is inherently privileged.** The working assumption — which makes the security/governance story
demonstrable — is that **participants belong to a healthy mix of groups**. Validation is a **pairwise
exercise**:

> Say user **A** is in `klevis_seceng` and user **B** is in `klevis_dart`. A deploys their slice with
> `privileged_group = klevis_seceng`, then grants B read access. **A** (a member) sees unmasked data;
> **B** (not a member) sees `***REDACTED***` / banded values through the *same* tables, functions, MCP,
> and Genie space. Swap roles — B deploys with `privileged_group = klevis_dart` and grants A — to see it
> from the other side.

So `privileged_group` just names **whichever group you're in** for *your* deployment; pick one where a
partner is **not** a member, so the two of you can prove the masks hold across every front door.

## Deploy order (each participant)

In dependency order — deploy a chapter, explore it, deploy the next:
1. **Chapter A — Foundation** → explore: masked vs. unmasked, query data, ask Genie.
2. **Chapter B — Skills↔MCP spectrum** → explore: call UC functions, run the V1 skill, add the hosted OBO MCP in the AI Playground.
3. **Chapter C — Agent loops** → explore: runbook synthesis → **approve (human gate)** → triage → recommendations.

Each chapter needs to be **deployed *and* run** (the bundle creates jobs/apps; you still have to run the
build job, start the app, …). The workspace "Deploy" button only deploys, so use one of these two
no-laptop paths — both run the same `databricks bundle` commands as you:

**Path 1 — the in-workspace notebook (recommended, no CLI to learn).** Open **`deploy_workshop.py`** at
the repo root, fill the widgets (`catalog`, `privileged_group`, `warehouse_id`), pick a **step**, and
Run All. It bootstraps the CLI, authenticates as you, and deploys + runs that chapter. Step through:
`A · Foundation` → `B · Spectrum` → `C · Propose` → **approve rules** → `C · Triage`. Widget values are
passed as bundle `--var` overrides, so there's no `config.yml` to edit. At the end, run
**`teardown_workshop.py`**.

**Path 2 — the web terminal.** The in-workspace web terminal has the CLI pre-installed and
pre-authenticated. Edit each chapter's `config.yml`, then:

```bash
cd chapter-a-foundation
# edit config.yml — set catalog, privileged_group, warehouse_id (no command-line flags)
databricks bundle deploy -t dev
databricks bundle run chapter_a_foundation -t dev
# then open chapter-a-foundation/explore.py
```

> Note: the CLI is blocked from running *inside a serverless notebook's `%sh`* except the web terminal,
> so `deploy_workshop.py` downloads the public CLI to `~/bin` and runs that. If GitHub egress is blocked
> in your workspace, use Path 2.

Cross-chapter references are **by UC object name** (no native cross-bundle wiring) — order is enforced
by these instructions.

## Per-participant config

If you use the **notebook** path the values are widgets; if you use the **web terminal**, set them in
each chapter's `config.yml`. Either way, three values:

| Value | Meaning | Example |
|----------|---------|---------|
| `catalog` | The pre-created shared catalog (the admin made it) | `klevis_demo_catalog` |
| `privileged_group` | A group **you belong to** — its members see unmasked data + the privileged Genie space; everyone else sees masked. Pick one where a partner is *not* a member, to validate the two views. | `klevis_seceng` |
| `warehouse_id` | A SQL warehouse you can use | `abcd1234efgh5678` |

Your schema prefix is **auto-derived** from your email (`klevis.aliaj@…` → `klevis_aliaj`); schemas land
under it: `{user}_ti_intel` / `_ti_risk` / `_ti_cs` / `_ti_tools`.

## Build status

- ✅ **Chapter A — Foundation**: schemas + per-user seed volume + data + group-keyed masks + 5
  UC-function tools (incl. **table-backed** enrich) + Open/Privileged Genie.
- ✅ **Chapter B — Skills↔MCP spectrum**: the **hosted OBO MCP app** (`user_api_scopes: [sql]`, wraps the
  Chapter A UC functions, runs each call as the caller), plus a **local MCP** and a real **skill** — all
  three importing one shared `threatintel_core.py` — and a **genuine LLM agent** (`agent.py`, a
  `ResponsesAgent` with a bounded tool-calling loop) that calls the UC functions directly and outputs a
  triage recommendation, the autonomous counterpart to Chapter C's workflow. The agent is deployed
  **on-behalf-of-user (OBO)** — invoked ad hoc (e.g. the AI Playground), it runs its tools as the caller,
  so the masks apply to whoever asked. Exercise the hosted app + the agent in the **AI Playground**. Needs
  workspace user authorization enabled (admin, one-time).
- ✅ **Chapter C — Agent loops**: `runbook_builder` (two-stage `ai_query` synthesis → PROPOSED rules),
  the human approval gate (in `explore.py` **or** the OBO **`review_console`** app — Approve/Reject +
  triage feed + incident drill), and `triage_runner` (matches incidents → runs each rule's DSL plan via
  the Chapter A UC functions → writes recommendations). Jobs are serverless; the Review UI runs SQL
  through a warehouse (OBO). Accuracy-vs-hidden-label payoff in `explore.py`/the app.
- ✅ **Validated end-to-end** — full A→B→C deploy + run, governance two-view (direct SQL, hosted MCP OBO,
  review-app OBO), and triage accuracy, exercised live via `tests/e2e/`.

See `design/workshop-participant-flow.md` for who-does-what (as whom) and the OBO-vs-SP / who-sees-what
details.
