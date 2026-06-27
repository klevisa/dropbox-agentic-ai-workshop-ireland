"""Load the workshop's pure-logic modules without a Databricks workspace (no pytest dependency).

Used by both conftest.py (wraps these as pytest fixtures) and run.py (the no-pytest runner).
"""
import importlib.util
import sys
import types
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
_CACHE = {}


def _load(relpath, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _stub_agent_deps():
    """Minimal stand-ins for mlflow + databricks-sdk so agent.py imports without them installed."""
    mlflow = types.ModuleType("mlflow")
    models = types.ModuleType("mlflow.models")

    class ModelConfig:
        def __init__(self, development_config=None):
            self._cfg = development_config or {}

        def get(self, key, default=None):
            return self._cfg.get(key, default)

    models.ModelConfig = ModelConfig
    models.set_model = lambda *a, **k: None
    mlflow.models = models

    pyfunc = types.ModuleType("mlflow.pyfunc")

    class ResponsesAgent:
        def create_text_output_item(self, text, id):
            return {"id": id, "text": text}

    pyfunc.ResponsesAgent = ResponsesAgent
    mlflow.pyfunc = pyfunc

    responses = types.ModuleType("mlflow.types.responses")
    for n in ("ResponsesAgentRequest", "ResponsesAgentResponse", "ResponsesAgentStreamEvent"):
        setattr(responses, n, type(n, (), {}))
    types_mod = types.ModuleType("mlflow.types")
    types_mod.responses = responses

    deployments = types.ModuleType("mlflow.deployments")
    deployments.get_deploy_client = lambda *a, **k: None

    databricks = types.ModuleType("databricks")
    sdk = types.ModuleType("databricks.sdk")
    sdk.WorkspaceClient = type("WorkspaceClient", (), {})
    service = types.ModuleType("databricks.sdk.service")
    sql = types.ModuleType("databricks.sdk.service.sql")
    sql.StatementParameterListItem = type("StatementParameterListItem", (), {})

    sys.modules.update({
        "mlflow": mlflow, "mlflow.models": models, "mlflow.pyfunc": pyfunc,
        "mlflow.types": types_mod, "mlflow.types.responses": responses,
        "mlflow.deployments": deployments,
        "databricks": databricks, "databricks.sdk": sdk,
        "databricks.sdk.service": service, "databricks.sdk.service.sql": sql,
    })


def get_fixture(name):
    """Return (and cache) one of the loaded modules by fixture name."""
    if name in _CACHE:
        return _CACHE[name]
    if name == "c_common":
        mod = _load("chapter-c-loops/src/common.py", "c_common")
    elif name == "ti_core":
        mod = _load("chapter-b-spectrum/hosted-mcp/threatintel_core.py", "ti_core")
    elif name == "agent_mod":
        _stub_agent_deps()
        mod = _load("chapter-b-spectrum/triage-agent/agent.py", "triage_agent_under_test")
    else:
        raise KeyError(f"unknown fixture {name!r}")
    _CACHE[name] = mod
    return mod
