"""Chapter B threatintel_core — the one tool definition shared by the skill, local MCP, and hosted MCP."""

EXPECTED_TOOLS = {"get_account_risk", "get_account_actions", "pivot_indicator",
                  "blast_radius", "enrich_indicator"}


def test_tools_registry_complete(ti_core):
    assert set(ti_core.TOOLS) == EXPECTED_TOOLS


def test_tools_schema(ti_core):
    assert ti_core.tools_schema("cat", "ada_lovelace") == "cat.ada_lovelace_ti_tools"


def test_tool_statement_shape(ti_core):
    schema = ti_core.tools_schema("cat", "p")
    statement, param = ti_core.tool_statement(schema, "enrich_indicator")
    assert statement == "SELECT * FROM cat.p_ti_tools.enrich_indicator(:ind)"
    assert param == "ind"


def test_tool_statement_account_param(ti_core):
    _stmt, param = ti_core.tool_statement(ti_core.tools_schema("c", "p"), "get_account_risk")
    assert param == "acct"


def test_tool_statement_unknown_raises(ti_core):
    raised = False
    try:
        ti_core.tool_statement("c.p_ti_tools", "no_such_tool")
    except ValueError:
        raised = True
    assert raised


def test_call_tool_dispatches(ti_core):
    calls = []

    def fake_run_sql(statement, param, value):
        calls.append((statement, param, value))
        return [{"ok": True}]

    schema = ti_core.tools_schema("cat", "p")
    out = ti_core.call_tool(fake_run_sql, schema, "blast_radius", "http://bad/x")
    assert out == [{"ok": True}]
    assert calls == [("SELECT * FROM cat.p_ti_tools.blast_radius(:ind)", "ind", "http://bad/x")]


def test_derive_prefix_sanitizes(ti_core):
    assert ti_core.derive_prefix("Ada.Lovelace+groupA@example.com") == "ada_lovelace_groupa"
