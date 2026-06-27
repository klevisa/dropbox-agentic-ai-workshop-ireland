#!/usr/bin/env python3
"""One-time, IDEMPOTENT setup of the two test service principals used for the OBO differential test.
Re-runnable: it creates only what's missing. Requires ACCOUNT-ADMIN auth (account console), which is a
different profile than the workspace one — pass it with --account-profile.

  python3 tests/e2e/setup_sps.py --account-profile <acct-admin-profile>

All workspace specifics come from tests/config.env (WORKSHOP_ACCOUNT_ID, WORKSHOP_HOST,
WORKSHOP_WORKSPACE_ID, WORKSHOP_PRIVILEGED_GROUP, WORKSHOP_NONPRIV_GROUP, the SP names/profiles).

Creates / ensures:
  - the privileged SP   in WORKSHOP_PRIVILEGED_GROUP
  - the non-privileged SP in WORKSHOP_NONPRIV_GROUP
  - an OAuth M2M secret for each (written to ~/.databrickscfg ONCE; secrets never go in the repo)
  - each SP assigned to the workspace
Then run.py uses them via --sp-priv-profile / --sp-reg-profile. The one-time USE CATALOG grant is
applied by tests/e2e/grant_sps.py; the per-run deployer-side grants are applied by run.py itself.
"""
import argparse
import json
import os
import subprocess
import sys

from _env import load_config_env

ACCOUNT_ID = ""   # set in main() from --account-id / WORKSHOP_ACCOUNT_ID
HOST = ""         # set in main() from WORKSHOP_HOST


def acct_api(profile, method, path, body=None):
    args = ["databricks", "account", "--profile", profile] if False else \
        ["databricks", "api", method.lower(), path, "-p", profile]
    if body is not None:
        args += ["--json", json.dumps(body)]
    r = subprocess.run(args, capture_output=True, text=True)
    try:
        return json.loads(r.stdout or "{}"), r.returncode, r.stderr
    except json.JSONDecodeError:
        return {}, r.returncode, (r.stderr or r.stdout)


def find_sp(profile, display_name):
    data, _rc, _e = acct_api(profile, "GET",
        f"/api/2.0/accounts/{ACCOUNT_ID}/scim/v2/ServicePrincipals?filter=displayName+eq+%22{display_name}%22")
    res = data.get("Resources", [])
    return res[0] if res else None


def ensure_sp(profile, display_name):
    sp = find_sp(profile, display_name)
    if sp:
        print(f"  exists: {display_name}  (id={sp['id']}, app_id={sp.get('applicationId')})")
        return sp
    body = {"displayName": display_name,
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal"],
            "entitlements": [{"value": "workspace-access"}, {"value": "databricks-sql-access"}]}
    sp, rc, err = acct_api(profile, "POST", f"/api/2.0/accounts/{ACCOUNT_ID}/scim/v2/ServicePrincipals", body)
    if rc != 0 or "id" not in sp:
        sys.exit(f"  FAILED to create {display_name}: {err or sp}")
    print(f"  created: {display_name}  (id={sp['id']}, app_id={sp.get('applicationId')})")
    return sp


def find_group(profile, name):
    data, _rc, _e = acct_api(profile, "GET",
        f"/api/2.0/accounts/{ACCOUNT_ID}/scim/v2/Groups?filter=displayName+eq+%22{name}%22")
    res = data.get("Resources", [])
    return res[0] if res else None


def ensure_member(profile, group, sp_id, display_name):
    g = find_group(profile, group)
    if not g:
        sys.exit(f"  group {group!r} not found at account level — is_account_group_member needs it there.")
    members = {m.get("value") for m in g.get("members", [])}
    if sp_id in members:
        print(f"  {display_name} already in {group}")
        return
    patch = {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
             "Operations": [{"op": "add", "path": "members", "value": [{"value": sp_id}]}]}
    _resp, rc, err = acct_api(profile, "PATCH",
        f"/api/2.0/accounts/{ACCOUNT_ID}/scim/v2/Groups/{g['id']}", patch)
    print(f"  added {display_name} -> {group}" if rc == 0 else f"  FAILED add to {group}: {err}")


def ensure_secret(profile, sp_id, display_name):
    resp, rc, err = acct_api(profile, "POST",
        f"/api/2.0/accounts/{ACCOUNT_ID}/servicePrincipals/{sp_id}/credentials/secrets", {})
    if rc != 0:
        print(f"  WARN secret mint for {display_name} failed (you may already have one): {err}")
        return None
    return resp.get("secret")


def ensure_workspace(profile, sp_id, workspace_id, display_name):
    _resp, rc, err = acct_api(profile, "PUT",
        f"/api/2.0/accounts/{ACCOUNT_ID}/workspaces/{workspace_id}/permissionassignments/principals/{sp_id}",
        {"permissions": ["USER"]})
    print(f"  {display_name} assigned to workspace" if rc == 0 else f"  WARN workspace assign: {err}")


def main():
    load_config_env()
    env = os.environ.get
    ap = argparse.ArgumentParser()
    ap.add_argument("--account-profile", default=env("WORKSHOP_ADMIN_PROFILE"),
                    help="CLI profile with ACCOUNT-admin auth")
    ap.add_argument("--account-id", default=env("WORKSHOP_ACCOUNT_ID"))
    ap.add_argument("--workspace-id", default=env("WORKSHOP_WORKSPACE_ID"))
    a = ap.parse_args()

    global ACCOUNT_ID, HOST
    ACCOUNT_ID, HOST = a.account_id, env("WORKSHOP_HOST")
    # (display_name, account group, ~/.databrickscfg profile name) for the two test SPs — all from env.
    sps_spec = [
        (env("WORKSHOP_SP_PRIV_NAME", "workshop-obo-sp-priv"), env("WORKSHOP_PRIVILEGED_GROUP"),
         env("WORKSHOP_SP_PRIV_PROFILE", "sp-priv")),
        (env("WORKSHOP_SP_REG_NAME", "workshop-obo-sp-reg"), env("WORKSHOP_NONPRIV_GROUP"),
         env("WORKSHOP_SP_REG_PROFILE", "sp-reg")),
    ]
    need = {"--account-profile": a.account_profile, "--account-id": ACCOUNT_ID, "WORKSHOP_HOST": HOST,
            "--workspace-id": a.workspace_id, "WORKSHOP_PRIVILEGED_GROUP": sps_spec[0][1],
            "WORKSHOP_NONPRIV_GROUP": sps_spec[1][1]}
    miss = [k for k, v in need.items() if not v]
    if miss:
        ap.error(f"missing: {', '.join(miss)} — set in tests/config.env (copy tests/config.env.example)")

    print("Setting up test service principals (idempotent)…")
    stanzas = []
    for display_name, group, profile_name in sps_spec:
        print(f"\n[{display_name}] -> {group}")
        sp = ensure_sp(a.account_profile, display_name)
        ensure_member(a.account_profile, group, sp["id"], display_name)
        ensure_workspace(a.account_profile, sp["id"], a.workspace_id, display_name)
        secret = ensure_secret(a.account_profile, sp["id"], display_name)
        stanzas.append((profile_name, sp.get("applicationId"), secret))

    # Write the M2M profiles straight to ~/.databrickscfg (secrets never printed). Append-only: skip a
    # profile if its [section] already exists, so re-runs don't clobber.
    cfg_path = os.path.expanduser("~/.databrickscfg")
    existing = open(cfg_path).read() if os.path.exists(cfg_path) else ""
    print("\n=== M2M profiles ===")
    for name, client_id, secret in stanzas:
        if f"[{name}]" in existing:
            print(f"  {name}: profile already present (left as-is). client_id={client_id}")
            continue
        if not secret:
            print(f"  {name}: secret unavailable (SP may already have a secret) — regenerate if needed. "
                  f"client_id={client_id}")
            continue
        with open(cfg_path, "a") as f:
            f.write(f"\n[{name}]\nhost          = {HOST}\nclient_id     = {client_id}\n"
                    f"client_secret = {secret}\n")
        print(f"  {name}: written to ~/.databrickscfg (secret not shown). client_id={client_id}")
    print("\nNext: python3 tests/e2e/grant_sps.py  (workspace-side grants), then run.py with "
          "--sp-priv-profile sp-priv --sp-reg-profile sp-reg")


if __name__ == "__main__":
    main()
