#!/usr/bin/env bash
# scripts/smoke_handoff.sh
#
# The lean handoff smoke suite. Run before a release, on the VPS, against
# the dev/prod database. Each smoke seeds and tears down its own fixtures.
#
# Runs SERIALLY (per tests/__init__.py — several smokes mutate the
# singleton numbering_state row and collide under parallel execution).
#
# Exits non-zero on first failure unless --keep-going is passed.
#
# Usage:
#   scripts/smoke_handoff.sh                 # stop on first failure
#   scripts/smoke_handoff.sh --keep-going    # run all, report at end
#
# Excluded by design (run individually when relevant):
#   - heavy schema probes:        test_invoice_schema_smoke.py
#   - concurrency one-offs:       test_invoices_concurrent.py
#   - admin scripts:              test_catalog_cleanup_report_smoke.py,
#                                 test_catalog_seed_import_smoke.py,
#                                 test_ariana_vara_scraper_smoke.py
#   - migration validations:      test_invoice_quote_discount_phase2a_smoke.py,
#                                 test_webhook_ingest_smoke.py,
#                                 test_catalog_lines_smoke.py,
#                                 test_catalog_hardening_smoke.py,
#                                 test_catalog_email_render_smoke.py,
#                                 test_catalog_samples_smoke.py
#   - secondary rate limits:      test_booking_rate_limit_smoke.py,
#                                 test_portal_rate_limit_smoke.py,
#                                 test_rate_limit_smoke.py
#   - sales add-participant:      test_sales_participants_smoke.py
#                                 (admin path covered by test_event_participants_smoke.py)
#   - frontend js math:           test_line_discount_slider_math.mjs
#
# See docs/SMOKE_TEST_AUDIT.md for rationale.

set -u
set -o pipefail

KEEP_GOING=0
for arg in "$@"; do
  case "$arg" in
    --keep-going) KEEP_GOING=1 ;;
    -h|--help)
      sed -n '2,/^$/p' "$0"
      exit 0
      ;;
    *)
      echo "unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-$REPO_ROOT/venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  echo "python not found at $PYTHON" >&2
  echo "set PYTHON env var or create venv at venv/" >&2
  exit 2
fi

# Surface DATABASE_URL to the post-suite sweep below. The smokes themselves
# load .env via python-dotenv; the bash sweep needs it in the shell env to
# invoke psql. Skip surgically (grep one line; do not source the whole file)
# because .env has lines with unquoted values that break `set -a; . .env`.
if [[ -z "${DATABASE_URL:-}" && -f "$REPO_ROOT/.env" ]]; then
  DATABASE_URL=$(grep -E "^DATABASE_URL=" "$REPO_ROOT/.env" | head -1 | cut -d= -f2- | sed -e 's/^"//; s/"$//; s/^'\''//; s/'\''$//')
  export DATABASE_URL
fi

# --- Suite definition (grouped by domain; order is informational only) ---
SUITE=(
  # auth / security / session
  tests/test_auth_smoke.py
  tests/test_logout_smoke.py
  tests/test_password_hash_smoke.py
  tests/test_password_reset_smoke.py
  tests/test_d3_cookie_auth_smoke.py
  tests/test_jwt_smoke.py
  tests/test_security_headers_smoke.py
  tests/test_booking_token_ttl_revocation_smoke.py

  # booking / customer
  tests/test_booking_smoke.py
  tests/test_booking_marketing_consent_smoke.py
  tests/test_boutique_experience_smoke.py
  tests/test_confirmation_code_entropy_smoke.py

  # admin appointments
  tests/test_admin_booking_smoke.py
  tests/test_admin_booking_settings_smoke.py
  tests/test_admin_staff_compensation_smoke.py

  # contacts / events
  tests/test_contacts_smoke.py
  tests/test_events_smoke.py
  tests/test_event_documents_smoke.py
  tests/test_event_participants_smoke.py

  # catalog
  tests/test_catalog_smoke.py
  tests/test_catalog_router_smoke.py
  tests/test_catalog_search_smoke.py
  tests/test_catalog_render_swap_smoke.py
  tests/test_special_orders_smoke.py

  # quotes / invoices / payments
  tests/test_invoices_smoke.py
  tests/test_quotes_smoke.py
  tests/test_payments_smoke.py
  tests/test_invoice_pdf_smoke.py
  tests/test_pdf_totals_smoke.py
  tests/test_stacked_discounts_smoke.py
  tests/test_quote_installments_smoke.py
  tests/test_invoice_payment_plans_smoke.py
  tests/test_quote_signature_hmac_smoke.py
  tests/test_payment_refund_auth_smoke.py

  # sales portal
  tests/test_sales_auth_smoke.py
  tests/test_sales_appointments_smoke.py
  tests/test_sales_appointments_actions_smoke.py
  tests/test_sales_tried_on_smoke.py
  tests/test_sales_quote_sign_convert_smoke.py
  tests/test_sales_team_schedule_smoke.py
  tests/test_portal_smoke.py
  # sales rep dashboard (Phase 1-6 of SALES_REP_DASHBOARD_PHASES.md)
  tests/test_sales_kiosk_lock_smoke.py
  tests/test_sales_search_rbac_smoke.py
  tests/test_sales_search_results_smoke.py
  tests/test_walk_in_assignment_smoke.py
  tests/test_sales_walk_in_smoke.py
  tests/test_sales_assignment_smoke.py
  tests/test_sales_lead_reassignment_cascade_smoke.py
  tests/test_sales_lead_cascade_preview_smoke.py
  tests/test_admin_lead_reassignment_smoke.py
  tests/test_admin_appointment_notes_audit_smoke.py
  tests/test_quote_approved_in_store_notification_smoke.py
  tests/test_admin_digest_in_store_approvals_smoke.py
  tests/test_event_participant_fk_smoke.py
  tests/test_appointment_participant_tag_smoke.py
  tests/test_quote_invoice_participant_tag_smoke.py
  tests/test_board_named_buyer_count_smoke.py
  tests/test_staff_booking_assigned_wiring_smoke.py
  tests/test_staff_booking_cancelled_wiring_smoke.py
  tests/test_staff_booking_rescheduled_wiring_smoke.py

  # attendance / clock-in
  tests/test_clock_in_smoke.py
  tests/test_clock_selfie_and_gate_smoke.py
  tests/test_attendance_review_smoke.py
  tests/test_schedule_attendance_stamping_smoke.py
  tests/test_attendance_reporting_smoke.py
  tests/test_staff_locations_smoke.py
  tests/test_attendance_owner_settings_smoke.py

  # schedule / time-off / auto-scheduler
  tests/test_schedule_smoke.py
  tests/test_schedule_presets_smoke.py
  tests/test_schedule_stability_smoke.py
  tests/test_schedule_resolver_smoke.py
  tests/test_time_off_endpoints_smoke.py
  tests/test_auto_scheduler_smoke.py

  # crons / retention
  tests/test_attendance_crons_smoke.py
  tests/test_reminder_runner_smoke.py
  tests/test_attendance_geo_retention_smoke.py

  # rate limiting
  tests/test_redis_rate_limit_smoke.py
  tests/test_auth_rate_limit_smoke.py

  # audit / activity logging
  tests/test_audit_append_only_smoke.py
  tests/test_activity_log_smoke.py

  # search
  tests/test_search_smoke.py

  # integrations / business profile / compliance
  tests/test_integration_tokens_smoke.py
  tests/test_upload_validation_smoke.py
  tests/test_business_profile_smoke.py
  tests/test_delete_policy_guardrail_smoke.py

  # dashboards / notifications
  tests/test_dashboard_smoke.py
  tests/test_notifications_smoke.py
  tests/test_notification_kind_naming_boundary_smoke.py
)

TOTAL=${#SUITE[@]}
PASS=0
FAIL=0
FAILED_TESTS=()

t0=$(date +%s)

STOPPED_EARLY=0
STOPPED_AT=""
STOPPED_REMAINING=0
for i in "${!SUITE[@]}"; do
  test_path="${SUITE[$i]}"
  idx=$((i + 1))
  printf '\n[%2d/%d] %s\n' "$idx" "$TOTAL" "$test_path"
  printf '%s\n' "----------------------------------------------------------------"

  if "$PYTHON" "$test_path"; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("$test_path")
    if [[ $KEEP_GOING -eq 0 ]]; then
      # Break instead of exit so the post-suite sweep at the bottom of the
      # script always runs, even on first-failure with default settings.
      STOPPED_EARLY=1
      STOPPED_AT="$test_path"
      STOPPED_REMAINING=$((TOTAL - idx))
      break
    fi
  fi
done

t1=$(date +%s)
elapsed=$((t1 - t0))

# ---------------------------------------------------------------------------
# Post-suite safety net: confirm no smoke fixtures leaked into the DB.
#
# Each smoke is supposed to clean up its own rows. When a smoke raises
# before its teardown runs (assertion failure or seed exception), real
# users/contacts/events stay behind and surface in admin UIs. The cleanup
# SQL has prefix-scoped predicates; we run it in apply mode and parse
# POST_RUN_RESIDUE_TOTAL from its output. Any non-zero means at least one
# smoke leaked.
#
# This step ALWAYS runs even if the suite failed above, so a leak is
# remediated in the same run that produced it.
# ---------------------------------------------------------------------------

SWEEP_SQL="$REPO_ROOT/scripts/cleanup_admin_smoke_pollution.sql"
SWEEP_FAILED=0

if [[ -z "${DATABASE_URL:-}" ]]; then
  printf '\n%s\n' "post-smoke sweep: SKIPPED (DATABASE_URL not set)"
elif ! command -v psql >/dev/null 2>&1; then
  printf '\n%s\n' "post-smoke sweep: SKIPPED (psql not on PATH)"
elif [[ ! -f "$SWEEP_SQL" ]]; then
  printf '\n%s\n' "post-smoke sweep: SKIPPED ($SWEEP_SQL missing)"
else
  printf '\n%s\n' "================================================================"
  printf 'post-smoke sweep: scanning for leaked smoke rows...\n'
  printf '%s\n' "----------------------------------------------------------------"

  # The SQL script defaults to rollback; pass apply=true so any residue
  # is also cleaned in the same run. POST_RUN_RESIDUE_TOTAL is emitted
  # before commit/rollback so it captures what was found, not what is left.
  sweep_out=$(psql "$DATABASE_URL" -v apply=true -f "$SWEEP_SQL" 2>&1) || {
    printf '%s\n' "$sweep_out"
    printf 'post-smoke sweep: psql FAILED — treating as a suite failure\n'
    SWEEP_FAILED=1
  }
  if [[ $SWEEP_FAILED -eq 0 ]]; then
    printf '%s\n' "$sweep_out"
    residue=$(printf '%s\n' "$sweep_out" | grep -oE 'POST_RUN_RESIDUE_TOTAL=[0-9]+' | tail -1 | cut -d= -f2)
    if [[ -z "$residue" ]]; then
      printf '\npost-smoke sweep: WARN — POST_RUN_RESIDUE_TOTAL not found in output\n'
      SWEEP_FAILED=1
    elif [[ "$residue" -gt 0 ]]; then
      printf '\npost-smoke sweep: LEAK DETECTED — %d smoke rows were cleaned\n' "$residue"
      printf 'this means at least one smoke aborted before its teardown ran.\n'
      SWEEP_FAILED=1
    else
      printf '\npost-smoke sweep: clean (no residue)\n'
    fi
  fi
fi

printf '\n%s\n' "================================================================"
printf 'Handoff smoke suite: %d/%d passed   elapsed: %ds\n' "$PASS" "$TOTAL" "$elapsed"
if [[ $STOPPED_EARLY -eq 1 ]]; then
  printf 'STOPPED at %s\n' "$STOPPED_AT"
  printf 'Pass: %d   Fail: %d   Remaining: %d\n' "$PASS" "$FAIL" "$STOPPED_REMAINING"
  printf 'Re-run with --keep-going to continue past failures.\n'
fi
if [[ $FAIL -gt 0 ]]; then
  printf '\nFailed:\n'
  for t in "${FAILED_TESTS[@]}"; do
    printf '  - %s\n' "$t"
  done
fi
if [[ $FAIL -gt 0 || $SWEEP_FAILED -ne 0 ]]; then
  exit 1
fi
exit 0
