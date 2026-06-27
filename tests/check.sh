#!/usr/bin/env bash
# Layer 0 + 1 gate: byte-compile every notebook/module, validate all three bundles, run the unit suite.
# No workspace mutation (bundle validate is read-only). Usage: tests/check.sh
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR/.."
[ -f "$DIR/config.env" ] && { set -a; . "$DIR/config.env"; set +a; }   # local values (gitignored)
PROFILE="${DATABRICKS_PROFILE:-${WORKSHOP_PROFILE:-}}"
: "${PROFILE:?set WORKSHOP_PROFILE (tests/config.env) or DATABRICKS_PROFILE}"
fail=0

echo "== py_compile =="
python3 -m py_compile \
  chapter-a-foundation/src/*.py chapter-a-foundation/explore.py \
  chapter-b-spectrum/common.py chapter-b-spectrum/explore.py \
  chapter-b-spectrum/hosted-mcp/*.py chapter-b-spectrum/local-mcp/server.py \
  chapter-b-spectrum/skill/scripts/threatintel.py chapter-b-spectrum/triage-agent/*.py \
  chapter-c-loops/src/*.py chapter-c-loops/explore.py \
  && echo "  OK" || { echo "  FAIL"; fail=1; }

echo "== bundle validate (-p $PROFILE) =="
for ch in chapter-a-foundation chapter-b-spectrum chapter-c-loops; do
  if (cd "$ch" && databricks bundle validate -t dev -p "$PROFILE" >/dev/null 2>&1); then
    echo "  OK   $ch"
  else
    echo "  FAIL $ch"; fail=1
  fi
done

echo "== unit tests =="
python3 tests/run.py || fail=1

find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
echo
[ "$fail" -eq 0 ] && echo "ALL CHECKS PASSED" || echo "CHECKS FAILED"
exit $fail
