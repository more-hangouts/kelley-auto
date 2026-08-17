#!/usr/bin/env bash
# Post-restart verification for the 2026-08-16 follow-up release.
#
# Run AFTER:
#   sudo systemctl restart kelley-backend
#   sudo systemctl restart kelley-public
#
# Checks the four things that matter, in order, and is deliberately
# NON-INVASIVE: it never submits a lead. A real submission would create a live
# deal and fire a "New vehicle lead" alert at sales@, so instead this proves
# the new code is the code that is running (the deployed OpenAPI schema no
# longer accepts the slot fields, and no appointment has been created by the
# lead path since the restart). If you want a true end-to-end submission,
# do it by hand from the site and delete the deal afterwards.
#
#   bash scripts/verify_followup_release.sh

set -uo pipefail
cd "$(dirname "$0")/.."

API="https://api.kelleyautoplex.com"
SITE="https://www.kelleyautoplex.com"
PY=".venv/bin/python"

pass=0
fail=0
ok()   { echo "  PASS  $1"; pass=$((pass + 1)); }
bad()  { echo "  FAIL  $1"; fail=$((fail + 1)); }

echo
echo "1. Public form no longer shows the picker"
page=$(curl -fsS "$SITE/contact-us" 2>/dev/null)
if [ -z "$page" ]; then
  bad "could not fetch $SITE/contact-us"
else
  echo "$page" | grep -q "Preferred appointment time" \
    && bad "contact page STILL renders the slot picker" \
    || ok "no slot picker on the contact page"
  echo "$page" | grep -q "Request Appointment" \
    && bad "contact page STILL says 'Request Appointment'" \
    || ok "CTA is no longer 'Request Appointment'"
  echo "$page" | grep -q "(830) 268-9308" \
    && ok "scheduling number is present" \
    || bad "scheduling number (830) 268-9308 missing — is kelley-public restarted?"
fi

echo
echo "2. Backend no longer turns a lead into an appointment"
schema=$(curl -fsS "$API/openapi.json" 2>/dev/null)
if [ -z "$schema" ]; then
  bad "could not fetch $API/openapi.json"
else
  # The running server's own contract is the proof the new code is loaded.
  echo "$schema" | grep -q '"preferred_hour"' \
    && bad "PublicLeadRequest STILL accepts preferred_hour — backend not restarted" \
    || ok "preferred_date/preferred_hour are gone from the live schema"
fi
$PY - <<'PYEOF'
import os, sys
sys.path.insert(0, os.getcwd())
from sqlalchemy import text
from database.connection import SessionLocal

db = SessionLocal()
try:
    # Any lead-path appointment CREATED since the cleanup ran means the old
    # code is still creating them. Anchored on max(cancelled_at) — the moment
    # the cleanup executed — NOT on the created_at of the cancelled rows: the
    # two surviving future appointments predate the cleanup, and comparing
    # against row creation times counts them as new every time.
    n = db.execute(text("""
        SELECT count(*) FROM appointments
         WHERE raw_payload->>'source' = 'public_lead'
           AND created_at > (
               SELECT COALESCE(max(cancelled_at), now() - interval '1 hour')
                 FROM appointments
                WHERE raw_payload->>'source' = 'public_lead'
                  AND status = 'cancelled'
           )
    """)).scalar()
    print(("  PASS  " if n == 0 else "  FAIL  ")
          + f"lead-created appointments since cleanup: {n} (want 0)")
finally:
    db.close()
PYEOF

echo
echo "3. Follow-ups endpoint is live"
if [ -n "$schema" ] && echo "$schema" | grep -q '"/api/events/follow-ups"'; then
  ok "/api/events/follow-ups is registered on the running server"
else
  bad "/api/events/follow-ups missing — the /sales toggle will show an alert"
fi

echo
echo "4. The two future appointments are still there for a human"
$PY - <<'PYEOF'
import os, sys
sys.path.insert(0, os.getcwd())
from sqlalchemy import text
from database.connection import SessionLocal

db = SessionLocal()
try:
    rows = db.execute(text("""
        SELECT id, status, slot_start_at, celebrant_first_name,
               celebrant_last_name, phone
          FROM appointments
         WHERE source = 'public_booking'
           AND raw_payload->>'source' = 'public_lead'
           AND status = 'pending'
         ORDER BY slot_start_at
    """)).all()
    for r in rows:
        name = " ".join(x for x in (r[3], r[4]) if x)
        print(f"    #{r[0]}  {r[2]:%Y-%m-%d %H:%M}  {name}  {r[5] or '—'}")
    print(("  PASS  " if len(rows) == 2 else "  FAIL  ")
          + f"{len(rows)} future pending lead appointment(s) preserved (want 2)")
finally:
    db.close()
PYEOF

echo
echo "----------------------------------------"
echo "verify: $pass checks printed above passed via shell; review any FAIL lines."
echo "(the two python blocks print their own PASS/FAIL)"
[ "$fail" -eq 0 ]
