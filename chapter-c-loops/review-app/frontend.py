"""Chapter C — Review console · FRONT END (data -> HTML). No SQL or identity logic here.

This is the "draw the page" half of the app. Every function takes ordinary Python values (the dicts /
lists `backend.py` returns) and gives back HTML strings. It imports no FastAPI and no Databricks SDK —
so you can read the entire UI in one place without any backend noise. `app.py` glues the two halves:
fetch with `backend.*`, render with `frontend.*`.

Sections that can fail independently (the incident drill's intel / risk cards) are passed in as a small
`(data, error)` pair; this layer just renders whichever is set.
"""
import html
import json


def esc(v):
    return html.escape("" if v is None else str(v))


# --- the page shell (header + nav + the caller badge) ----------------------------------------------
CSS = """
:root { --bg:#0f1117; --panel:#181b24; --line:#2a2f3a; --fg:#e6e8ee; --mut:#9aa3b2;
        --accent:#ff6b4a; --ok:#3ddc84; --no:#ff5c7a; --chip:#222732; }
* { box-sizing:border-box; }
body { margin:0; font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--fg); }
header { display:flex; align-items:center; gap:18px; padding:14px 22px; background:var(--panel);
         border-bottom:1px solid var(--line); position:sticky; top:0; z-index:5; }
header h1 { font-size:16px; margin:0; letter-spacing:.3px; }
nav a { color:var(--mut); text-decoration:none; margin-right:16px; font-weight:600; }
nav a.active, nav a:hover { color:var(--fg); }
.who { margin-left:auto; font-size:12px; color:var(--mut); text-align:right; }
.badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:700; }
.b-priv { background:#1d3a2a; color:var(--ok); }
.b-reg { background:#3a2030; color:var(--no); }
.b-sp { background:#33363f; color:var(--mut); }
main { padding:22px; max-width:1180px; margin:0 auto; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px 18px; margin-bottom:14px; }
.card h3 { margin:0 0 4px; font-size:15px; }
.meta { color:var(--mut); font-size:12px; margin-bottom:10px; }
.chip { display:inline-block; background:var(--chip); border:1px solid var(--line); border-radius:6px;
        padding:1px 7px; margin:1px 3px 1px 0; font-size:11px; color:var(--mut); }
pre { background:#0b0d13; border:1px solid var(--line); border-radius:8px; padding:10px 12px;
      overflow:auto; font-size:12px; color:#cdd6e6; margin:6px 0; }
.row { display:flex; gap:8px; align-items:center; margin-top:10px; }
button { font:inherit; font-weight:700; border:0; border-radius:7px; padding:7px 16px; cursor:pointer; }
.approve { background:var(--ok); color:#062313; }
.reject { background:var(--no); color:#2a0612; }
table { width:100%; border-collapse:collapse; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); font-size:13px; vertical-align:top; }
th { color:var(--mut); font-size:11px; text-transform:uppercase; letter-spacing:.5px; }
a.link { color:var(--accent); text-decoration:none; }
a.link:hover { text-decoration:underline; }
.empty { color:var(--mut); padding:24px; text-align:center; border:1px dashed var(--line); border-radius:10px; }
.note { background:#2a2410; border:1px solid #4a3f14; color:#e8d48a; padding:10px 14px;
        border-radius:8px; font-size:13px; margin-bottom:14px; }
.kv { display:grid; grid-template-columns:160px 1fr; gap:4px 14px; font-size:13px; }
.kv .k { color:var(--mut); }
.conf { font-weight:700; }
.st { font-weight:700; font-size:11px; padding:2px 8px; border-radius:10px; }
.st-proposed { background:#33363f; color:#e8d48a; }
.st-approved { background:#1d3a2a; color:var(--ok); }
.st-rejected { background:#3a2030; color:var(--no); }
form.inline { display:inline; }
.ev-h { font-weight:700; font-size:13px; margin:16px 0 6px; display:flex; align-items:center; gap:8px; }
.ev-h:first-child { margin-top:4px; }
.ev-h .chip { font-weight:600; }
.ev-t { border:1px solid var(--line); border-radius:8px; overflow:hidden; }
.ev-t th { background:#0b0d13; }
.ev-t td { color:#cdd6e6; }
.ev-t tr:last-child td { border-bottom:0; }
.play { background:#2a1d10; border:1px solid #5a3a17; color:var(--accent); font-weight:700;
        padding:2px 10px; border-radius:10px; font-size:12px; }
"""


def page(body, *, identity, obo, is_privileged, active="queue"):
    """Wrap a body fragment in the full HTML page. The caller badge is derived from identity flags the
    backend computed (privileged = unmasked + can promote; non-privileged = masked + read-only)."""
    if obo and is_privileged:
        badge = '<span class="badge b-priv">privileged &middot; unmasked &middot; can promote</span>'
    elif obo:
        badge = '<span class="badge b-reg">non-privileged &middot; masked &middot; read-only</span>'
    else:
        badge = '<span class="badge b-sp">app-SP (no OBO)</span>'
    who = f"{esc(identity)}<br>{badge}"
    nav_queue = "active" if active == "queue" else ""
    nav_feed = "active" if active == "feed" else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Runbook Review Console</title>
<style>{CSS}</style></head><body>
<header>
  <h1>&#x1F6E1; Runbook Review Console</h1>
  <nav>
    <a class="{nav_queue}" href="/">Review queue</a>
    <a class="{nav_feed}" href="/triage">Triage feed</a>
  </nav>
  <div class="who">{who}</div>
</header>
<main>{body}</main></body></html>"""


# --- small reusable fragments ----------------------------------------------------------------------
def note(msg):
    return f'<div class="note">{esc(msg)}</div>'


def error(msg):
    """Render a query error. A permission denial is reframed as the governance gate working — that's the
    teaching moment, not a bug."""
    msg = str(msg)
    if "PERMISSION" in msg.upper() or "not authorized" in msg.lower():
        return ('<div class="note">Permission denied for your identity &mdash; this is the '
                f'governance gate working as intended.<br><small>{esc(msg)}</small></div>')
    return f'<div class="note">Query error: {esc(msg)}</div>'


def table_missing(name):
    return (f'<div class="empty">Table <code>{esc(name)}</code> does not exist yet.<br>'
            "Run the Chapter C jobs (runbook_builder / triage_runner) first; this screen populates "
            "once those tables land.</div>")


def empty(msg):
    return f'<div class="empty">{esc(msg)}</div>'


def back_link():
    return '<div class="meta"><a class="link" href="/triage">&larr; back to triage feed</a></div>'


# --- screen 1: review queue ------------------------------------------------------------------------
def queue(rules, can_promote, banner=""):
    if not rules:
        return (banner + "<h2>Review queue</h2>"
                + '<div class="empty">No proposed rules awaiting review. '
                  "The runbook-builder job writes rules with status=&lsquo;proposed&rsquo; here.</div>")
    cards = "".join(_rule_card(r, can_promote) for r in rules)
    return banner + f"<h2>Review queue &mdash; {len(rules)} proposed rule(s)</h2>" + cards


def _rule_card(r, can_promote):
    rule_id = r.get("rule_id")
    ap_raw = r.get("action_plan")
    try:                                            # pretty-print the plan JSON if it parses
        ap = json.dumps(json.loads(ap_raw), indent=2) if ap_raw else "(none)"
    except (TypeError, ValueError):
        ap = ap_raw or "(none)"
    conf = r.get("confidence")
    conf_s = f"{float(conf):.2f}" if conf not in (None, "") else "&mdash;"
    if can_promote:
        controls = (
            '<div class="row">'
            '<form class="inline" method="post" action="/approve">'
            f'<input type="hidden" name="rule_id" value="{esc(rule_id)}">'
            '<button class="approve" type="submit">Approve</button></form>'
            '<form class="inline" method="post" action="/reject">'
            f'<input type="hidden" name="rule_id" value="{esc(rule_id)}">'
            '<button class="reject" type="submit">Reject</button></form></div>')
    else:
        controls = ('<div class="meta">Approve/Reject hidden &mdash; promotion requires '
                    'MODIFY on runbook_rules (the privileged group).</div>')
    return f"""<div class="card">
      <h3>{esc(r.get('name'))} <span class="chip">{esc(rule_id)}</span>
          <span class="st st-proposed">proposed</span></h3>
      <div class="meta">scenario hint: <b>{esc(r.get('scenario_hint'))}</b>
         &middot; evidence: {esc(r.get('evidence_count'))}
         &middot; confidence: <span class="conf">{conf_s}</span>
         &middot; by {esc(r.get('created_by'))} at {esc(r.get('created_at'))}</div>
      <div class="kv"><div class="k">symptom pattern</div><div>{esc(r.get('symptom_pattern'))}</div>
         <div class="k">rationale</div><div>{esc(r.get('rationale'))}</div></div>
      <div class="meta" style="margin-top:8px">action plan</div>
      <pre>{esc(ap)}</pre>
      {controls}</div>"""


# --- screen 2: triage feed -------------------------------------------------------------------------
def feed(recs):
    if not recs:
        return ("<h2>Triage feed</h2>"
                + '<div class="empty">No recommendations yet. The triage_runner job writes '
                  "recommendations here once it runs against new incidents.</div>")
    trs = "".join(_feed_row(r) for r in recs)
    return (f"<h2>Triage feed &mdash; {len(recs)} recommendation(s)</h2>"
            "<table><tr><th>Incident</th><th>Account</th><th>Indicator</th><th>Matched rule</th>"
            "<th>Recommended play</th><th>Rationale</th><th>When</th></tr>"
            + trs + "</table>")


def _feed_row(r):
    inc = r.get("incident_id")
    inc_link = f'<a class="link" href="/incident/{esc(inc)}">{esc(inc)}</a>' if inc else "&mdash;"
    return (f"<tr><td>{inc_link}</td><td>{esc(r.get('account_id'))}</td>"
            f"<td>{esc(r.get('indicator_value'))}</td><td>{esc(r.get('matched_rule_id'))}</td>"
            f'<td><span class="chip">{esc(r.get("recommended_play"))}</span></td>'
            f"<td>{esc(r.get('rationale'))}</td><td>{esc(r.get('recommended_at'))}</td></tr>")


# --- screen 3: incident drill (composed of independent cards) --------------------------------------
def incident(inc, rec, intel, risk):
    """`inc` is the incident dict; `rec` is the recommendation dict (or None); `intel` and `risk` are
    (data, error) pairs so a single failing/permission-blocked section becomes one error card instead
    of a dead page."""
    return (_incident_card(inc)
            + _recommendation_cards(rec)
            + _intel_card(intel)
            + _risk_card(risk))


def _incident_card(inc):
    return f"""<div class="card"><h3>Incident {esc(inc.get('incident_id'))}
        <span class="st st-proposed">{esc(inc.get('status'))}</span></h3>
      <div class="kv">
        <div class="k">created</div><div>{esc(inc.get('created_at'))}</div>
        <div class="k">indicator</div><div>{esc(inc.get('indicator_value'))} <span class="chip">{esc(inc.get('indicator_type'))}</span></div>
        <div class="k">account</div><div>{esc(inc.get('account_id'))}</div>
        <div class="k">scenario label</div><div>{esc(inc.get('scenario_label'))} <span class="chip">ground truth &middot; hidden from the agent</span></div>
      </div>
      <div class="meta" style="margin-top:8px">narrative</div>
      <pre>{esc(inc.get('narrative'))}</pre></div>"""


def _recommendation_cards(rec):
    if not rec:
        return ('<div class="card"><h3>Evidence gathered</h3>'
                '<div class="meta">No recommendation for this incident yet &mdash; run triage_runner.</div></div>')
    rec_card = f"""<div class="card"><h3>Agent recommendation
        <span class="play">{esc(rec.get('recommended_play'))}</span></h3>
      <div class="kv">
        <div class="k">matched rule</div><div>{esc(rec.get('matched_rule_id'))}</div>
        <div class="k">rationale</div><div>{esc(rec.get('rationale'))}</div>
        <div class="k">decided at</div><div>{esc(rec.get('recommended_at'))}</div></div></div>"""
    evidence_card = (
        '<div class="card"><h3>Evidence gathered '
        '<span class="meta" style="font-weight:400">&mdash; what the triage agent ran, point-in-time</span></h3>'
        + render_evidence(rec.get("evidence") or {}) + "</div>")
    return rec_card + evidence_card


def _intel_card(intel):
    inner = '<div class="card"><h3>URLhaus verdict</h3>'
    if intel.error:
        return inner + intel.error + "</div>"
    row = intel.data
    if not row:
        return inner + '<div class="meta">No URLhaus intel row for this indicator (not in feed).</div></div>'
    chips = "".join(f'<span class="chip">{esc(t)}</span>' for t in (row.get("tags") or "").split(";") if t)
    return inner + f"""<div class="kv">
      <div class="k">family</div><div>{esc(row.get('family'))}</div>
      <div class="k">threat</div><div>{esc(row.get('threat'))}</div>
      <div class="k">url_status</div><div>{esc(row.get('url_status'))}</div>
      <div class="k">type / host</div><div>{esc(row.get('urlhaus_type'))} &middot; {esc(row.get('host'))}</div>
      <div class="k">tags</div><div>{chips or '&mdash;'}</div>
      <div class="k">reference</div><div>{esc(row.get('urlhaus_reference'))}</div></div></div>"""


def _risk_card(risk):
    inner = '<div class="card"><h3>Account &amp; latest risk</h3>'
    if risk.error:
        return inner + risk.error + "</div>"
    row = risk.data
    if not row:
        return inner + '<div class="meta">No account row found.</div></div>'
    return inner + f"""<div class="kv">
      <div class="k">account</div><div>{esc(row.get('account_id'))}</div>
      <div class="k">customer name</div><div>{esc(row.get('customer_name'))} <span class="chip">PII / masked unless privileged</span></div>
      <div class="k">segment / tier</div><div>{esc(row.get('segment'))} &middot; {esc(row.get('plan_tier'))}</div>
      <div class="k">region / status</div><div>{esc(row.get('region'))} &middot; {esc(row.get('status'))}</div>
      <div class="k">risk score</div><div><b>{esc(row.get('risk_score'))}</b> &middot; band {esc(row.get('risk_band'))}</div>
      <div class="k">as of</div><div>{esc(row.get('score_date'))}</div>
      <div class="k">top signal</div><div>{esc(row.get('top_signal'))}</div></div></div>"""


# --- evidence rendering (the per-tool outputs stored in triage_recommendations.evidence) -----------
EVIDENCE_TITLES = {
    "enrich_indicator": "Enrichment &mdash; URLhaus verdict",
    "pivot_indicator": "Pivot &mdash; campaign &amp; threat actor",
    "blast_radius": "Blast radius &mdash; affected accounts",
    "get_account_risk": "Account risk",
    "get_account_actions": "Prior protective actions",
}
EVIDENCE_ORDER = ["enrich_indicator", "pivot_indicator", "blast_radius",
                  "get_account_risk", "get_account_actions"]


def _ev_cell(col, val):
    """Render one evidence cell: lists and ';'-delimited tags become chips; blanks become a dash."""
    if val is None or val == "":
        return "&mdash;"
    if isinstance(val, list):
        return "".join(f'<span class="chip">{esc(x)}</span>' for x in val) or "&mdash;"
    text = str(val)
    if col in ("tags", "sibling_indicators") and ";" in text:
        return "".join(f'<span class="chip">{esc(t)}</span>' for t in text.split(";") if t)
    return esc(text)


def render_evidence(evidence):
    """Turn the evidence dict {action: [row, ...]} into titled tables — one block per tool the agent ran."""
    if not evidence:
        return '<div class="meta">No evidence recorded for this recommendation.</div>'
    order = [a for a in EVIDENCE_ORDER if a in evidence] + [a for a in evidence if a not in EVIDENCE_ORDER]
    blocks = []
    for action in order:
        rows = evidence.get(action) or []
        header = (f'<div class="ev-h">{EVIDENCE_TITLES.get(action, esc(action))}'
                  f'<span class="chip">{esc(action)}</span></div>')
        if not rows:
            blocks.append(header + '<div class="meta">no rows returned (clean / not in feed)</div>')
            continue
        cols = list(rows[0].keys())
        thead = "".join(f"<th>{esc(c)}</th>" for c in cols)
        body = "".join("<tr>" + "".join(f"<td>{_ev_cell(c, r.get(c))}</td>" for c in cols) + "</tr>"
                       for r in rows)
        blocks.append(header + f'<table class="ev-t"><tr>{thead}</tr>{body}</table>')
    return "".join(blocks)
