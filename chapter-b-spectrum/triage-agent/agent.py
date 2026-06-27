"""Chapter B — Triage agent (Mosaic AI `ResponsesAgent`, models-from-code).

Just the agent. Given an incident id, it looks the incident up, then **investigates by calling the
Chapter A UC functions as tools** — the LLM decides which tools to call and when (a bounded tool-calling
loop) — and returns a JSON recommendation (play + rationale + evidence): the same shape Chapter C's
`triage_runner` produces, but with no pre-authored plan. It's the autonomous counterpart to that
governed workflow.

The tools are the UC functions, **called directly** via the SQL Statements API (not the MCP). The agent
runs them **on-behalf-of the caller (OBO)**: because it can be invoked ad hoc (e.g. from the AI
Playground), it should inherit *whoever asked* — so the Chapter A masks apply to that user, not to a
fixed service principal. The SQL is executed through a `WorkspaceClient` built with
`ModelServingUserCredentials()` (per request — see `predict`); the LLM call stays on the agent's own
(system) identity. Logging, registration, and deployment to a Model Serving endpoint live in `deploy.py`
(run by the DAB), which declares the OBO scopes.

Config is supplied at log time via `model_config` (see deploy.py): catalog, prefix, llm_endpoint,
warehouse_id. The `development_config` below is only the fallback for local testing.
"""
import json
import re
from typing import Generator

import mlflow
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem
from mlflow.deployments import get_deploy_client
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest, ResponsesAgentResponse, ResponsesAgentStreamEvent,
)

CFG = mlflow.models.ModelConfig(development_config={
    "catalog": "", "prefix": "", "warehouse_id": "",
    "llm_endpoint": "databricks-claude-sonnet-4-5"})
CATALOG = CFG.get("catalog")
PREFIX = CFG.get("prefix")
LLM_ENDPOINT = CFG.get("llm_endpoint")
WAREHOUSE_ID = CFG.get("warehouse_id")
S_TOOLS = f"{CATALOG}.{PREFIX}_ti_tools"
S_INTEL = f"{CATALOG}.{PREFIX}_ti_intel"

# Safety bound on the agent loop: the most tools any investigation needs is ~4 (enrich → pivot →
# blast_radius → get_account_risk), so 6 leaves room for that plus the final answer while guaranteeing
# the loop can't run forever if the model keeps calling tools without concluding.
MAX_TOOL_TURNS = 6

PLAYS = ["account_suspended", "rate_limited", "forced_password_reset", "mfa_enforced",
         "manual_review", "external_sharing_disabled", "session_revoked", "cleared_no_action"]

# tool name -> (UC function, SQL parameter name). These are the Chapter A UC functions, called directly.
TOOLS = {
    "enrich_indicator": ("enrich_indicator", "ind"),
    "pivot_indicator": ("pivot_indicator", "ind"),
    "blast_radius": ("blast_radius", "ind"),
    "get_account_risk": ("get_account_risk", "acct"),
    "get_account_actions": ("get_account_actions", "acct"),
}
TOOL_DESCRIPTIONS = {
    "enrich_indicator": "URLhaus verdict for a URL/IP/domain/hash (query_status, threat, url_status, tags, family).",
    "pivot_indicator": "Pivot an indicator to its campaign, threat actor, sibling indicators, and family/threat/tags.",
    "blast_radius": "Which internal accounts have this indicator in their incident telemetry, with each account's risk band.",
    "get_account_risk": "Latest risk score, band, and top contributing signal for an account.",
    "get_account_actions": "Protective actions already taken on an account and why.",
}


def _tool_specs():
    """OpenAI-style function-tool specs the LLM uses to decide which tool to call."""
    specs = []
    for name, (_fn, param) in TOOLS.items():
        arg_desc = ("an account id like ACC-000888" if param == "acct"
                    else "an indicator value (URL/IP/domain/hash) or IOC-id")
        specs.append({"type": "function", "function": {
            "name": name, "description": TOOL_DESCRIPTIONS[name],
            "parameters": {"type": "object", "required": [param],
                           "properties": {param: {"type": "string", "description": arg_desc}}}}})
    return specs


TOOL_SPECS = _tool_specs()
SYSTEM_PROMPT = (
    "You are a SOC triage agent. You are given one security incident. Investigate it using the tools — "
    "YOU decide which tools to call and in what order. Gather enough evidence, then recommend exactly ONE "
    "containment play from: " + ", ".join(PLAYS) + ". "
    'Reply with ONLY a JSON object: {"recommended_play": "<one play>", "rationale": "<one sentence>"}.')


class TriageAgent(ResponsesAgent):
    def __init__(self):
        self._deploy_client = None

    def _llm(self):
        # The LLM is called on the agent's OWN (system) identity — it's declared in deploy.py's
        # SystemAuthPolicy. Safe to cache: it's the same identity for every request.
        if self._deploy_client is None:
            self._deploy_client = get_deploy_client("databricks")
        return self._deploy_client

    def _user_client(self):
        """A WorkspaceClient that runs SQL **as the invoking user** (OBO), so the Chapter A masks apply
        to whoever called the agent. Built FRESH per request — never cached on the instance — because
        the caller's identity is only known at query time and the agent object is reused across callers;
        caching would leak one user's credentials to the next. Falls back to default creds when run
        outside Model Serving (local dev), where `databricks_ai_bridge` / the invoker context is absent."""
        try:
            from databricks_ai_bridge import ModelServingUserCredentials
            return WorkspaceClient(credentials_strategy=ModelServingUserCredentials())
        except Exception:
            return WorkspaceClient()

    def _query(self, client, statement, param=None, value=None):
        """Run a SQL statement on the configured warehouse as `client`'s identity; rows as list of dicts.
        RAISES on a non-SUCCEEDED state — a failed tool call must surface, not silently return [] (an
        empty result would masquerade as 'no evidence' and hide a permission/warehouse problem)."""
        params = [StatementParameterListItem(name=param, value=value)] if param else None
        r = client.statement_execution.execute_statement(
            warehouse_id=WAREHOUSE_ID, statement=statement, parameters=params, wait_timeout="30s")
        state = r.status.state.value if r.status and r.status.state else "UNKNOWN"
        if state != "SUCCEEDED":
            err = getattr(r.status, "error", None)
            raise RuntimeError(f"SQL {state}: {getattr(err, 'message', '') if err else ''}")
        cols = [c.name for c in r.manifest.schema.columns] if r.manifest and r.manifest.schema else []
        rows = r.result.data_array if (r.result and r.result.data_array) else []
        return [dict(zip(cols, row)) for row in rows]

    def _lookup_incident(self, client, incident_id):
        rows = self._query(
            client,
            f"SELECT incident_id, account_id, indicator_value, indicator_type, narrative "
            f"FROM {S_INTEL}.incidents WHERE incident_id = :id LIMIT 1", "id", incident_id)
        return rows[0] if rows else None

    def _run_tool(self, client, name, args):
        fn, param = TOOLS[name]
        return self._query(client, f"SELECT * FROM {S_TOOLS}.{fn}(:{param})", param, str(args.get(param)))

    def _incident_id_from(self, request_input):
        """The caller passes the incident id as the user message; tolerate a sentence around it."""
        text = ""
        for m in request_input:
            if m.role == "user":
                text = m.content if isinstance(m.content, str) else str(m.content)
        match = re.search(r"INC-\d+", text or "")
        return match.group(0) if match else (text or "").strip()

    def _decision(self, text):
        try:
            start, end = text.find("{"), text.rfind("}")
            d = json.loads(text[start:end + 1])
            play = d.get("recommended_play")
            return (play if play in PLAYS else None), d.get("rationale", "")
        except Exception:
            return None, ""

    def _json_output(self, obj):
        return ResponsesAgentResponse(
            output=[self.create_text_output_item(text=json.dumps(obj, default=str), id="msg_1")])

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        # Build the OBO client ONCE per request and thread it through every tool call, so the whole
        # investigation runs as the caller (their masks), and no identity carries over between requests.
        user = self._user_client()
        incident_id = self._incident_id_from(request.input)
        incident = self._lookup_incident(user, incident_id)
        if not incident:
            return self._json_output({"error": f"incident {incident_id!r} not found"})

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content":
                f"Incident {incident['incident_id']} on account {incident['account_id']}. "
                f"Indicator {incident['indicator_value']} ({incident['indicator_type']}). "
                f"Narrative: {incident['narrative']}"}]
        evidence = {}
        for _ in range(MAX_TOOL_TURNS):   # bounded agent loop — the LLM drives it, not a fixed plan
            response = self._llm().predict(endpoint=LLM_ENDPOINT, inputs={
                "messages": messages, "tools": TOOL_SPECS, "max_tokens": 1024, "temperature": 0})
            choice = response["choices"][0]
            message = choice.get("message", {})
            if choice.get("finish_reason") == "tool_calls":
                messages.append(message)
                for call in message.get("tool_calls", []):
                    name = call["function"]["name"]
                    args = json.loads(call["function"].get("arguments") or "{}")
                    try:
                        result = self._run_tool(user, name, args) if name in TOOLS else [{"error": f"unknown tool {name}"}]
                    except Exception as e:
                        result = [{"error": str(e)}]
                    evidence.setdefault(name, []).extend(result)
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": json.dumps(result, default=str)})
            else:
                play, rationale = self._decision(message.get("content", ""))
                return self._json_output({
                    "incident_id": incident["incident_id"], "account_id": incident["account_id"],
                    "indicator_value": incident["indicator_value"], "matched_rule_id": None,
                    "recommended_play": play, "rationale": rationale,
                    "tools_called": list(evidence), "evidence": evidence})
        return self._json_output({"error": "agent did not converge", "incident_id": incident_id})

    def predict_stream(self, request: ResponsesAgentRequest) -> Generator[ResponsesAgentStreamEvent, None, None]:
        for item in self.predict(request).output:
            yield ResponsesAgentStreamEvent(type="response.output_item.done", item=item)


AGENT = TriageAgent()
mlflow.models.set_model(AGENT)
