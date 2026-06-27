#!/usr/bin/env python3
"""Workspace-side grants so the two test SPs can reach the deployer's slice. Idempotent / re-runnable.
Splits the work by who is allowed to issue each grant (mirrors the real workshop's admin/participant split):

  - admin  grants USE CATALOG on the catalog (a participant can't self-issue this)
  - deployer (+groupa) grants USE SCHEMA / SELECT / EXECUTE on its own schemas, and CAN_USE on its apps

  python3 tests/e2e/grant_sps.py \
      --admin-profile <acct/ws-admin> --deployer-profile <deployer-profile> \
      --priv-app-id <sp_priv applicationId> --reg-app-id <sp_reg applicationId>

Catalog/warehouse come from --catalog/--warehouse or WORKSHOP_CATALOG/WORKSHOP_WAREHOUSE
(tests/config.env). run.py re-applies the deployer-side grants automatically each run, so this
script is mainly for the one-time admin USE CATALOG grant.
"""
import argparse
import json
import os
import subprocess

from _env import load_config_env

CATALOG = ""    # set in main() from --catalog / WORKSHOP_CATALOG
WAREHOUSE = ""  # set in main() from --warehouse / WORKSHOP_WAREHOUSE


def sql(profile, statement):
    body = {"warehouse_id": WAREHOUSE, "wait_timeout": "50s", "statement": statement}
    out = subprocess.run(["databricks", "api", "post", "/api/2.0/sql/statements", "-p", profile,
                          "--json", json.dumps(body)], capture_output=True, text=True).stdout
    state = json.loads(out or "{}").get("status", {}).get("state", "ERR")
    print(f"    [{state}] {statement[:90]}")
    return state


def app_grant(profile, app_name, app_id):
    """Grant CAN_USE on a Databricks App to an SP (by application id)."""
    body = {"access_control_list": [{"service_principal_name": app_id, "permission_level": "CAN_USE"}]}
    r = subprocess.run(["databricks", "api", "patch", f"/api/2.0/permissions/apps/{app_name}",
                        "-p", profile, "--json", json.dumps(body)], capture_output=True, text=True)
    print(f"    app {app_name} CAN_USE -> {app_id}: {'ok' if r.returncode == 0 else r.stderr[:120]}")


def main():
    load_config_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-profile", required=True)
    ap.add_argument("--deployer-profile", required=True)
    ap.add_argument("--prefix", required=True, help="deployer schema prefix, e.g. <handle>_groupa")
    ap.add_argument("--priv-app-id", required=True)
    ap.add_argument("--reg-app-id", required=True)
    ap.add_argument("--app-names", default="", help="comma-sep app names to grant CAN_USE (mcp-…,review-…)")
    ap.add_argument("--catalog", default=os.environ.get("WORKSHOP_CATALOG"))
    ap.add_argument("--warehouse", default=os.environ.get("WORKSHOP_WAREHOUSE"))
    a = ap.parse_args()
    if not (a.catalog and a.warehouse):
        ap.error("set --catalog/--warehouse or WORKSHOP_CATALOG/WORKSHOP_WAREHOUSE (tests/config.env)")
    global CATALOG, WAREHOUSE
    CATALOG, WAREHOUSE = a.catalog, a.warehouse
    sps = [a.priv_app_id, a.reg_app_id]

    print("admin: USE CATALOG")
    for sp in sps:
        sql(a.admin_profile, f"GRANT USE CATALOG ON CATALOG {CATALOG} TO `{sp}`")

    print("deployer: schema USE/SELECT/EXECUTE")
    for schema in (f"{a.prefix}_ti_intel", f"{a.prefix}_ti_risk", f"{a.prefix}_ti_tools"):
        for sp in sps:
            sql(a.deployer_profile, f"GRANT USE SCHEMA ON SCHEMA {CATALOG}.{schema} TO `{sp}`")
            sql(a.deployer_profile, f"GRANT SELECT ON SCHEMA {CATALOG}.{schema} TO `{sp}`")
            sql(a.deployer_profile, f"GRANT EXECUTE ON SCHEMA {CATALOG}.{schema} TO `{sp}`")

    print("deployer: app CAN_USE")
    for app_name in [n.strip() for n in a.app_names.split(",") if n.strip()]:
        for sp in sps:
            app_grant(a.deployer_profile, app_name, sp)


if __name__ == "__main__":
    main()
