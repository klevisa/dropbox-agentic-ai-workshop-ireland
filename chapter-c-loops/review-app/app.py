"""Chapter C — Review console (a Databricks App, FastAPI). The human approval gate, with a UI.

This is the browser version of the approval beat that `explore.py` does as a notebook `UPDATE`.
Three screens:
  1. Review queue   — proposed runbook_rules; Approve/Reject = governed promotion writes.
  2. Triage feed    — recent triage_recommendations (the triage agent's output).
  3. Incident drill — one incident + its URLhaus verdict + the account's latest risk.

The code is split into three files so each layer reads on its own:
  * backend.py   — identity (OBO) + data access. Runs SQL **as the caller** (so the Chapter A masks
                   apply per identity) and returns plain dicts/lists.
  * frontend.py  — pure data -> HTML. No SQL, no FastAPI; just turns the dicts into pages.
  * app.py       — THIS file: the routes. Each one reads the caller, fetches with `backend.*`, and
                   renders with `frontend.*`. Thin glue, nothing else.

OBO note: a caller in the participant's `privileged_group` sees unmasked detail and the Approve/Reject
buttons; everyone else sees masked data and read-only screens. The buttons are only UX — the real gate
is the UPDATE running as the caller (UC `MODIFY` decides whether it succeeds).

PREREQ (admin, once): a workspace admin must enable user authorization so `user_api_scopes: [sql]`
takes effect; then restart the app. Until then it falls back to the app SP and the writes fail.
"""
import html
from collections import namedtuple

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import backend
import frontend as ui

app = FastAPI(title="Runbook Review Console", version="1.0")

# A fetched-or-failed section: .data is the backend result, .error is a ready-to-render HTML fragment.
Section = namedtuple("Section", "data error")


def _render(body, caller, active):
    """Wrap a body in the page shell, passing the caller's identity flags to the front end."""
    return HTMLResponse(ui.page(body, identity=caller.identity, obo=caller.obo,
                                is_privileged=caller.is_privileged, active=active))


def _section(fetch, table=None):
    """Run one optional backend fetch; on failure capture an HTML error fragment instead of failing the
    whole page (a missing table or a permission denial becomes one error card)."""
    try:
        return Section(data=fetch(), error=None)
    except Exception as e:
        if table and backend.is_missing_table(e):
            return Section(data=None, error=ui.table_missing(table))
        return Section(data=None, error=ui.error(e))


# --- screen 1: review queue ------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def review_queue(request: Request, msg: str = "", merr: str = ""):
    caller = backend.Caller(request)
    banner = (ui.note(msg) if msg else "") + (ui.error(merr) if merr else "")
    try:
        rules = backend.proposed_rules(caller)
    except Exception as e:
        frag = ui.table_missing(backend.RULES_TABLE) if backend.is_missing_table(e) else ui.error(e)
        return _render(banner + "<h2>Review queue</h2>" + frag, caller, "queue")
    body = ui.queue(rules, can_promote=caller.obo and caller.is_privileged, banner=banner)
    return _render(body, caller, "queue")


@app.post("/approve")
def approve(request: Request, rule_id: str = Form(...)):
    return _promote(request, rule_id, "approved")


@app.post("/reject")
def reject(request: Request, rule_id: str = Form(...)):
    return _promote(request, rule_id, "rejected")


def _promote(request: Request, rule_id: str, new_status: str):
    caller = backend.Caller(request)
    try:
        backend.promote_rule(caller, rule_id, new_status)
        return RedirectResponse(f"/?msg=Rule+{rule_id}+{new_status}.", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/?merr={html.escape(str(e))[:300]}", status_code=303)


# --- screen 2: triage feed -------------------------------------------------------------------------
@app.get("/triage", response_class=HTMLResponse)
def triage_feed(request: Request):
    caller = backend.Caller(request)
    try:
        recs = backend.triage_feed(caller)
    except Exception as e:
        frag = ui.table_missing(backend.RECS_TABLE) if backend.is_missing_table(e) else ui.error(e)
        return _render("<h2>Triage feed</h2>" + frag, caller, "feed")
    return _render(ui.feed(recs), caller, "feed")


# --- screen 3: incident drill ----------------------------------------------------------------------
@app.get("/incident/{incident_id}", response_class=HTMLResponse)
def incident_drill(request: Request, incident_id: str):
    caller = backend.Caller(request)
    back = ui.back_link()
    # The incident itself is required — if it fails, there's nothing to drill into.
    try:
        inc = backend.incident(caller, incident_id)
    except Exception as e:
        frag = ui.table_missing(backend.INCIDENTS_TABLE) if backend.is_missing_table(e) else ui.error(e)
        return _render(back + frag, caller, "feed")
    if not inc:
        return _render(back + ui.empty(f"No incident {incident_id} found."), caller, "feed")

    # Supporting sections are independent; a failure in one becomes an error card, not a dead page.
    try:
        rec = backend.latest_recommendation(caller, incident_id)
    except Exception:
        rec = None
    intel = _section(lambda: backend.indicator_intel(caller, inc["indicator_value"]), backend.INTEL_TABLE)
    risk = _section(lambda: backend.account_risk(caller, inc["account_id"]), backend.ACCOUNTS_TABLE)
    return _render(back + ui.incident(inc, rec, intel, risk), caller, "feed")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "catalog": backend.CATALOG, "prefix": backend.PREFIX}
