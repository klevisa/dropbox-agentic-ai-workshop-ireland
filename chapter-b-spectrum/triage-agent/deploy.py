# Databricks notebook source
# MAGIC %md
# MAGIC # Chapter B · Deploy the triage agent
# MAGIC Logs `agent.py` (models-from-code), registers it to Unity Catalog, and deploys it to a **Model
# MAGIC Serving** endpoint with `agents.deploy`. Then query the endpoint with an **incident id** and it
# MAGIC returns the recommendation JSON.
# MAGIC
# MAGIC Run it via the DAB for CLI ease — `databricks bundle run deploy_triage_agent -t dev` — or open it
# MAGIC and `Run All`. Deploying an agent is inherently imperative (log → register → `agents.deploy`), so
# MAGIC the DAB job is just a thin runner around this notebook; there's no declarative resource for it.

# COMMAND ----------
# MAGIC %pip install -q mlflow databricks-agents databricks-sdk

# COMMAND ----------
# MAGIC %restart_python

# COMMAND ----------
# MAGIC %run ../common

# COMMAND ----------
# Config — defaults set here from config.yml-style values; the DAB job overrides them via params.
dbutils.widgets.text("catalog", "")               # set, or let the DAB job pass it
dbutils.widgets.text("warehouse_id", "")          # set, or let the DAB job pass it
dbutils.widgets.text("model_endpoint", "")        # blank = auto-pick a Claude endpoint
dbutils.widgets.text("uc_model_name", "triage_agent")
dbutils.widgets.text("agent_path", "")            # DAB passes the absolute path; blank = sibling agent.py

CATALOG = dbutils.widgets.get("catalog")
WAREHOUSE_ID = dbutils.widgets.get("warehouse_id").strip()
MODEL_ENDPOINT = dbutils.widgets.get("model_endpoint").strip()
UC_MODEL_NAME = dbutils.widgets.get("uc_model_name").strip()

# workshop_context() + notebook_dir() come from ../common.
ctx = workshop_context(spark, catalog=CATALOG)
AGENT_PATH = dbutils.widgets.get("agent_path").strip() or f"{notebook_dir(dbutils)}/agent.py"
print(ctx)
print(f"warehouse={WAREHOUSE_ID}  agent={AGENT_PATH}")

# COMMAND ----------
from databricks.sdk import WorkspaceClient

# Pick a tool-calling FM endpoint (Claude; Llama 3.3 also supports tools).
available = {e.name for e in WorkspaceClient().serving_endpoints.list()}
LLM = next((c for c in ([MODEL_ENDPOINT] if MODEL_ENDPOINT else [
    "databricks-claude-sonnet-4-5", "databricks-claude-sonnet-4",
    "databricks-claude-3-7-sonnet", "databricks-meta-llama-3-3-70b-instruct"]) if c in available), None)
assert LLM, f"no usable tool-calling FM endpoint; set MODEL_ENDPOINT. available: {sorted(available)[:10]}"

UC_MODEL = f"{ctx.catalog}.{ctx.tools}.{UC_MODEL_NAME}"
print(f"model={LLM}   register -> {UC_MODEL}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Declare the agent's auth — on-behalf-of-user (OBO)
# MAGIC This agent is meant to be called **ad hoc** (e.g. from the AI Playground), so it should run as
# MAGIC **whoever asks**, not as a fixed service principal — then the Chapter A masks apply to the *caller*.
# MAGIC We deploy it with an MLflow **`AuthPolicy`** (two halves):
# MAGIC * **`SystemAuthPolicy`** — what the agent uses as *itself*: just the **LLM endpoint** (the model it
# MAGIC   calls to reason; nothing identity-sensitive).
# MAGIC * **`UserAuthPolicy`** — the REST API **scopes** the agent may use *on the caller's behalf*. We only
# MAGIC   need to run SQL on a warehouse (the UC-function tools go through the SQL Statement Execution API),
# MAGIC   so we request the two `sql.*` scopes and nothing more (least privilege).
# MAGIC
# MAGIC At runtime `agent.py` builds a `WorkspaceClient(credentials_strategy=ModelServingUserCredentials())`
# MAGIC per request, so each tool call runs as that user. (Requires `mlflow>=2.22.1` and a workspace admin
# MAGIC to have enabled on-behalf-of-user authorization. Docs:
# MAGIC https://docs.databricks.com/aws/en/generative-ai/agent-framework/agent-authentication-model-serving)

# COMMAND ----------
from mlflow.models.auth_policy import AuthPolicy, SystemAuthPolicy, UserAuthPolicy
from mlflow.models.resources import DatabricksServingEndpoint

# As itself, the agent only needs to call the LLM.
system_auth_policy = SystemAuthPolicy(resources=[DatabricksServingEndpoint(endpoint_name=LLM)])

# On the caller's behalf, it only needs to run SQL on the warehouse (which is how it invokes the UC
# functions + reads the incident). The user's own grants + the masks then govern what comes back — we do
# NOT pre-grant the tables/functions here (that would be the service-principal model we're moving away
# from). If your workspace rejects a scope string, adjust to the values in the docs linked above.
user_auth_policy = UserAuthPolicy(api_scopes=["sql.statement-execution", "sql.warehouses"])
auth_policy = AuthPolicy(system_auth_policy=system_auth_policy, user_auth_policy=user_auth_policy)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Log + register to Unity Catalog, then deploy

# COMMAND ----------
import mlflow

# Route BOTH the deploy run and the endpoint's inference traces to a clean, findable experiment
# (mirrors Chapter C's aiapps-chapter-c-triage). Without this, MLflow defaults to the deploy
# notebook's auto-experiment and agents.deploy wires the endpoint's traces to that obscure path.
EXPERIMENT = f"/Users/{ctx.me}/aiapps-chapter-b-triage"
mlflow.set_experiment(EXPERIMENT)
print(f"experiment (deploy run + agent traces): {EXPERIMENT}")

mlflow.set_registry_uri("databricks-uc")
with mlflow.start_run():
    logged = mlflow.pyfunc.log_model(
        name="agent",
        python_model=AGENT_PATH,
        model_config={"catalog": ctx.catalog, "prefix": ctx.prefix, "llm_endpoint": LLM,
                      "warehouse_id": WAREHOUSE_ID},
        auth_policy=auth_policy,        # OBO — replaces the system-principal `resources=` model
        # databricks-ai-bridge supplies ModelServingUserCredentials (the OBO client) at serving time.
        pip_requirements=["mlflow>=2.22.1", "databricks-sdk", "databricks-ai-bridge"],
        registered_model_name=UC_MODEL,
    )
print(f"registered {UC_MODEL} version {logged.registered_model_version}")

# COMMAND ----------
from databricks import agents

deployment = agents.deploy(model_name=UC_MODEL, model_version=logged.registered_model_version,
                           scale_to_zero_enabled=True)
print(f"endpoint: {deployment.endpoint_name}")
print(f"query:    {deployment.query_endpoint}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Call it — give it an incident id, get JSON back
# MAGIC The endpoint may take a few minutes to become READY after the first deploy.

# COMMAND ----------
from mlflow.deployments import get_deploy_client

try:
    resp = get_deploy_client("databricks").predict(
        endpoint=deployment.endpoint_name,
        inputs={"input": [{"role": "user", "content": "INC-00187"}]})
    print(resp["output"][0]["content"][0]["text"])
except Exception as e:
    print(f"endpoint not ready yet ({str(e)[:120]}). Try again in a few minutes:")
    print(f'  get_deploy_client("databricks").predict(endpoint="{deployment.endpoint_name}", '
          f'inputs={{"input":[{{"role":"user","content":"INC-00187"}}]}})')
