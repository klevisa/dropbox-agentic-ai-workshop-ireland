"""Chapter B triage agent — pure helpers (decision parsing, incident-id extraction, tool specs).

The agent's mlflow/databricks imports are stubbed by the `agent_mod` fixture (see conftest).
"""


class _Msg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


def test_plays_and_tools_present(agent_mod):
    assert len(agent_mod.PLAYS) == 8
    assert set(agent_mod.TOOLS) == {"enrich_indicator", "pivot_indicator", "blast_radius",
                                    "get_account_risk", "get_account_actions"}


def test_tool_specs_well_formed(agent_mod):
    for spec in agent_mod.TOOL_SPECS:
        fn = spec["function"]
        assert fn["name"] in agent_mod.TOOLS
        expected_param = agent_mod.TOOLS[fn["name"]][1]   # "acct" or "ind"
        assert fn["parameters"]["required"] == [expected_param]
        assert expected_param in fn["parameters"]["properties"]


def test_decision_parses_valid(agent_mod):
    agent = agent_mod.TriageAgent()
    play, rationale = agent._decision('{"recommended_play": "session_revoked", "rationale": "ato"}')
    assert play == "session_revoked"
    assert rationale == "ato"


def test_decision_rejects_play_not_allowed(agent_mod):
    agent = agent_mod.TriageAgent()
    play, _ = agent._decision('{"recommended_play": "nuke_everything"}')
    assert play is None


def test_decision_handles_garbage(agent_mod):
    agent = agent_mod.TriageAgent()
    assert agent._decision("no json at all") == (None, "")


def test_incident_id_from_sentence(agent_mod):
    agent = agent_mod.TriageAgent()
    inp = [_Msg("user", "please triage INC-00187 right away")]
    assert agent._incident_id_from(inp) == "INC-00187"


def test_incident_id_from_bare_id(agent_mod):
    agent = agent_mod.TriageAgent()
    assert agent._incident_id_from([_Msg("user", "INC-00042")]) == "INC-00042"


def test_incident_id_uses_last_user_message(agent_mod):
    agent = agent_mod.TriageAgent()
    inp = [_Msg("user", "INC-00001"), _Msg("assistant", "ok"), _Msg("user", "actually INC-00099")]
    assert agent._incident_id_from(inp) == "INC-00099"
