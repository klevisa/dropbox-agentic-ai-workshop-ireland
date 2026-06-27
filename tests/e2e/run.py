#!/usr/bin/env python3
"""Live end-to-end test of the workshop: deploy + run A → B → C as the privileged user, assert at each
gate (incl. groupa-vs-groupb redaction), then tear down. Mutates the workspace and costs compute — run
it deliberately. Backbone for the `/workshop-e2e` skill.

  python3 tests/e2e/run.py                         # full run as the default (groupa) profile, then teardown
  python3 tests/e2e/run.py --groupb-profile g2     # also assert redaction for a non-privileged identity
  python3 tests/e2e/run.py --stages a              # just Chapter A
  python3 tests/e2e/run.py --no-teardown           # leave resources up
  python3 tests/e2e/run.py --teardown-only         # just tear down

Automated gates: bundle deploy/run exit codes, table row counts, UC-function results, masking (groupa
unmasked; groupb redacted, if a groupb profile is given), Genie spaces exist, proposed/approved rules,
triage recommendations + accuracy vs. the hidden scenario_label. Documented-manual (printed, not
asserted): the MCP OBO tool call and the agent-endpoint query (need OAuth / an MCP client / a warm
serving endpoint) and MLflow traces.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXPECTED_PLAYS = {  # scenario_label -> expected containment play (same mapping as explore.py)
    "account_takeover": "forced_password_reset", "malware_delivery": "account_suspended",
    "data_exfiltration": "external_sharing_disabled", "credential_stuffing": "rate_limited",
    "phishing_wave": "session_revoked", "insider_activity": "manual_review",
    "api_token_abuse": "rate_limited", "benign": "cleared_no_action"}
ALLOWED_PLAYS = set(EXPECTED_PLAYS.values()) | {"mfa_enforced", "forced_password_reset"}


class Runner:
    def __init__(self, cfg):
        self.cfg = cfg
        self.results = []   # (ok, label, detail)

    # ---- shell / CLI helpers ----
    def _cli(self, args, cwd=None, check=False):
        return subprocess.run(["databricks", *args], cwd=cwd, capture_output=True, text=True, check=check)

    def whoami(self, profile):
        out = self._cli(["current-user", "me", "-p", profile, "-o", "json"]).stdout
        return json.loads(out)["userName"]

    def sql(self, statement, profile=None):
        """Run SQL via the Statements API; return (state, columns, rows)."""
        profile = profile or self.cfg["profile"]
        body = {"warehouse_id": self.cfg["warehouse"], "wait_timeout": "50s", "statement": statement}
        out = self._cli(["api", "post", "/api/2.0/sql/statements", "-p", profile, "--json", json.dumps(body)]).stdout
        resp = json.loads(out or "{}")
        state = resp.get("status", {}).get("state", "ERR")
        cols = [c["name"] for c in resp.get("manifest", {}).get("schema", {}).get("columns", [])]
        rows = resp.get("result", {}).get("data_array", [])
        return state, cols, rows

    def scalar(self, statement, profile=None):
        state, _cols, rows = self.sql(statement, profile)
        return rows[0][0] if (state == "SUCCEEDED" and rows) else None

    def scalar_int(self, statement, profile=None):
        """The Statements API returns values as strings — coerce a count to int (0 if null/empty)."""
        v = self.scalar(statement, profile)
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    def _chapter_vars(self, chapter):
        """--var flags for `bundle deploy`. The committed config.yml is intentionally blank (no
        workspace specifics in git), so the harness supplies the values from config.env here. Only pass
        vars a chapter actually declares — an undeclared --var is a hard error. (model_endpoint is left
        blank = auto-pick a Claude endpoint; uc_model_name keeps its bundle default.)"""
        v = {"catalog": self.cfg["catalog"], "warehouse_id": self.cfg["warehouse"]}
        if chapter in ("chapter-a-foundation", "chapter-c-loops"):
            v["privileged_group"] = self.cfg["privileged_group"]
        return [f"--var={k}={val}" for k, val in v.items()]

    def bundle_deploy(self, chapter):
        r = self._cli(["bundle", "deploy", "-t", "dev", "-p", self.cfg["profile"], *self._chapter_vars(chapter)],
                      cwd=ROOT / chapter)
        return r.returncode == 0, r.stderr[-300:]

    def bundle_run(self, chapter, target):
        # Pass --var here too: for an APP target, `bundle run` re-deploys the app and RE-RESOLVES its env,
        # so empty `value: ${var.warehouse_id}` (config.yml is blank) is rejected unless we supply them.
        # Harmless for job targets — their params were baked at deploy; run just triggers the job.
        r = self._cli(["bundle", "run", target, "-t", "dev", "-p", self.cfg["profile"], *self._chapter_vars(chapter)],
                      cwd=ROOT / chapter)
        return r.returncode == 0, (r.stderr or r.stdout)[-300:]

    def check(self, label, ok, detail=""):
        self.results.append((bool(ok), label, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  — {detail}" if detail else ""))
        return ok

    # ---- stages ----
    def preflight(self):
        """Record the deployer's (non-default) capabilities up front, so a Chapter B failure later is
        easy to attribute. Entitlements are informational; the FM-endpoint query is a hard gate (ai_query
        in C needs it). App/serving-create aren't SCIM entitlements, so those surface at the B deploy."""
        print("\n== preflight (deployer capabilities) ==")
        out = self._cli(["api", "get", "/api/2.0/preview/scim/v2/Me", "-p", self.cfg["profile"]]).stdout
        ents = sorted(e.get("value") for e in json.loads(out or "{}").get("entitlements", []))
        print(f"  deployer={self.whoami(self.cfg['profile'])}  entitlements={ents or '(none listed)'}")
        # FM endpoint must be queryable for ai_query (Chapter C).
        state, _c, _r = self.sql("SELECT ai_query('databricks-claude-sonnet-4-5', 'reply with: ok') AS r")
        self.check("FM endpoint queryable (ai_query)", state == "SUCCEEDED", state)
    def stage_a(self):
        print("\n== Chapter A — foundation ==")
        cat, p = self.cfg["catalog"], self.cfg["prefix"]
        intel, risk, tools = f"{cat}.{p}_ti_intel", f"{cat}.{p}_ti_risk", f"{cat}.{p}_ti_tools"
        if not self.cfg["asserts_only"]:
            self.check("A deploy", *self.bundle_deploy("chapter-a-foundation"))
            ok, detail = self.bundle_run("chapter-a-foundation", "chapter_a_foundation")
            if not self.check("A job run", ok, detail):
                return
        self.check("incidents loaded", self.scalar_int(f"SELECT count(*) FROM {intel}.incidents") > 0)
        self.check("accounts loaded", self.scalar_int(f"SELECT count(*) FROM {risk}.accounts") > 0)

        acct = self.scalar(f"SELECT account_id FROM {risk}.account_risk_scores LIMIT 1")
        ind = self.scalar(f"""SELECT inc.indicator_value FROM {intel}.incidents inc
            JOIN {intel}.indicator_intel ii ON ii.indicator_value = inc.indicator_value LIMIT 1""")
        for fn, arg in [("get_account_risk", acct), ("get_account_actions", acct),
                        ("pivot_indicator", ind), ("blast_radius", ind)]:
            st, _c, rows = self.sql(f"SELECT * FROM {tools}.{fn}('{arg}')")
            self.check(f"UC function {fn} returns rows", st == "SUCCEEDED" and len(rows) >= 1, st)
        url = self.scalar(f"SELECT indicator_value FROM {intel}.indicator_intel WHERE urlhaus_type='url' LIMIT 1")
        self.check("enrich_indicator ok on a feed URL",
                   self.scalar(f"SELECT query_status FROM {tools}.enrich_indicator('{url}')") == "ok")

        # masking — privileged caller (groupa) sees the real name
        name_a = self.scalar(f"SELECT customer_name FROM {risk}.accounts WHERE account_id='{acct}'")
        self.check("groupa sees UNMASKED customer_name", name_a not in (None, "***REDACTED***"), name_a)

        # masking — non-privileged caller (groupb), if provided: grant read, expect redaction
        gb = self.cfg.get("groupb_profile")
        if gb:
            gb_user = self.whoami(gb)
            for stmt in [f"GRANT USE SCHEMA ON SCHEMA {risk} TO `{gb_user}`",
                         f"GRANT SELECT ON SCHEMA {risk} TO `{gb_user}`"]:
                self.sql(stmt)   # groupa owns the schema, so it can grant
            name_b = self.scalar(f"SELECT customer_name FROM {risk}.accounts WHERE account_id='{acct}'", profile=gb)
            score_b = self.scalar(f"""SELECT risk_score FROM {risk}.account_risk_scores
                WHERE account_id='{acct}' ORDER BY score_date DESC LIMIT 1""", profile=gb)
            self.check("groupb sees REDACTED customer_name", name_b == "***REDACTED***", str(name_b))
            self.check("groupb sees NULL risk_score (masked)", score_b in (None, "null"), str(score_b))
        else:
            print("  [skip] groupb redaction — pass --groupb-profile to test it")

        spaces = self._genie_spaces()
        self.check("2 Genie spaces created", len(spaces) == 2, f"{[s['title'] for s in spaces]}")

    def stage_b(self):
        print("\n== Chapter B — MCP + agent ==")
        if not self.cfg["asserts_only"]:
            self.check("B deploy", *self.bundle_deploy("chapter-b-spectrum"))
            self.check("MCP app started", *self.bundle_run("chapter-b-spectrum", "threatintel_mcp"))
        self.check("MCP app reaches RUNNING", self._wait_app_running())
        if not self.cfg["asserts_only"]:
            self.check("agent deploy job", *self.bundle_run("chapter-b-spectrum", "deploy_triage_agent"))
        self.check("agent serving endpoint exists", self._agent_endpoint_exists())
        self.agent_predict()
        self.obo_mcp()

    def _mcp_app_url(self):
        for a in self._apps():
            if (a.get("name") or "").startswith("mcp-"):
                return (a.get("url") or "").rstrip("/") + "/mcp"
        return None

    # ---- re-share the slice with the test SPs (what a participant does by hand after (re)deploying) ----
    def _sp_ids(self):
        """The test SPs' application ids (client_id) from ~/.databrickscfg — the principal used in grants."""
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(os.path.expanduser("~/.databrickscfg"))
        ids = []
        for prof in (self.cfg.get("sp_priv_profile"), self.cfg.get("sp_reg_profile")):
            if prof and prof in cfg and cfg[prof].get("client_id"):
                ids.append(cfg[prof]["client_id"])
        return ids

    def _grant_sp_schemas(self):
        """Re-grant the test SPs read access to this slice's schemas. Re-applied every run because teardown
        DROPs the schemas (a grant dies with its object). Mirrors a participant re-sharing their schemas.
        NOTE: USE CATALOG is the admin's one-time grant (catalog is never dropped, and a participant can't
        self-issue it) — so it's a documented prereq, not re-issued here."""
        cat, p = self.cfg["catalog"], self.cfg["prefix"]
        for sp in self._sp_ids():
            for schema in (f"{p}_ti_intel", f"{p}_ti_risk", f"{p}_ti_tools"):
                for priv in ("USE SCHEMA", "SELECT", "EXECUTE"):
                    self.sql(f"GRANT {priv} ON SCHEMA {cat}.{schema} TO `{sp}`")

    def _grant_sp_app(self, name_prefix):
        """Re-grant the test SPs CAN_USE on a freshly-(re)deployed app (its permissions reset when the app
        is destroyed+recreated). Mirrors 'share my app with my test identity' in the UI. PATCH is additive,
        so the deployer's own CAN_MANAGE is untouched."""
        ids = self._sp_ids()
        app = next((a.get("name") for a in self._apps() if (a.get("name") or "").startswith(name_prefix)), None)
        if not (app and ids):
            return
        acl = [{"service_principal_name": sp, "permission_level": "CAN_USE"} for sp in ids]
        self._cli(["api", "patch", f"/api/2.0/permissions/apps/{app}", "-p", self.cfg["profile"],
                   "--json", json.dumps({"access_control_list": acl})])

    def obo_mcp(self):
        """MCP OBO differential: call the hosted MCP's get_account_risk as the privileged SP and the
        non-privileged SP; assert the SAME tool returns unmasked vs masked (identity forwarded by OBO).
        Needs the venv with `mcp` (tests/e2e/.venv) since the MCP streamable-HTTP client isn't stdlib."""
        priv, reg = self.cfg.get("sp_priv_profile"), self.cfg.get("sp_reg_profile")
        venv_py = os.path.join(os.path.dirname(__file__), ".venv", "bin", "python")
        probe = os.path.join(os.path.dirname(__file__), "mcp_probe.py")
        if not (priv and reg):
            print("  [skip] OBO MCP differential — pass --sp-priv-profile / --sp-reg-profile")
            return
        if not os.path.exists(venv_py):
            print("  [skip] OBO MCP differential — create tests/e2e/.venv with `mcp` (see README)")
            return
        url = self._mcp_app_url()
        if not url:
            return self.check("MCP app url found", False)
        self._grant_sp_schemas()          # re-share schemas + the MCP app with the SPs (post-deploy)
        self._grant_sp_app("mcp-")
        cat, p = self.cfg["catalog"], self.cfg["prefix"]
        acct = self.scalar(f"SELECT account_id FROM {cat}.{p}_ti_risk.account_risk_scores LIMIT 1")

        def mcp_call(profile):
            r = subprocess.run([venv_py, probe, "--app-url", url, "--profile", profile,
                                "--tool", "get_account_risk", "--arg-name", "account_id",
                                "--arg-value", acct], capture_output=True, text=True)
            try:
                return json.loads(r.stdout)
            except Exception:
                return None

        priv_rows, reg_rows = mcp_call(priv), mcp_call(reg)
        if not priv_rows or not reg_rows:
            return self.check("MCP OBO reachable for both SPs", False,
                              f"priv={bool(priv_rows)} reg={bool(reg_rows)}")
        self.check("MCP OBO: privileged SP sees UNMASKED name",
                   priv_rows[0].get("customer_name") not in (None, "***REDACTED***"))
        self.check("MCP OBO: non-privileged SP sees REDACTED name",
                   reg_rows[0].get("customer_name") == "***REDACTED***")

    def _agent_endpoint_name(self):
        out = self._cli(["api", "get", "/api/2.0/serving-endpoints", "-p", self.cfg["profile"]]).stdout
        for e in json.loads(out or "{}").get("endpoints", []):
            n = e.get("name") or ""
            if n.startswith("agents_") and self.cfg["prefix"] in n:
                return n
        return None

    def _wait_endpoint_ready(self, name, minutes=15):
        deadline = time.monotonic() + minutes * 60
        while time.monotonic() < deadline:
            out = self._cli(["api", "get", f"/api/2.0/serving-endpoints/{name}", "-p", self.cfg["profile"]]).stdout
            st = json.loads(out or "{}").get("state", {})
            if st.get("ready") == "READY" and st.get("config_update") == "NOT_UPDATING":
                return True
            time.sleep(20)
        return False

    def agent_predict(self):
        """Live-query the deployed agent with an incident id; assert it returns a valid play AND that its
        tool evidence is populated (catches the silent-empty-evidence regression — the agent must actually
        exercise the governed tools, not just reason from the prompt)."""
        name = self._agent_endpoint_name()
        if not name:
            return self.check("agent endpoint present", False)
        if not self._wait_endpoint_ready(name):
            return self.check("agent endpoint READY", False, "still updating")
        cat, p = self.cfg["catalog"], self.cfg["prefix"]
        incident = self.scalar(f"SELECT incident_id FROM {cat}.{p}_ti_intel.incidents LIMIT 1")
        out = self._cli(["api", "post", f"/serving-endpoints/{name}/invocations", "-p", self.cfg["profile"],
                         "--json", json.dumps({"input": [{"role": "user", "content": f"Triage {incident}"}]})]).stdout
        try:
            rec = json.loads(json.loads(out)["output"][0]["content"][0]["text"])
        except Exception as e:
            return self.check("agent returns recommendation JSON", False, f"{str(e)[:120]} :: {out[:160]}")
        self.check("agent returns a valid play", rec.get("recommended_play") in ALLOWED_PLAYS,
                   rec.get("recommended_play"))
        populated = [k for k, v in (rec.get("evidence") or {}).items() if v]
        self.check("agent tool evidence populated (not silently empty)", len(populated) >= 1,
                   f"tools with rows: {populated}")

    def reset_c(self):
        """Make Chapter C deterministically re-runnable: clear the runbook + recommendations and put the
        incident queue back to 'new' (triage consumes 'new', so without this a re-run finds nothing)."""
        cat, p = self.cfg["catalog"], self.cfg["prefix"]
        self.sql(f"DELETE FROM {cat}.{p}_ti_intel.runbook_rules")
        self.sql(f"DELETE FROM {cat}.{p}_ti_risk.triage_recommendations")
        self.sql(f"UPDATE {cat}.{p}_ti_intel.incidents SET status='new' WHERE status IN ('triaged','uncovered')")
        print("  reset Chapter C state (rules cleared, incidents → new)")

    def stage_c(self):
        print("\n== Chapter C — agent loops ==")
        cat, p = self.cfg["catalog"], self.cfg["prefix"]
        intel, risk = f"{cat}.{p}_ti_intel", f"{cat}.{p}_ti_risk"
        if self.cfg.get("reset") and not self.cfg["asserts_only"]:
            self.reset_c()
        if not self.cfg["asserts_only"]:
            self.check("C deploy", *self.bundle_deploy("chapter-c-loops"))
            ok, detail = self.bundle_run("chapter-c-loops", "runbook_builder")
            if not self.check("runbook_builder run", ok, detail):
                return
        # Rule checks query regardless of status: right after a fresh build they're 'proposed'; in
        # --asserts-only (rules already approved by a prior run) they're 'approved'. Either is valid.
        self.check("runbook rules synthesized",
                   self.scalar_int(f"SELECT count(*) FROM {intel}.runbook_rules") > 0)
        st, _c, rows = self.sql(f"SELECT action_plan FROM {intel}.runbook_rules")
        plans_ok = st == "SUCCEEDED" and rows and all(
            json.loads(r[0])[-1]["action"] == "recommend_action" for r in rows)
        self.check("every action_plan ends in recommend_action", plans_ok)
        # Human approval gate (no-op if already approved — re-runnable).
        self.sql(f"UPDATE {intel}.runbook_rules SET status='approved' WHERE status='proposed'")
        self.check("rules approved", self.scalar_int(
            f"SELECT count(*) FROM {intel}.runbook_rules WHERE status='approved'") > 0)
        if not self.cfg["asserts_only"]:
            ok, detail = self.bundle_run("chapter-c-loops", "triage_runner")
            if not self.check("triage_runner run", ok, detail):
                return
        recs = self.scalar_int(f"SELECT count(*) FROM {risk}.triage_recommendations")
        self.check("recommendations written", recs > 0, f"{recs} recs")
        self.check("triage accuracy clears threshold", *self._accuracy(intel, risk))
        print("  [manual] MLflow traces: open the /Users/<you>/aiapps-chapter-c-triage experiment ▸ Traces")
        # The review app (OBO approval console) + its per-group differential test.
        if not self.cfg["asserts_only"]:
            self.check("review app started", *self.bundle_run("chapter-c-loops", "review_console"))
            self._wait_app_running(name_prefix="review-")
        self.obo_review_app()

    # ---- assertions needing more than one query ----
    def _accuracy(self, intel, risk):
        values = ", ".join(f"('{k}','{v}')" for k, v in EXPECTED_PLAYS.items())
        pct = self.scalar(f"""
          WITH expected(scenario_label, expected_play) AS (SELECT * FROM VALUES {values} AS t(s, e))
          SELECT round(100.0*sum(CASE WHEN tr.recommended_play=e.expected_play THEN 1 ELSE 0 END)/count(*),1)
          FROM {risk}.triage_recommendations tr
          JOIN {intel}.incidents inc ON inc.incident_id = tr.incident_id
          LEFT JOIN expected e ON e.scenario_label = inc.scenario_label
          WHERE tr.recommended_by <> 'triage-agent'""")
        pct = float(pct) if pct is not None else 0.0
        return pct >= self.cfg["accuracy_threshold"], f"{pct}% (>= {self.cfg['accuracy_threshold']}%)"

    def _genie_spaces(self):
        out = self._cli(["api", "get", "/api/2.0/genie/spaces", "-p", self.cfg["profile"]]).stdout
        spaces = json.loads(out or "{}").get("spaces", [])
        return [s for s in spaces if (s.get("title") or "").startswith(f"{self.cfg['prefix']} · Threat Intel")]

    def _apps(self):
        out = self._cli(["api", "get", "/api/2.0/apps", "-p", self.cfg["profile"]]).stdout
        return json.loads(out or "{}").get("apps", [])

    def _wait_app_running(self, name_prefix="mcp-", minutes=8):
        deadline = time.monotonic() + minutes * 60
        while time.monotonic() < deadline:
            for a in self._apps():
                if (a.get("name") or "").startswith(name_prefix):
                    if (a.get("compute_status") or {}).get("state") in ("ACTIVE", "RUNNING"):
                        return True
            time.sleep(20)
        return False

    def _agent_endpoint_exists(self):
        # agents.deploy names the endpoint agents_<catalog>-<schema>-<model> and TRUNCATES it, so match
        # on the stable agents_ prefix + this participant's schema prefix rather than the model name.
        out = self._cli(["api", "get", "/api/2.0/serving-endpoints", "-p", self.cfg["profile"]]).stdout
        eps = json.loads(out or "{}").get("endpoints", [])
        return any((e.get("name") or "").startswith("agents_") and self.cfg["prefix"] in (e.get("name") or "")
                   for e in eps)

    # ---- OBO differential via the review app (the user-facing per-group test) ----
    def _oauth_token(self, profile):
        """An OAuth access token for a profile. Handles M2M SP profiles (client_id/secret →
        client-credentials grant at /oidc/v1/token — `databricks auth token` is U2M-only) and falls back
        to U2M for human profiles. Stdlib only (no SDK/PyPI here). '' if unavailable."""
        import base64
        import configparser
        import os
        import urllib.request
        cfg = configparser.ConfigParser()
        cfg.read(os.path.expanduser("~/.databrickscfg"))
        if profile in cfg and cfg[profile].get("client_id") and cfg[profile].get("client_secret"):
            sec = cfg[profile]
            host = sec.get("host", "").rstrip("/")
            body = b"grant_type=client_credentials&scope=all-apis"
            req = urllib.request.Request(f"{host}/oidc/v1/token", data=body, method="POST")
            basic = base64.b64encode(f"{sec['client_id']}:{sec['client_secret']}".encode()).decode()
            req.add_header("Authorization", f"Basic {basic}")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read()).get("access_token", "")
            except Exception:
                return ""
        out = self._cli(["auth", "token", "-p", profile]).stdout   # U2M fallback
        try:
            return json.loads(out or "{}").get("access_token", "")
        except json.JSONDecodeError:
            return ""

    def _http_get(self, url, token):
        """GET a Databricks App page as the token's identity (OBO). Stdlib only (no PyPI here)."""
        import urllib.request
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8", "replace")

    def _review_app_url(self):
        for app in self._apps():
            if (app.get("name") or "").startswith("review-"):
                return (app.get("url") or "").rstrip("/")
        return None

    def obo_review_app(self):
        """Hit the review app as the privileged SP and the non-privileged SP; assert the SAME app shows
        unmasked vs masked data (OBO forwarding the caller's identity). The masked-data check depends only
        on UC masking (reliable); the badge/button gating depends on the app's me().groups lookup for an
        SP, which we report rather than hard-assert."""
        priv, reg = self.cfg.get("sp_priv_profile"), self.cfg.get("sp_reg_profile")
        if not (priv and reg):
            print("  [skip] OBO review-app differential — pass --sp-priv-profile / --sp-reg-profile")
            return
        url = self._review_app_url()
        if not url:
            return self.check("review app reachable", False, "no review-* app found")
        self._grant_sp_schemas()          # re-share schemas + the review app with the SPs (post-deploy)
        self._grant_sp_app("review-")
        cat, p = self.cfg["catalog"], self.cfg["prefix"]
        incident = self.scalar(f"SELECT incident_id FROM {cat}.{p}_ti_risk.triage_recommendations LIMIT 1")
        if not incident:
            return self.check("review app has a triaged incident to drill", False)
        drill = f"{url}/incident/{incident}"
        try:
            priv_html = self._http_get(drill, self._oauth_token(priv))
            reg_html = self._http_get(drill, self._oauth_token(reg))
        except Exception as e:
            return self.check("review app OBO reachable", False, str(e)[:160])
        self.check("review app: privileged SP sees UNMASKED name",
                   "***REDACTED***" not in priv_html and "customer name" in priv_html.lower())
        self.check("review app: non-privileged SP sees REDACTED name", "***REDACTED***" in reg_html)
        # informational (SP group-lookup caveat): does the badge gate too?
        print(f"    badge(priv)={'privileged' in priv_html}  badge(reg-masked)={'non-privileged' in reg_html}")

    def teardown(self):
        print("\n== teardown ==")
        env = {"PROFILE": self.cfg["profile"], "CATALOG": self.cfg["catalog"],
               "PREFIX": self.cfg["prefix"], "WAREHOUSE": self.cfg["warehouse"]}
        import os
        subprocess.run(["bash", str(ROOT / "tests" / "teardown.sh")], env={**os.environ, **env})

    def summary(self):
        passed = sum(1 for ok, _, _ in self.results if ok)
        print(f"\n===== E2E SUMMARY: {passed}/{len(self.results)} gates passed =====")
        for ok, label, detail in self.results:
            if not ok:
                print(f"  FAIL  {label}  {detail}")
        return all(ok for ok, _, _ in self.results)


def main():
    from _env import load_config_env
    load_config_env()   # pull tests/config.env (your values) into the environment; CLI flags still win
    env = os.environ.get
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=env("WORKSHOP_PROFILE"))
    ap.add_argument("--groupb-profile", default=env("WORKSHOP_GROUPB_PROFILE"))
    ap.add_argument("--sp-priv-profile", default=env("WORKSHOP_SP_PRIV_PROFILE"), help="M2M profile of the privileged SP (OBO test)")
    ap.add_argument("--sp-reg-profile", default=env("WORKSHOP_SP_REG_PROFILE"), help="M2M profile of the non-privileged SP (OBO test)")
    ap.add_argument("--catalog", default=env("WORKSHOP_CATALOG"))
    ap.add_argument("--privileged-group", default=env("WORKSHOP_PRIVILEGED_GROUP"))
    ap.add_argument("--warehouse", default=env("WORKSHOP_WAREHOUSE"))
    ap.add_argument("--uc-model-name", default="triage_agent")
    ap.add_argument("--accuracy-threshold", type=float, default=80.0,
                    help="min triage accuracy %% (data is separable by design; ~85-92%% expected vs noise)")
    ap.add_argument("--stages", default="a,b,c")
    ap.add_argument("--no-teardown", action="store_true")
    ap.add_argument("--teardown-only", action="store_true")
    ap.add_argument("--asserts-only", action="store_true",
                    help="skip deploy/run; just assert against already-deployed state")
    ap.add_argument("--reset", action="store_true",
                    help="before Chapter C, clear rules/recs and reset incidents to 'new' (re-runnable)")
    a = ap.parse_args()
    missing = [n for n, v in [("profile", a.profile), ("catalog", a.catalog),
                              ("warehouse", a.warehouse), ("privileged-group", a.privileged_group)] if not v]
    if missing:
        sys.exit(f"missing required config: {', '.join(missing)} — set them in tests/config.env "
                 f"(copy tests/config.env.example) or pass --{missing[0]} …")

    cfg = {"profile": a.profile, "groupb_profile": a.groupb_profile, "catalog": a.catalog,
           "privileged_group": a.privileged_group, "warehouse": a.warehouse,
           "uc_model_name": a.uc_model_name, "accuracy_threshold": a.accuracy_threshold,
           "asserts_only": a.asserts_only, "sp_priv_profile": a.sp_priv_profile,
           "sp_reg_profile": a.sp_reg_profile, "reset": a.reset}
    runner = Runner(cfg)
    me = runner.whoami(a.profile)
    cfg["prefix"] = re.sub(r"[^a-zA-Z0-9]", "_", me.split("@")[0]).lower()
    print(f"e2e as {me}  ·  prefix {cfg['prefix']}  ·  catalog {a.catalog}")

    if a.teardown_only:
        runner.teardown()
        return 0

    stages = [s.strip() for s in a.stages.split(",")]
    try:
        if not a.asserts_only:
            runner.preflight()
        if "a" in stages:
            runner.stage_a()
        if "b" in stages:
            runner.stage_b()
        if "c" in stages:
            runner.stage_c()
    finally:
        ok = runner.summary()
        if not a.no_teardown:
            runner.teardown()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
