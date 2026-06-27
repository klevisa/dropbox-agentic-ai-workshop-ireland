"""Chapter C runbook DSL + utilities — the load-bearing, deterministic contract everything trusts."""


class _FakeSpark:
    """Minimal spark stand-in: spark.sql(...).collect()[0][0] == the configured user."""
    def __init__(self, user):
        self._user = user

    def sql(self, _query):
        user = self._user
        return type("R", (), {"collect": lambda self: [[user]]})()


# ---------------------------------------------------------------- validate_plan
def test_validate_accepts_a_good_plan(c_common):
    plan = [
        {"action": "get_account_risk", "args": {"account_id": "$incident.account_id"}},
        {"action": "recommend_action", "args": {"play": "forced_password_reset"}},
    ]
    assert c_common.validate_plan(plan) == []


def test_validate_rejects_empty(c_common):
    assert c_common.validate_plan([]) == ["plan is empty or not a list"]


def test_validate_rejects_unknown_action(c_common):
    problems = c_common.validate_plan([{"action": "frobnicate", "args": {}}])
    assert any("unknown action" in p for p in problems)


def test_validate_rejects_bad_play(c_common):
    problems = c_common.validate_plan([{"action": "recommend_action", "args": {"play": "nuke_everything"}}])
    assert any("play" in p for p in problems)


def test_validate_rejects_unresolvable_ref(c_common):
    problems = c_common.validate_plan(
        [{"action": "get_account_risk", "args": {"account_id": "$incident.bogus"}}])
    assert any("unresolvable ref" in p for p in problems)


def test_validate_allows_steps_ref(c_common):
    plan = [{"action": "blast_radius", "args": {"indicator": "$steps.enrich_indicator.indicator"}}]
    assert c_common.validate_plan(plan) == []


# ---------------------------------------------------------------- repair_plan
def test_repair_slims_recommend_action_to_decision_only(c_common):
    # The LLM tends to echo identifiers; repair must drop everything but play + rationale.
    step = {"action": "recommend_action", "args": {
        "account_id": "$incident.account_id", "indicator_value": "$incident.indicator_value",
        "incident_id": "$incident.incident_id", "matched_rule_id": "RB-001",
        "play": "session_revoked", "rationale": "phishing wave"}}
    repaired = c_common.repair_plan([step])
    assert repaired[0]["args"] == {"play": "session_revoked", "rationale": "phishing wave"}


def test_repair_fixes_indicator_action_arg(c_common):
    repaired = c_common.repair_plan([{"action": "enrich_indicator", "args": {"account_id": "x"}}])
    assert repaired[0]["args"] == {"indicator": "$incident.indicator_value"}


def test_repair_fixes_account_action_and_alias(c_common):
    repaired = c_common.repair_plan([{"action": "get_account_risk", "args": {"account": "$incident.account"}}])
    assert repaired[0]["args"]["account_id"] == "$incident.account_id"


def test_repair_drops_unknown_actions(c_common):
    repaired = c_common.repair_plan([{"action": "frobnicate", "args": {}},
                                     {"action": "recommend_action", "args": {"play": "cleared_no_action"}}])
    assert [s["action"] for s in repaired] == ["recommend_action"]


def test_repair_then_validate_is_clean(c_common):
    messy = [
        {"action": "get_account_risk", "args": {"account": "$incident.account"}},
        {"action": "blast_radius", "args": {"indicator": "$incident.indicator_value", "account_id": "x"}},
        {"action": "recommend_action", "args": {"play": "external_sharing_disabled",
                                                "incident_id": "$incident.incident_id", "rationale": "exfil"}},
    ]
    assert c_common.validate_plan(c_common.repair_plan(messy)) == []


# ---------------------------------------------------------------- to_sql_string / extract_json
def test_to_sql_string_escapes_quotes(c_common):
    assert c_common.to_sql_string("O'Brien") == "'O''Brien'"


def test_extract_json_bare(c_common):
    assert c_common.extract_json('{"rule_id": "RB-003"}') == {"rule_id": "RB-003"}


def test_extract_json_fenced(c_common):
    assert c_common.extract_json('```json\n{"a": 1}\n```')["a"] == 1


# ---------------------------------------------------------------- resolve_ref / resolve_args
def test_resolve_ref_incident_field(c_common):
    ctx = {"incident": {"account_id": "ACC-1"}, "steps": {}}
    assert c_common.resolve_ref("$incident.account_id", ctx) == "ACC-1"


def test_resolve_ref_literal_passthrough(c_common):
    assert c_common.resolve_ref("forced_password_reset", {}) == "forced_password_reset"


def test_resolve_ref_missing_returns_none(c_common):
    assert c_common.resolve_ref("$incident.nope", {"incident": {}}) is None


def test_resolve_args_maps_all(c_common):
    ctx = {"incident": {"account_id": "ACC-9", "indicator_value": "http://x"}}
    out = c_common.resolve_args({"account_id": "$incident.account_id", "play": "rate_limited"}, ctx)
    assert out == {"account_id": "ACC-9", "play": "rate_limited"}


# ---------------------------------------------------------------- derive_prefix
def test_derive_prefix_override_wins(c_common):
    assert c_common.derive_prefix(spark=None, override="forced") == "forced"


def test_derive_prefix_sanitizes_email(c_common):
    spark = _FakeSpark("Ada.Lovelace+groupA@example.com")
    assert c_common.derive_prefix(spark) == "ada_lovelace_groupa"
