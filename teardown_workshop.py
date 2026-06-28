# Databricks notebook source
# MAGIC %md
# MAGIC # Tear down the workshop — run this at the end
# MAGIC
# MAGIC Removes everything **you** deployed. `bundle destroy` only cleans bundle-tracked resources
# MAGIC (jobs + apps); everything else was created imperatively or inside notebooks, so this notebook
# MAGIC removes it explicitly:
# MAGIC 1. **`bundle destroy`** all three chapters — the jobs + the MCP and Review apps.
# MAGIC 2. **Delete the agent serving endpoint** (created by `agents.deploy()`, not by the DAB).
# MAGIC 3. **Drop your schemas** (`{prefix}_ti_intel/_risk/_cs/_tools`, `CASCADE`) — tables, UC functions,
# MAGIC    masks, views, and the registered agent model.
# MAGIC 4. **Delete your two Genie spaces** (titled `{prefix} · Threat Intel — …`).
# MAGIC 5. **Delete the MLflow experiments** (`/Users/<you>/aiapps-chapter-b-triage` and `…-c-triage`).
# MAGIC
# MAGIC Set the widgets to the **same `catalog` you deployed with**, set `confirm` to **YES**, and Run All.
# MAGIC The shared catalog itself (admin-owned) is left alone.

# COMMAND ----------
# MAGIC %run ./_deploy_lib

# COMMAND ----------
import re

dbutils.widgets.text("catalog", "", "catalog (the one you deployed into)")
dbutils.widgets.text("privileged_group", "", "privileged_group (so the bundle resolves; any value ok)")
dbutils.widgets.text("warehouse_id", "", "warehouse_id (so the bundle resolves; any value ok)")
dbutils.widgets.dropdown("confirm", "NO", ["NO", "YES"], "confirm teardown")

# COMMAND ----------
# MAGIC %md
# MAGIC **Fill in the widgets above** (and set `confirm` to YES), then run the next cell to capture them.

# COMMAND ----------
CATALOG = dbutils.widgets.get("catalog").strip()
GROUP = dbutils.widgets.get("privileged_group").strip() or "placeholder_group"
WAREHOUSE = dbutils.widgets.get("warehouse_id").strip() or "placeholder"
CONFIRM = dbutils.widgets.get("confirm")

require(catalog=CATALOG)
if CONFIRM != "YES":
    raise SystemExit("Set the 'confirm' widget to YES to tear down. (Nothing was deleted.)")

# Your schema prefix = local part of your email, non-alphanumerics -> '_' (same rule as src/common.py).
me = spark.sql("SELECT current_user()").collect()[0][0]
PREFIX = re.sub(r"[^a-zA-Z0-9]", "_", me.split("@")[0]).lower()
print(f"me={me}  prefix={PREFIX}  catalog={CATALOG}")

# COMMAND ----------
# 1) destroy the three bundles (jobs + apps). Vars are passed so the bundle config resolves.
cli = ensure_cli()
env = auth_env()
root = repo_root()
var_flags = [f"--var=catalog={CATALOG}", f"--var=warehouse_id={WAREHOUSE}", f"--var=privileged_group={GROUP}"]

for chapter in ["chapter-a-foundation", "chapter-b-spectrum", "chapter-c-loops"]:
    print(f"\n=== destroy {chapter} ===")
    try:
        run_cli(cli, ["bundle", "destroy", "-t", "dev", "--auto-approve", *var_flags],
                f"{root}/{chapter}", env)
    except Exception as e:
        print(f"  (continuing) {chapter}: {e}")

# COMMAND ----------
# 1b) BACKSTOP — delete the workshop jobs + apps by NAME, in case `bundle destroy` above didn't run to
# completion. `bundle destroy` aborts as a unit if any one resource fails to delete (e.g. an app that's
# mid-start), which orphans the rest — so we don't trust it as the only path. Scoped to YOUR handle so
# this is safe in a shared workspace.
import json as _j, subprocess, time

HANDLE = PREFIX.replace("_", "-")   # app names use the hyphen-form of your handle

_jl = subprocess.run([cli, "jobs", "list", "-o", "json"], env=env, capture_output=True, text=True)
_jobs = _j.loads(_jl.stdout or "[]") if _jl.returncode == 0 else []
_jobs = _jobs if isinstance(_jobs, list) else _jobs.get("jobs", [])
for _job in _jobs:
    _name = _job.get("settings", {}).get("name") or ""
    if "AI Apps Workshop" in _name and PREFIX in _name:
        subprocess.run([cli, "jobs", "delete", str(_job["job_id"])], env=env, capture_output=True)
        print(f"  deleted job {_job['job_id']}  {_name}")

for _app in (f"mcp-{HANDLE}", f"review-{HANDLE}"):
    if subprocess.run([cli, "apps", "delete", _app], env=env, capture_output=True).returncode == 0:
        print(f"  deleting app {_app} (async)")
# wait for the apps to actually disappear, else a later redeploy hits 'app already exists'
for _app in (f"mcp-{HANDLE}", f"review-{HANDLE}"):
    for _ in range(20):
        if subprocess.run([cli, "apps", "get", _app], env=env, capture_output=True).returncode != 0:
            print(f"  {_app}: gone"); break
        time.sleep(9)

# COMMAND ----------
# 2) delete the agent serving endpoint(s). agents.deploy() creates the endpoint imperatively (not in
# the DAB), so bundle destroy leaves it. Match any endpoint whose name contains your {prefix}_ti_tools.
import json as _json
_eps = subprocess.run([cli, "serving-endpoints", "list", "-o", "json"],
                      env=env, capture_output=True, text=True)
_data = _json.loads(_eps.stdout or "[]") if _eps.returncode == 0 else []
_eps_list = _data if isinstance(_data, list) else _data.get("endpoints", [])
_hits = [e["name"] for e in _eps_list if f"{PREFIX}_ti_tools" in (e.get("name") or "")]
if not _hits:
    print("  (no agent serving endpoint found)")
for _n in _hits:
    subprocess.run([cli, "serving-endpoints", "delete", _n], env=env, capture_output=True)
    print(f"  deleted serving endpoint {_n}")

# COMMAND ----------
# 3) drop the per-user schemas (CASCADE). We run as YOU, so spark.sql is enough — no warehouse needed.
for schema in ["ti_intel", "ti_risk", "ti_cs", "ti_tools"]:
    fq = f"{CATALOG}.{PREFIX}_{schema}"
    spark.sql(f"DROP SCHEMA IF EXISTS {fq} CASCADE")
    print(f"  dropped {fq}")

# COMMAND ----------
# 4) delete your two Genie spaces (matched by title prefix). Genie spaces aren't bundle-tracked.
import json, subprocess

listing = subprocess.run([cli, "api", "get", "/api/2.0/genie/spaces"],
                         env=env, capture_output=True, text=True)
spaces = json.loads(listing.stdout or "{}").get("spaces", []) if listing.returncode == 0 else []
hits = [s for s in spaces if (s.get("title") or "").startswith(f"{PREFIX} · Threat Intel")]
if not hits:
    print("  (no Genie spaces matched — already gone, or created under a different prefix)")
for s in hits:
    subprocess.run([cli, "api", "delete", f"/api/2.0/genie/spaces/{s['space_id']}"],
                   env=env, capture_output=True, text=True)
    print(f"  deleted {s['space_id']}  {s['title']}")

# COMMAND ----------
# 5) delete the MLflow experiments (the agent deploy + triage_runner set these at your home path;
# they aren't bundle-tracked). An MLflow experiment at a workspace path IS a workspace object, so a
# `workspace delete` removes it permanently — cleaner than the API's soft-delete, which would leave a
# trashed experiment at the same name and block re-creating it on your next run.
for experiment in (f"/Users/{me}/aiapps-chapter-b-triage", f"/Users/{me}/aiapps-chapter-c-triage"):
    res = subprocess.run([cli, "workspace", "delete", experiment], env=env, capture_output=True, text=True)
    print(f"  deleted experiment {experiment}" if res.returncode == 0
          else f"  (no experiment to delete: {experiment})")

# COMMAND ----------
# 6) remove the bundle deployment folders (state + uploaded files). The out-of-band deletes above can
# leave these behind; clearing them means your next deploy starts from a clean state.
for _b in ("aiapps-chapter-a", "aiapps-chapter-b", "aiapps-chapter-c"):
    _p = f"/Users/{me}/.bundle/{_b}"
    _r = subprocess.run([cli, "workspace", "delete", "--recursive", _p], env=env, capture_output=True, text=True)
    print(f"  removed {_p}" if _r.returncode == 0 else f"  (nothing to remove: {_p})")

print("\n✅ Teardown complete. The shared catalog itself was left in place (admin-owned).")
