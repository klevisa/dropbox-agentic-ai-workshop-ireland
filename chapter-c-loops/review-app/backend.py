"""Chapter C — Review console · BACKEND (identity + data access). No HTML lives here.

This is the "talk to Databricks" half of the app. Every function runs SQL **as the calling user (OBO)**
so Unity Catalog masks + grants apply per identity, and returns plain Python (dicts / lists) for the
front end (`frontend.py`) to render. You can read this file without thinking about markup, and read
`frontend.py` without thinking about Databricks.

OBO (on-behalf-of-user): per request we read the forwarded user token from `X-Forwarded-Access-Token`
and build a `WorkspaceClient` bound to it, so a caller in the participant's `privileged_group` sees
unmasked data and everyone else sees masked — the same mechanism as Chapter B's MCP.

Env (set by the DAB apps resource): WORKSHOP_CATALOG, USER_PREFIX, WORKSHOP_WAREHOUSE_ID,
PRIVILEGED_GROUP. DATABRICKS_HOST is injected by the Apps runtime.
"""
import json
import os

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.service.sql import StatementParameterListItem

# --- config (from the DAB apps resource env) -------------------------------------------------------
CATALOG = os.environ["WORKSHOP_CATALOG"]
PREFIX = os.environ["USER_PREFIX"]                       # underscore-form, matches the Chapter A schemas
WAREHOUSE_ID = os.environ["WORKSHOP_WAREHOUSE_ID"]
PRIVILEGED_GROUP = os.environ.get("PRIVILEGED_GROUP", "")
HOST = os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_WORKSPACE_URL")
if HOST and not HOST.startswith("http"):
    HOST = "https://" + HOST

S_INTEL, S_RISK = f"{PREFIX}_ti_intel", f"{PREFIX}_ti_risk"
RULES_TABLE = f"{CATALOG}.{S_INTEL}.runbook_rules"
RECS_TABLE = f"{CATALOG}.{S_RISK}.triage_recommendations"
INCIDENTS_TABLE = f"{CATALOG}.{S_INTEL}.incidents"
INTEL_TABLE = f"{CATALOG}.{S_INTEL}.indicator_intel"
ACCOUNTS_TABLE = f"{CATALOG}.{S_RISK}.accounts"
RISK_TABLE = f"{CATALOG}.{S_RISK}.account_risk_scores"


def _sp(name, val):
    return StatementParameterListItem(name=name, value=val)


def is_missing_table(e):
    """True if an exception is 'table doesn't exist yet' (vs. a permission/other error) — lets the route
    show a friendly 'run the Chapter C jobs first' message instead of a scary stack."""
    m = str(e).upper()
    return "TABLE_OR_VIEW_NOT_FOUND" in m or "CANNOT BE FOUND" in m


class Caller:
    """The identity behind one request, plus a SQL runner bound to it.

    If the Apps runtime forwarded a user token (OBO), we authenticate as THAT user, so every query runs
    as the caller. Otherwise we fall back to the app's service principal (local dev / OBO not enabled).
    """

    def __init__(self, request):
        self.token = request.headers.get("X-Forwarded-Access-Token")
        self.email = (request.headers.get("X-Forwarded-Email")
                      or request.headers.get("X-Forwarded-Preferred-Username"))
        self.obo = bool(self.token)
        if self.obo and HOST:
            # The app SP injects DATABRICKS_CLIENT_ID/SECRET into the env. A token on top makes the SDK
            # see two auth methods and refuse. Pin auth_type='pat' and null the SP creds so this config
            # uses ONLY the forwarded user token -> every query runs as the caller.
            cfg = Config(host=HOST, token=self.token, auth_type="pat", client_id=None, client_secret=None)
            self.w = WorkspaceClient(config=cfg)
        else:
            self.w = WorkspaceClient()
        self._is_privileged = None

    @property
    def identity(self):
        return self.email or ("app-SP (no OBO)" if not self.obo else "unknown-user")

    @property
    def is_privileged(self):
        """Membership in the participant's privileged_group. Gates whether the Approve/Reject buttons
        render — but the UPDATE itself still runs as the caller, so UC MODIFY has the final say."""
        if self._is_privileged is None:
            try:
                me = self.w.current_user.me()
                if not self.email and me.user_name:
                    self.email = me.user_name
                groups = {(g.display or "").lower() for g in (me.groups or [])}
                self._is_privileged = PRIVILEGED_GROUP.lower() in groups
            except Exception:
                self._is_privileged = False
        return self._is_privileged

    def query(self, statement, params=None):
        """Run one statement AS THIS CALLER; return rows as a list of dicts. Raises on non-SUCCEEDED."""
        kwargs = dict(warehouse_id=WAREHOUSE_ID, statement=statement, wait_timeout="50s")
        if params:
            kwargs["parameters"] = params
        r = self.w.statement_execution.execute_statement(**kwargs)
        state = r.status.state.value if r.status and r.status.state else "UNKNOWN"
        if state != "SUCCEEDED":
            err = getattr(r.status, "error", None)
            raise RuntimeError(getattr(err, "message", err) if err else state)
        cols = [c.name for c in r.manifest.schema.columns] if (
            r.manifest and r.manifest.schema and r.manifest.schema.columns) else []
        rows = r.result.data_array if (r.result and r.result.data_array) else []
        return [dict(zip(cols, row)) for row in rows]


# --- data access: one function per thing a screen needs --------------------------------------------
def proposed_rules(caller):
    """Screen 1: the rules awaiting review (status='proposed')."""
    return caller.query(
        f"""SELECT rule_id, name, scenario_hint, symptom_pattern, action_plan, rationale,
                   evidence_count, confidence, status, created_by, created_at
            FROM {RULES_TABLE}
            WHERE status = 'proposed'
            ORDER BY confidence DESC NULLS LAST, created_at DESC""")


def promote_rule(caller, rule_id, new_status):
    """The governed write: UPDATE runbook_rules AS THE CALLER (OBO). The UC MODIFY grant decides whether
    it succeeds — the buttons are only UX."""
    caller.query(
        f"""UPDATE {RULES_TABLE}
            SET status = :st, reviewed_by = current_user(), reviewed_at = current_timestamp()
            WHERE rule_id = :rid""",
        params=[_sp("st", new_status), _sp("rid", rule_id)])


def triage_feed(caller, limit=100):
    """Screen 2: recent triage_recommendations (the triage agent's output)."""
    return caller.query(
        f"""SELECT recommendation_id, incident_id, account_id, indicator_value,
                   matched_rule_id, recommended_play, rationale, recommended_at
            FROM {RECS_TABLE}
            ORDER BY recommended_at DESC NULLS LAST
            LIMIT {int(limit)}""")


def incident(caller, incident_id):
    """Screen 3, part 1: one incident. Returns the row dict, or None if not found."""
    rows = caller.query(
        f"""SELECT incident_id, created_at, narrative, indicator_value, indicator_type,
                   account_id, status, scenario_label
            FROM {INCIDENTS_TABLE} WHERE incident_id = :id LIMIT 1""",
        params=[_sp("id", incident_id)])
    return rows[0] if rows else None


def latest_recommendation(caller, incident_id):
    """Screen 3, part 2: the most recent recommendation for an incident, or None. The stored `evidence`
    JSON is deserialized here so the front end receives a plain dict {action: [rows]}."""
    rows = caller.query(
        f"""SELECT matched_rule_id, recommended_play, rationale, evidence, recommended_at
            FROM {RECS_TABLE} WHERE incident_id = :id
            ORDER BY recommended_at DESC NULLS LAST LIMIT 1""",
        params=[_sp("id", incident_id)])
    if not rows:
        return None
    rec = rows[0]
    try:
        rec["evidence"] = json.loads(rec["evidence"]) if rec.get("evidence") else {}
    except (TypeError, ValueError):
        rec["evidence"] = {}
    return rec


def indicator_intel(caller, indicator):
    """Screen 3, part 3: URLhaus verdict for the indicator, or None."""
    rows = caller.query(
        f"""SELECT family, threat, tags, url_status, urlhaus_type, host, urlhaus_reference
            FROM {INTEL_TABLE} WHERE indicator_value = :v LIMIT 1""",
        params=[_sp("v", indicator)])
    return rows[0] if rows else None


def account_risk(caller, account):
    """Screen 3, part 4: the account + its latest risk score, or None. customer_name + risk_score come
    back masked unless the caller is privileged (the masks live on the table, enforced per caller)."""
    rows = caller.query(
        f"""SELECT a.account_id, a.customer_name, a.segment, a.plan_tier, a.region, a.status,
                   s.risk_score, s.risk_band, s.score_date, s.top_signal
            FROM {ACCOUNTS_TABLE} a
            LEFT JOIN (
              SELECT account_id, risk_score, risk_band, score_date, top_signal,
                     ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY score_date DESC) rn
              FROM {RISK_TABLE}
            ) s ON s.account_id = a.account_id AND s.rn = 1
            WHERE a.account_id = :a LIMIT 1""",
        params=[_sp("a", account)])
    return rows[0] if rows else None
