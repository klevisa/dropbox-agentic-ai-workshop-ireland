#!/usr/bin/env python3
"""Synthetic threat-intel + account-risk data for the Threat-Intel team (Agentic AI Apps Workshop).

Stdlib-only (no external deps), fixed seed for reproducibility. Writes CSVs to ./data/.
Two domains: intel.* (threat feeds/investigations) and risk.* (account scoring service).
"""
import csv, json, os, random
from datetime import date, datetime, timedelta

SEED = 1337
random.seed(SEED)
# Write the CSVs straight into this seed dir (what the Chapter A bundle deploys), and read the frozen
# real-IOC feed from ./real_urlhaus (both live here after the tree was flattened for the workshop).
OUT = os.path.dirname(__file__)
os.makedirs(OUT, exist_ok=True)

TODAY = date(2026, 6, 1)  # pinned so dataset is fully reproducible


def d(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


def ts(days_ago, jitter=True):
    base = datetime(TODAY.year, TODAY.month, TODAY.day) - timedelta(days=days_ago)
    if jitter:
        base += timedelta(seconds=random.randint(0, 86399))
    return base.isoformat(sep=" ")


def write(name, header, rows):
    path = os.path.join(OUT, f"{name}.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  {name:28s} {len(rows):6d} rows -> {path}")


# ---------------------------------------------------------------- vocabularies
ACTOR_HANDLES = [
    "SCATTERED SPIDER", "MIDNIGHT BLIZZARD", "LAZARUS GROUP", "FANCY BEAR", "COZY BEAR",
    "WIZARD SPIDER", "MUDDYWATER", "CHARMING KITTEN", "SANDWORM", "APT41", "KIMSUKY",
    "VOLT TYPHOON", "SILENT CHOLLIMA", "INDRIK SPIDER", "GOSSAMER BEAR", "STATIC KITTEN",
    "MUSTANG PANDA", "OCEAN LOTUS", "DARKHYDRUS", "TURLA", "GAMAREDON", "BLACKBYTE",
    "EVILCORP", "FIN7", "CARBANAK", "TA505", "UNC2452", "STORM-0558", "STORM-1167",
    "GHOSTWRITER", "BLACKCAT", "LOCKBIT", "CL0P", "AKIRA", "QAKBOT-OP", "EMOTET-OP",
    "RHYSIDA", "PLAY", "MEDUSA", "8BASE",
]
MOTIVATIONS = ["financial", "espionage", "hacktivism", "destruction", "insider"]
SOPHIS = ["low", "moderate", "high", "advanced", "strategic"]
REGIONS = ["Eastern Europe", "East Asia", "Middle East", "South Asia", "West Africa",
           "North America", "Southeast Asia", "Unknown"]
SECTORS = ["Technology", "Financial Services", "Healthcare", "Government", "Retail",
           "Media", "Education", "Energy", "Manufacturing", "SaaS / Cloud Storage"]
SEVERITIES = ["low", "medium", "high", "critical"]
CAMPAIGN_STATUS = ["active", "contained", "monitoring", "closed"]
MITRE = ["T1566 Phishing", "T1078 Valid Accounts", "T1486 Data Encrypted for Impact",
         "T1059 Command and Scripting", "T1110 Brute Force", "T1567 Exfil to Cloud",
         "T1098 Account Manipulation", "T1530 Data from Cloud Storage",
         "T1136 Create Account", "T1621 MFA Request Generation", "T1539 Steal Session Cookie"]
IOC_TYPES = ["ipv4", "domain", "url", "sha256", "md5", "email", "filename"]
TLP = ["TLP:CLEAR", "TLP:GREEN", "TLP:AMBER", "TLP:RED"]
FEED_SOURCES = ["Recorded Future", "Mandiant", "CrowdStrike Falcon X", "Internal Honeypot",
                "Abuse.ch", "VirusTotal", "ISAC Share", "Confidential HUMINT", "OSINT Crawler"]
SOURCE_SENSITIVITY = ["public", "partner", "confidential", "restricted"]
ANALYSTS = ["c.ramirez", "m.chen", "j.fletcher", "a.okafor", "p.novak", "s.delgado",
            "t.ibrahim", "r.kowalski"]
INV_STATUS = ["open", "in_review", "escalated", "closed", "false_positive"]
SIGNAL_TYPES = ["impossible_travel", "mass_file_download", "credential_stuffing",
                "anomalous_share_external", "mfa_fatigue", "new_device_burst",
                "api_token_abuse", "geo_velocity", "bulk_account_creation",
                "suspicious_oauth_grant", "data_staging", "off_hours_admin",
                "malware_callback"]
ACTION_TYPES = ["account_suspended", "rate_limited", "forced_password_reset",
                "mfa_enforced", "manual_review", "external_sharing_disabled",
                "session_revoked", "cleared_no_action"]
PLAN_TIERS = ["Basic", "Plus", "Professional", "Standard (Team)", "Advanced (Team)",
              "Enterprise"]
SEGMENTS = ["Consumer", "SMB", "Mid-Market", "Enterprise", "Education", "Reseller"]
COUNTRIES = ["US", "GB", "DE", "FR", "JP", "BR", "IN", "CA", "AU", "NL", "SG", "KR"]
FIRST = ["Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Sam", "Jamie", "Avery",
         "Quinn", "Drew", "Skyler", "Cameron", "Dakota", "Reese", "Parker", "Noor", "Wei",
         "Yuki", "Lars", "Ingrid", "Diego", "Lucia", "Omar", "Fatima", "Kenji", "Mei"]
LAST = ["Park", "Nguyen", "Schmidt", "Rossi", "Kowalski", "Andersen", "Silva", "Khan",
        "OBrien", "Tanaka", "Mueller", "Dubois", "Costa", "Ivanov", "Haddad", "Lindgren",
        "Okafor", "Reyes", "Bauer", "Moreau", "Sato", "Petrov", "Singh", "Romano"]


def pick(seq):
    return random.choice(seq)


# ---------------------------------------------------------------- intel.threat_actors
N_ACTORS = len(ACTOR_HANDLES)
actors = []
for i, name in enumerate(ACTOR_HANDLES, 1):
    first_seen = random.randint(400, 2500)
    actors.append([
        f"TA-{i:04d}", name,
        "; ".join(random.sample(["APT" + str(random.randint(1, 99)), "G" + str(random.randint(1000, 9999)),
                                  name.split()[0].title()], k=2)),
        pick(SOPHIS), pick(MOTIVATIONS), pick(REGIONS),
        d(first_seen), d(random.randint(0, 60)),
        random.random() < 0.75,
    ])
write("threat_actors",
      ["actor_id", "actor_name", "aliases", "sophistication", "motivation",
       "origin_region", "first_seen", "last_seen", "is_active"], actors)

# ---------------------------------------------------------------- intel.campaigns
N_CAMPAIGNS = 120
campaigns = []
for i in range(1, N_CAMPAIGNS + 1):
    actor = pick(actors)
    start = random.randint(5, 700)
    end = "" if random.random() < 0.4 else d(max(0, start - random.randint(10, 200)))
    campaigns.append([
        f"CMP-{i:04d}",
        f"Operation {pick(['Crimson','Iron','Silent','Hollow','Pale','Amber','Frost','Granite','Velvet','Onyx'])} "
        f"{pick(['Tide','Harvest','Vault','Falcon','Cascade','Echo','Lantern','Spire','Drift','Anvil'])}",
        actor[0], pick(SECTORS),
        "; ".join(random.sample(MITRE, k=random.randint(2, 4))),
        pick(SEVERITIES), pick(CAMPAIGN_STATUS),
        d(start), end,
    ])
# --- real-IOC family campaigns ---------------------------------------------
# Real Abuse.ch/URLhaus IOCs are grouped by malware family (Mirai/Mozi/Gafgyt/...). Each family
# gets its OWN campaign row here so pivot_indicator works on real IOCs (every real indicator below
# is linked to one of these campaign_ids). Family campaigns continue the CMP-#### sequence after
# the synthetic ones, are reproducible (seed-driven), and reuse the existing campaigns schema.
REAL_JSONL = os.path.join(OUT, "real_urlhaus", "urlhaus_real.jsonl")
_real_rows = []
if os.path.exists(REAL_JSONL):
    with open(REAL_JSONL) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line:
                _real_rows.append(json.loads(_line))
# stable ordering so sampling/id-assignment is reproducible across runs
_real_rows.sort(key=lambda o: (o.get("indicator_type", ""), o.get("indicator", "")))


def _family_of(o):
    """Derive a malware family for a real IOC from its signature, else its tags, else 'Generic'."""
    sig = (o.get("signature") or "").strip()
    if sig and sig.lower() not in ("none", "null"):
        return sig
    FAM_TAGS = {"mozi": "Mozi", "mirai": "Mirai", "gafgyt": "Gafgyt", "tsunami": "Tsunami",
                "hajime": "Hajime", "clearfake": "ClearFake", "remcosrat": "RemcosRAT",
                "guloader": "GuLoader", "formbook": "Formbook", "amadey": "Amadey",
                "phorpiex": "Phorpiex", "vidar": "Vidar", "stealc": "Stealc",
                "connectwise": "ConnectWise", "kongtuke": "KongTuke", "leethozer": "LeetHozer",
                "tofsee": "Tofsee", "rekoobe": "Rekoobe"}
    for t in (o.get("tags") or []):
        if t and t.lower() in FAM_TAGS:
            return FAM_TAGS[t.lower()]
    return "Generic"


# one campaign per family, deterministic order
_families = sorted({_family_of(o) for o in _real_rows})
FAMILY_CAMPAIGN = {}
_next_cmp = N_CAMPAIGNS + 1
for fam in _families:
    cid = f"CMP-{_next_cmp:04d}"
    FAMILY_CAMPAIGN[fam] = cid
    _next_cmp += 1
    actor = pick(actors)
    campaigns.append([
        cid, f"URLhaus {fam} distribution", actor[0],
        pick(["Technology", "SaaS / Cloud Storage", "Manufacturing", "Energy"]),
        "; ".join(random.sample(MITRE, k=2)),
        pick(["high", "critical", "medium"]), "active",
        d(random.randint(1, 30)), "",
    ])

write("campaigns",
      ["campaign_id", "campaign_name", "actor_id", "target_sector", "mitre_ttps",
       "severity", "status", "start_date", "end_date"], campaigns)


def make_ioc(t):
    if t == "ipv4":
        return ".".join(str(random.randint(1, 254)) for _ in range(4))
    if t == "domain":
        return pick(["secure-", "login-", "cdn-", "update-", "vault-", "dropbox-"]) + \
            pick(["portal", "auth", "sync", "files", "share"]) + pick([".com", ".net", ".io", ".co"])
    if t == "url":
        return "https://" + pick(["cdn", "static", "files", "dl"]) + "." + \
            pick(["xn--malware", "badhost", "evilcdn"]) + ".com/" + \
            "".join(random.choice("abcdef0123456789") for _ in range(8))
    if t == "sha256":
        return "".join(random.choice("abcdef0123456789") for _ in range(64))
    if t == "md5":
        return "".join(random.choice("abcdef0123456789") for _ in range(32))
    if t == "email":
        return f"{pick(['admin','it-support','hr','no-reply','billing'])}@{pick(['secure-mail','acct-verify','dropbox-team'])}.com"
    return pick(["invoice", "report", "update", "resume", "scan"]) + \
        pick(["_2026", "-final", "_urgent"]) + pick([".pdf.exe", ".docm", ".lnk", ".iso", ".zip"])


# ---------------------------------------------------------------- intel.indicators
N_IOC = 3200
iocs = []
for i in range(1, N_IOC + 1):
    t = pick(IOC_TYPES)
    camp = pick(campaigns)
    fs = random.randint(0, 700)
    iocs.append([
        f"IOC-{i:06d}", t, make_ioc(t), camp[0],
        random.randint(40, 100), pick(TLP),
        pick(FEED_SOURCES), pick(SOURCE_SENSITIVITY),
        d(fs), d(max(0, fs - random.randint(0, 60))),
        random.random() < 0.7,
    ])
# --- append REAL Abuse.ch / URLhaus IOCs ------------------------------------
# Map every real indicator from the frozen jsonl onto the EXISTING 11-column indicators schema and
# APPEND, so ti_intel.indicators ends up synthetic + real. IDs continue the IOC-##### sequence.
# A parallel `indicator_intel` side table carries the URLhaus verdict detail (host / url_status /
# threat / tags / family / payload hashes) keyed by indicator_id — this is what gives the mock a
# faithful family/tags verdict WITHOUT changing the indicators column set. The mock LEFT JOINs it.
RECENT_REAL = date(2026, 6, 11)  # snapshot pull date (jsonl timestamps are 2026-06-11 UTC)


def _real_date(o):
    raw = (o.get("date_added") or "").strip()
    if len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10]).isoformat()
        except ValueError:
            pass
    return RECENT_REAL.isoformat()


def _is_dotted_ip(h):
    parts = (h or "").split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def _map_type(o):
    t = o["indicator_type"]
    if t == "url":
        return "url"
    if t == "host":
        return "ipv4" if _is_dotted_ip(o["indicator"]) else "domain"
    return t  # md5 / sha256 pass through


intel_rows = []  # side table: ti_intel.indicator_intel
n_real = 0
real_id = N_IOC  # continue IOC sequence after the synthetic ids
for o in _real_rows:
    real_id += 1
    iid = f"IOC-{real_id:06d}"
    itype = _map_type(o)
    val = o["indicator"]
    fam = _family_of(o)
    camp = FAMILY_CAMPAIGN[fam]
    da = _real_date(o)
    url_status = o.get("url_status") or ""
    # hashes are always treated active; urls follow url_status (online->active); hosts active
    if o["indicator_type"] in ("md5", "sha256"):
        active = True
    elif o["indicator_type"] == "url":
        active = (url_status == "online")
    else:  # host
        active = True
    iocs.append([
        iid, itype, val, camp,
        random.randint(80, 95), "TLP:CLEAR",
        "Abuse.ch", "public",
        da, da, active,
    ])
    n_real += 1
    # side table verdict detail
    tags = ";".join(o.get("tags") or [])
    host = o.get("host") or (val if o["indicator_type"] == "host" else "")
    intel_rows.append([
        iid, val, o["indicator_type"],
        host, url_status, (o.get("threat") or ""), tags, fam,
        (o.get("file_type") or ""),
        (val if o["indicator_type"] == "md5" else ""),
        (val if o["indicator_type"] == "sha256" else ""),
        (o.get("source_ref") or o.get("linked_url") or ""),
        da,
    ])

write("indicators",
      ["indicator_id", "indicator_type", "indicator_value", "campaign_id", "confidence",
       "tlp", "source", "source_sensitivity", "first_seen", "last_seen", "is_active"], iocs)

# --- ti_intel.indicator_intel (optional side table; mock LEFT JOINs for verdict fidelity) ----
write("indicator_intel",
      ["indicator_id", "indicator_value", "urlhaus_type", "host", "url_status", "threat",
       "tags", "family", "payload_file_type", "payload_md5", "payload_sha256",
       "urlhaus_reference", "date_added"], intel_rows)
print(f"  (real IOCs appended: {n_real} from Abuse.ch/URLhaus across {len(FAMILY_CAMPAIGN)} family campaigns)")

# ---------------------------------------------------------------- risk.accounts
N_ACCOUNTS = 2000
accounts = []
for i in range(1, N_ACCOUNTS + 1):
    fn, ln = pick(FIRST), pick(LAST)
    seg = pick(SEGMENTS)
    accounts.append([
        f"ACC-{i:06d}", f"{fn} {ln}",
        f"{fn.lower()}.{ln.lower()}{random.randint(1,999)}@{pick(['gmail.com','outlook.com','proton.me','company.com','edu.org'])}",
        seg, pick(PLAN_TIERS), pick(REGIONS), pick(COUNTRIES),
        d(random.randint(30, 2900)),
        pick(["active", "active", "active", "suspended", "limited", "under_review"]),
    ])
write("accounts",
      ["account_id", "customer_name", "email", "segment", "plan_tier", "region",
       "country", "signup_date", "status"], accounts)

# ---------------------------------------------------------------- risk.risk_signals
# Only a subset of accounts are "interesting". Raw behavioral telemetry = sensitive.
flagged = random.sample(accounts, k=550)
signals = []
sid = 1
for acc in flagged:
    for _ in range(random.randint(1, 9)):
        st = pick(SIGNAL_TYPES)
        signals.append([
            f"SIG-{sid:07d}", acc[0], st,
            round(random.uniform(0.1, 1.0), 3),
            round(random.uniform(0.5, 5.0), 2),
            ts(random.randint(0, 90)),
        ])
        sid += 1
write("risk_signals",
      ["signal_id", "account_id", "signal_type", "signal_value", "weight",
       "observed_at"], signals)

# ---------------------------------------------------------------- risk.account_risk_scores
# Mary's cron service: a fresh score per flagged account for the last N days (history).
bands = [("low", 0, 39), ("medium", 40, 69), ("high", 70, 89), ("critical", 90, 100)]
scores = []
scid = 1
for acc in flagged:
    base = random.randint(20, 95)
    acc_sigs = [s for s in signals if s[1] == acc[0]]
    top = pick(acc_sigs)[2] if acc_sigs else pick(SIGNAL_TYPES)
    for day in range(0, 30, random.choice([1, 1, 2, 3])):  # cron cadence variation
        score = max(0, min(100, base + random.randint(-8, 8)))
        band = next(b for b, lo, hi in bands if lo <= score <= hi)
        scores.append([
            f"SCR-{scid:07d}", acc[0], d(day), score, band,
            f"risk-model-v{random.choice(['3.2','3.3','3.4'])}", top,
        ])
        scid += 1
write("account_risk_scores",
      ["score_id", "account_id", "score_date", "risk_score", "risk_band",
       "model_version", "top_signal"], scores)

# ---------------------------------------------------------------- scenario playbook model
# Each investigation is DRIVEN by a scenario so that symptoms <-> response actions are
# correlated and LEARNABLE (with controlled noise). This is what makes the downstream
# runbook-synthesis demo meaningful: ai_query extracts {symptoms, actions} per incident,
# a reduce step generalizes them into a runbook, and a new incident is looked up against it.
# `steps` = investigative DSL skills the analyst ran; `containment` = ACTION_TYPES applied.
#
# SEPARABLE BY DESIGN: every scenario carries a UNIQUE signature symptom (listed first) so the 8
# scenarios are linearly separable — synthesis yields one rule per scenario and the narrative-only
# matcher can disambiguate them. Signatures: account_takeover=impossible_travel,
# malware_delivery=malware_callback, data_exfiltration=mass_file_download, credential_stuffing=
# credential_stuffing, phishing_wave=suspicious_oauth_grant, insider_activity=off_hours_admin,
# api_token_abuse=api_token_abuse, benign=new_device_burst. Only geo_velocity is shared (cred-stuffing
# + benign), and their signatures still separate them. NOISE (below) still perturbs ~18% for realism.
SCENARIOS = {
    "account_takeover": {
        "titles": ["Reported account takeover", "Confirmed account takeover", "Suspicious account takeover"],
        "symptoms": ["impossible_travel", "mfa_fatigue"],
        "steps": ["get_account_risk", "blast_radius"],
        "containment": ["forced_password_reset", "session_revoked", "mfa_enforced"],
        "outcome": "contained; no customer impact confirmed",
    },
    "malware_delivery": {
        "titles": ["Potential malware delivery", "Confirmed malware delivery", "Malware C2 callback"],
        "symptoms": ["malware_callback"],
        "steps": ["enrich_indicator", "pivot_indicator", "blast_radius"],
        "containment": ["account_suspended"],
        "outcome": "contained; host isolated",
    },
    "data_exfiltration": {
        "titles": ["Potential data exfiltration", "Confirmed data exfiltration", "Anomalous bulk export"],
        "symptoms": ["mass_file_download", "anomalous_share_external"],
        "steps": ["get_account_risk", "blast_radius"],
        "containment": ["external_sharing_disabled", "account_suspended", "manual_review"],
        "outcome": "escalated to IR",
    },
    "credential_stuffing": {
        "titles": ["Credential stuffing wave", "Credential abuse", "Brute-force login burst"],
        "symptoms": ["credential_stuffing", "geo_velocity"],
        "steps": ["get_account_risk"],
        "containment": ["rate_limited", "forced_password_reset"],
        "outcome": "contained",
    },
    "phishing_wave": {
        "titles": ["Phishing wave", "OAuth consent phishing", "Reported phishing"],
        "symptoms": ["suspicious_oauth_grant"],
        "steps": ["get_account_risk"],
        "containment": ["session_revoked", "mfa_enforced", "manual_review"],
        "outcome": "contained; malicious grant revoked",
    },
    "insider_activity": {
        "titles": ["Suspicious insider activity", "Insider data staging", "Anomalous internal access"],
        "symptoms": ["off_hours_admin", "data_staging"],
        "steps": ["get_account_risk", "blast_radius"],
        "containment": ["manual_review", "external_sharing_disabled"],
        "outcome": "monitoring continues; legal hold applied",
    },
    "api_token_abuse": {
        "titles": ["API token abuse", "Automated abuse via API", "Bulk account creation"],
        "symptoms": ["api_token_abuse", "bulk_account_creation"],
        "steps": ["get_account_risk"],
        "containment": ["rate_limited", "session_revoked"],
        "outcome": "contained; token revoked",
    },
    "benign": {
        "titles": ["Anomalous login reviewed", "Low-severity signal review", "Automated alert triage"],
        "symptoms": ["new_device_burst", "geo_velocity"],
        "steps": ["get_account_risk"],
        "containment": ["cleared_no_action"],
        "outcome": "closed as benign; false positive",
    },
}
SCENARIO_KEYS = list(SCENARIOS.keys())
ACTION_PHRASES = {
    "forced_password_reset": "forced a credential reset", "session_revoked": "revoked active sessions",
    "mfa_enforced": "enforced MFA re-enrollment", "account_suspended": "suspended the account",
    "external_sharing_disabled": "disabled external sharing", "manual_review": "queued for manual review",
    "rate_limited": "applied API rate limiting", "cleared_no_action": "took no action (cleared)",
}
STEP_PHRASES = {
    "enrich_indicator": "enriched {n} indicators against the URLhaus feed",
    "pivot_indicator": "pivoted to the associated campaign and sibling indicators",
    "blast_radius": "assessed blast radius across {k} accounts",
    "get_account_risk": "pulled the account risk score (band {band})",
}
NOISE = 0.18  # fraction of investigations that go off-pattern, so the runbook must GENERALIZE

# ---------------------------------------------------------------- intel.investigations
N_INV = 320
investigations = []
inv_ids = []
inv_scenarios = []  # parallel to inv_ids, so account_actions can stay scenario-consistent
for i in range(1, N_INV + 1):
    inv_id = f"INV-{i:05d}"
    inv_ids.append(inv_id)
    scen_key = pick(SCENARIO_KEYS)
    scen = SCENARIOS[scen_key]
    inv_scenarios.append(scen_key)
    related = pick(flagged)[0] if random.random() < 0.7 else ""
    opened = random.randint(0, 365)
    closed = "" if random.random() < 0.45 else ts(max(0, opened - random.randint(1, 90)))
    sev = pick(SEVERITIES)
    # symptoms: the scenario's, with jitter (drop one and/or add a DIFFERENT unrelated one)
    syms = list(scen["symptoms"])
    if random.random() < NOISE and len(syms) > 1:
        syms.pop(random.randrange(len(syms)))
    if random.random() < NOISE:
        syms.append(pick([s for s in SIGNAL_TYPES if s not in syms] or SIGNAL_TYPES))
    syms = list(dict.fromkeys(syms))  # dedupe, preserve order
    # containment: the scenario's, unless noise -> off-pattern response + reclassified outcome
    if random.random() < NOISE:
        cont = [pick(ACTION_TYPES)]
        outcome = pick(["reclassified after review", "closed as benign", "escalated to IR"])
    else:
        cont = scen["containment"]
        outcome = scen["outcome"]
    n_ioc, k_acc, band = random.randint(2, 40), random.randint(1, 6), pick(["low", "medium", "high", "critical"])
    step_txt = "; ".join(STEP_PHRASES[s].format(n=n_ioc, k=k_acc, band=band) for s in scen["steps"])
    step_txt = step_txt[0].upper() + step_txt[1:]
    cont_txt = ", ".join(ACTION_PHRASES.get(c, c.replace("_", " ")) for c in cont)
    investigations.append([
        inv_id, pick(scen["titles"]), pick(ANALYSTS), related, pick(INV_STATUS), sev,
        ts(opened), closed,
        # summary = CS-safe one-liner
        f"{pick(['Investigated','Reviewed','Triaged'])} {sev} {scen_key.replace('_',' ')} signal cluster; {outcome}.",
        # detailed_notes = SENSITIVE (symptoms -> investigative steps -> response -> outcome)
        f"Pivoted from {pick(FEED_SOURCES)} feed. Observed {' and '.join(syms)}. {step_txt}. "
        f"Response: {cont_txt}. Outcome: {outcome}. "
        f"HUMINT note: {pick(['source A reliability B2','do not disclose to partner orgs','sub-judice — legal hold'])}.",
        # source_methods = SENSITIVE
        f"{pick(['Honeypot telemetry','Confidential partner feed','Internal EDR','Court-ordered log pull'])}; "
        f"collection method {pick(['passive DNS','endpoint memory capture','authenticated API audit','undercover channel'])}.",
    ])
write("investigations",
      ["investigation_id", "title", "analyst", "related_account_id", "status", "severity",
       "opened_at", "closed_at", "summary", "detailed_notes", "source_methods"], investigations)

# ---------------------------------------------------------------- risk.account_actions
# Actions linked to an investigation inherit that investigation's scenario containment, so
# the action vocabulary stays consistent with the investigation notes.
N_ACTIONS = 480
actions = []
for i in range(1, N_ACTIONS + 1):
    acc = pick(flagged)
    if random.random() < 0.6:
        idx = random.randrange(len(inv_ids))
        inv_ref, scen = inv_ids[idx], SCENARIOS[inv_scenarios[idx]]
        cont = [c for c in scen["containment"] if c != "cleared_no_action"] or ["manual_review"]
        atype = pick(cont)
        reason = (f"{inv_scenarios[idx].replace('_',' ').capitalize()} response "
                  f"— {pick(['temporary protective measure','pending verification','resolved after review'])}.")
    else:
        inv_ref, atype = "", pick(ACTION_TYPES)
        reason = (f"{pick(['Elevated risk score','Multiple high-severity signals','Linked to active campaign','Policy violation','Customer-reported compromise'])} "
                  f"— {pick(['temporary protective measure','pending verification','resolved after review','awaiting customer response'])}.")
    actions.append([f"ACT-{i:05d}", acc[0], atype, reason, pick(ANALYSTS), ts(random.randint(0, 120)), inv_ref])
write("account_actions",
      ["action_id", "account_id", "action_type", "reason_summary", "taken_by",
       "taken_at", "related_investigation_id"], actions)

# ---------------------------------------------------------------- intel.incidents
# The ARRIVING QUEUE the triage agent works (every ~15 min). Each incident bundles a free-text
# narrative (symptom-rich -> Stage-1 ai_query extraction), a REAL IOC value (so live enrichment
# returns a verdict), and an account (the entity-pivot glue). `scenario_label` is HIDDEN
# ground-truth for eval. Backlog spread over the trailing ~21 days; status='new' so the triage
# agent has work to do. A small trickle generator (triage job) adds fresh ones during a live demo.
N_INCIDENTS = 300
INC_NOISE = 0.18
# REAL malware IOCs (URLhaus is a malware-URL feed) are reserved for the malware/C2 scenario,
# where enrich/pivot/blast_radius and a family attribution actually make sense. The account-
# behaviour scenarios instead get a scenario-appropriate SYNTHETIC indicator (a suspicious login
# IP / lookalike domain) and NO malware-family claim — so we never assert "Mirai" on a phishing
# or credential-stuffing case. (Cohesion fix.)
_real_pool = [(_map_type(o), o["indicator"], _family_of(o), (o.get("url_status") == "online"))
              for o in _real_rows] or [("ipv4", "203.0.113.7", "Generic", True)]
_real_online = [r for r in _real_pool if r[3]] or _real_pool
SCENARIO_IOC_TYPE = {"account_takeover": "ipv4", "credential_stuffing": "ipv4",
                     "api_token_abuse": "ipv4", "benign": "ipv4", "phishing_wave": "domain",
                     "data_exfiltration": "domain", "insider_activity": "domain"}
incidents = []
for i in range(1, N_INCIDENTS + 1):
    scen_key = pick(SCENARIO_KEYS)
    scen = SCENARIOS[scen_key]
    acc = pick(flagged)
    if scen_key == "malware_delivery":
        itype, ival, fam, _on = pick(_real_online)              # real URLhaus malware IOC
        attribution = f"; suspected {fam} activity" if fam != "Generic" else ""
        verb = "Flagged indicator"
    else:
        itype = SCENARIO_IOC_TYPE.get(scen_key, "ipv4")
        ival = make_ioc(itype)                                  # scenario-appropriate synthetic IOC
        attribution = ""
        verb = "Source indicator"
    syms = list(scen["symptoms"])
    if random.random() < INC_NOISE and len(syms) > 1:
        syms.pop(random.randrange(len(syms)))
    if random.random() < INC_NOISE:
        syms.append(pick([s for s in SIGNAL_TYPES if s not in syms] or SIGNAL_TYPES))
    syms = list(dict.fromkeys(syms))
    sev = pick(SEVERITIES)
    narrative = (
        f"Alert on account {acc[0]} ({acc[3]} / {acc[5]}): observed {' and '.join(syms)}. "
        f"{verb} {ival} ({itype}) in associated telemetry{attribution}. "
        f"Severity {sev}. Awaiting triage."
    )
    incidents.append([
        f"INC-{i:05d}", ts(random.randint(0, 21)), narrative, ival, itype, acc[0], "new", scen_key,
    ])
write("incidents",
      ["incident_id", "created_at", "narrative", "indicator_value", "indicator_type",
       "account_id", "status", "scenario_label"], incidents)

print(f"\nDone. Seed={SEED}, pinned date={TODAY}. CSVs in {OUT}/")
