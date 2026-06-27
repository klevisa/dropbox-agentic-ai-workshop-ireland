#!/usr/bin/env bash
# Reusable teardown for one participant's deployment (amended from the manual teardown).
# Destroys all three bundles, drops the per-user schemas, and deletes the participant's Genie spaces.
#
# Values come from tests/config.env (WORKSHOP_PROFILE/CATALOG/PREFIX/WAREHOUSE), or inline env overrides:
#   PROFILE=… CATALOG=… PREFIX=… WAREHOUSE=… tests/teardown.sh
# (run.py passes PROFILE/CATALOG/PREFIX/WAREHOUSE directly when it calls this.)
#
# `bundle destroy` only removes bundle-tracked resources (jobs/app). Everything created imperatively or
# inside notebooks — schemas/tables/UC functions/masks, Genie spaces, the agent SERVING ENDPOINT, and
# the Chapter C MLflow EXPERIMENT — is not bundle-tracked, so we remove those explicitly here.
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR/.."
[ -f "$DIR/config.env" ] && { set -a; . "$DIR/config.env"; set +a; }   # local values (gitignored)
PROFILE="${PROFILE:-${WORKSHOP_PROFILE:-}}"
CATALOG="${CATALOG:-${WORKSHOP_CATALOG:-}}"
PREFIX="${PREFIX:-${WORKSHOP_PREFIX:-}}"
WAREHOUSE="${WAREHOUSE:-${WORKSHOP_WAREHOUSE:-}}"
: "${PROFILE:?set PROFILE or WORKSHOP_PROFILE (tests/config.env)}"
: "${CATALOG:?set CATALOG or WORKSHOP_CATALOG}"
: "${PREFIX:?set PREFIX or WORKSHOP_PREFIX}"
: "${WAREHOUSE:?set WAREHOUSE or WORKSHOP_WAREHOUSE}"
echo "teardown: catalog=$CATALOG prefix=$PREFIX profile=$PROFILE"

echo "== 1. destroy bundles =="
for ch in chapter-a-foundation chapter-b-spectrum chapter-c-loops; do
  echo "  -- $ch"
  (cd "$ch" && databricks bundle destroy -t dev -p "$PROFILE" --auto-approve 2>&1 | tail -2) || true
done

echo "== 1b. wait for apps to finish deleting (async — else the next deploy hits 'app already exists') =="
# App names are the hyphen-form handle: prefix klevis_aliaj_groupa -> mcp-/review-klevis-aliaj-groupa.
HANDLE="$(echo "$PREFIX" | tr '_' '-')"
for app in "mcp-$HANDLE" "review-$HANDLE"; do
  for i in $(seq 1 30); do
    present=$(databricks apps list -o json -p "$PROFILE" 2>/dev/null \
      | python3 -c "import sys,json;d=json.load(sys.stdin);d=d if isinstance(d,list) else d.get('apps',[]);print(any(a.get('name')=='$app' for a in d))")
    [ "$present" = "False" ] && { echo "  $app: gone"; break; }
    [ "$i" = "30" ] && echo "  $app: still present after wait (may be mid-START; delete manually)"
    sleep 10
  done
done

echo "== 2. delete agent serving endpoint(s) (name contains '${PREFIX}_ti_tools') =="
# agents.deploy() creates the endpoint imperatively (not in the DAB), so bundle destroy leaves it.
databricks serving-endpoints list -o json -p "$PROFILE" > /tmp/aiapps_eps.json 2>/dev/null || echo '[]' > /tmp/aiapps_eps.json
python3 - "$PREFIX" "$PROFILE" /tmp/aiapps_eps.json <<'PY'
import json, subprocess, sys
prefix, profile, path = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(path))
eps = data if isinstance(data, list) else data.get("endpoints", [])
hits = [e["name"] for e in eps if f"{prefix}_ti_tools" in (e.get("name") or "")]
if not hits:
    print("  (none found)")
for n in hits:
    subprocess.run(["databricks", "serving-endpoints", "delete", n, "-p", profile], capture_output=True)
    print(f"  deleted {n}")
PY

echo "== 3. drop schemas (CASCADE) =="
for schema in ti_intel ti_risk ti_cs ti_tools; do
  body="{\"warehouse_id\":\"$WAREHOUSE\",\"wait_timeout\":\"50s\",\"statement\":\"DROP SCHEMA IF EXISTS $CATALOG.${PREFIX}_${schema} CASCADE\"}"
  state=$(databricks api post /api/2.0/sql/statements -p "$PROFILE" --json "$body" 2>/dev/null \
            | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',{}).get('state','ERR'))")
  echo "  ${PREFIX}_${schema}: $state"
done

echo "== 4. delete Genie spaces (title prefix '$PREFIX · Threat Intel') =="
# NOTE: write the API JSON to a file, then read it — do NOT do `... | python3 - <<'PY'`, because the
# heredoc and the pipe both claim stdin (the heredoc wins) so json.load(stdin) sees the script, not the
# JSON. (That was the silent JSONDecodeError that left spaces undeleted.)
databricks api get /api/2.0/genie/spaces -p "$PROFILE" > /tmp/aiapps_genie.json 2>/dev/null || echo '{}' > /tmp/aiapps_genie.json
python3 - "$PREFIX" "$PROFILE" /tmp/aiapps_genie.json <<'PY'
import json, subprocess, sys
prefix, profile, path = sys.argv[1], sys.argv[2], sys.argv[3]
spaces = json.load(open(path)).get("spaces", [])
hits = [s for s in spaces if (s.get("title") or "").startswith(f"{prefix} · Threat Intel")]
if not hits:
    print("  (none found)")
for s in hits:
    subprocess.run(["databricks", "api", "delete", f"/api/2.0/genie/spaces/{s['space_id']}",
                    "-p", profile], capture_output=True)
    print(f"  deleted {s['space_id']}  {s['title']}")
PY

echo "== 5. delete MLflow experiments (Chapter B agent + Chapter C triage) =="
# The Chapter B agent deploy and Chapter C's triage_runner each set an experiment at the deployer's
# home path (not bundle-tracked). An experiment at a workspace path is a workspace object, so
# `workspace delete` removes it permanently (the MLflow API's delete only soft-deletes, which would
# block re-creating it by name next run).
EMAIL=$(databricks current-user me -p "$PROFILE" 2>/dev/null \
          | python3 -c "import sys,json;print(json.load(sys.stdin).get('userName',''))")
if [ -n "$EMAIL" ]; then
  for EXP in "/Users/$EMAIL/aiapps-chapter-b-triage" "/Users/$EMAIL/aiapps-chapter-c-triage"; do
    if databricks workspace delete "$EXP" -p "$PROFILE" 2>/dev/null; then
      echo "  deleted $EXP"
    else
      echo "  (no experiment to delete: $EXP)"
    fi
  done
fi

echo "teardown complete"
