# Databricks notebook source
# MAGIC %md
# MAGIC # Chapter C · common — shared helpers
# MAGIC Pulled into the other notebooks with `# MAGIC %run ./common`, so the per-participant setup and a
# MAGIC few small utilities are written **once**. `%run` executes this notebook in the caller's
# MAGIC namespace, making every function below available to whatever notebook ran it.

# COMMAND ----------
import json
import re


def derive_prefix(spark, override=""):
    """Per-participant schema prefix: your email local-part with non-alphanumerics turned into '_'.
        klevis.aliaj@databricks.com -> klevis_aliaj
    Pass `override` (the user_prefix widget) to force a specific prefix."""
    if override:
        return override
    me = spark.sql("SELECT current_user()").collect()[0][0]
    return re.sub(r"[^a-zA-Z0-9]", "_", me.split("@")[0]).lower()


class WorkshopContext:
    """The names every Chapter C notebook needs: me, catalog, and the schema names (intel/risk/tools)."""

    def __init__(self, spark, catalog, prefix_override=""):
        self.me = spark.sql("SELECT current_user()").collect()[0][0]
        self.catalog = catalog
        self.prefix = derive_prefix(spark, prefix_override)
        self.intel = f"{self.prefix}_ti_intel"
        self.risk = f"{self.prefix}_ti_risk"
        self.tools = f"{self.prefix}_ti_tools"

    def __repr__(self):
        return (f"WorkshopContext(me={self.me}, catalog={self.catalog}, prefix={self.prefix}, "
                f"schemas={self.intel}/{self.risk}/{self.tools})")


def workshop_context(spark, catalog, prefix_override=""):
    """Build the WorkshopContext for this participant."""
    return WorkshopContext(spark, catalog, prefix_override)


def get_llm_endpoint(workspace, override=""):
    """Pick the foundation-model endpoint that ai_query will call.
    Uses `override` (the model_endpoint widget) if set; otherwise the first available Claude endpoint,
    falling back to Llama. Raises if none is available so you set model_endpoint explicitly."""
    available = {e.name for e in workspace.serving_endpoints.list()}
    candidates = [override] if override else [
        "databricks-claude-sonnet-4-5", "databricks-claude-sonnet-4",
        "databricks-claude-3-7-sonnet", "databricks-meta-llama-3-3-70b-instruct"]
    endpoint = next((c for c in candidates if c in available), None)
    if not endpoint:
        raise ValueError(f"No usable FM endpoint found — set model_endpoint in config.yml. "
                         f"Available (sample): {sorted(available)[:10]}")
    return endpoint


def to_sql_string(text):
    """Turn a Python string into a SQL string literal so it can be embedded in a SQL statement.
    SQL wraps a string literal in single quotes and escapes an inner single quote by doubling it:
        O'Brien  ->  'O''Brien'
    We use this to pass long prompts / JSON schemas into ai_query() inside a SQL statement."""
    return "'" + text.replace("'", "''") + "'"


def extract_json(text):
    """Parse the first {...} JSON object out of an LLM response, tolerating ```json code fences."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    start, end = t.find("{"), t.rfind("}")
    return json.loads(t[start:end + 1])


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# The runbook DSL contract — shared by 01_build_runbook (synthesis/validation) and 02_triage_runner
# (execution). Pure logic with no spark/dbutils, so it's unit-testable by importing this module.
# ─────────────────────────────────────────────────────────────────────────────────────────────────

# Allowed actions = the Chapter A UC functions + the terminal recommend_action (a pure decision).
ACTIONS = {"enrich_indicator", "pivot_indicator", "blast_radius", "get_account_risk",
           "get_account_actions", "recommend_action"}
# The only incident fields a plan may reference.
INCIDENT_REFS = {"$incident.indicator_value", "$incident.account_id", "$incident.indicator_type",
                 "$incident.incident_id"}
# The allowed containment "plays" recommend_action may choose.
ACTION_PLAYS = {"account_suspended", "rate_limited", "forced_password_reset", "mfa_enforced",
                "manual_review", "external_sharing_disabled", "session_revoked", "cleared_no_action"}
_INDICATOR_ACTIONS = {"enrich_indicator", "pivot_indicator", "blast_radius"}
_ACCOUNT_ACTIONS = {"get_account_risk", "get_account_actions"}


def validate_plan(action_plan):
    """Return a list of problems with a plan (empty list = valid)."""
    if not isinstance(action_plan, list) or not action_plan:
        return ["plan is empty or not a list"]
    problems = []
    for i, step in enumerate(action_plan):
        if not isinstance(step, dict):
            problems.append(f"step {i}: not an object"); continue
        action = step.get("action")
        if action not in ACTIONS:
            problems.append(f"step {i}: unknown action {action!r}"); continue
        args = step.get("args") or {}
        if not isinstance(args, dict):
            problems.append(f"step {i}: args not an object"); continue
        for key, value in args.items():
            if isinstance(value, str) and value.startswith("$") and not (
                    value in INCIDENT_REFS or value.startswith("$steps.")):
                problems.append(f"step {i}: unresolvable ref {value!r} for arg {key!r}")
        if action == "recommend_action" and args.get("play") not in ACTION_PLAYS:
            problems.append(f"step {i}: recommend_action play {args.get('play')!r} not allowed")
    return problems


def repair_plan(action_plan):
    """Fix the small, predictable mistakes the LLM tends to make: aliased refs and the wrong arg name
    for indicator- vs account-centric actions. recommend_action is reduced to just its decision
    (play + optional rationale) — every identifier it applies to is known to the runtime."""
    if not isinstance(action_plan, list):
        return []
    ref_aliases = {"$incident.indicator": "$incident.indicator_value",
                   "$incident.account": "$incident.account_id", "$incident.id": "$incident.incident_id"}
    repaired = []
    for step in action_plan:
        if not isinstance(step, dict) or step.get("action") not in ACTIONS:
            continue
        action = step["action"]
        args = dict(step.get("args") or {})
        for key, value in list(args.items()):
            if isinstance(value, str) and value in ref_aliases:
                args[key] = ref_aliases[value]
        if action in _INDICATOR_ACTIONS:
            args.pop("account_id", None)
            args["indicator"] = "$incident.indicator_value"
        elif action in _ACCOUNT_ACTIONS:
            args.pop("indicator", None)
            args["account_id"] = "$incident.account_id"
        elif action == "recommend_action":
            args = {k: v for k, v in args.items() if k in ("play", "rationale")}
        repaired_step = {"action": action, "args": args}
        if step.get("when"):
            repaired_step["when"] = step["when"]
        repaired.append(repaired_step)
    return repaired


def resolve_ref(value, context):
    """Resolve a '$incident.x' / '$steps.action.field' reference against the running context."""
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    current = context
    for part in value[1:].split("."):
        current = current.get(part) if isinstance(current, dict) else None
        if current is None:
            break
    return current


def resolve_args(args, context):
    return {k: resolve_ref(v, context) for k, v in (args or {}).items()}
