# Smoke Test Audit — Client Handoff

**Date:** 2026-05-17
**Goal:** Reduce the accumulated smoke suite to a lean, domain-organized handoff suite that protects critical production behavior without carrying every historical phase-slice test forever.

## Summary

| Disposition | Count |
|-------------|-------|
| **KEEP** (in handoff suite, no rename) | 55 |
| **RENAME** (drop phase marker → stable name) | 13 |
| **MERGE** (fold into another smoke) | 0 |
| **ARCHIVE** (file stays, excluded from handoff suite) | 16 |
| **DELETE** (truly obsolete) | 2 |
| **Total smoke files today** | 86 (85 Python + 1 mjs) |

The handoff suite as defined runs **68 Python smokes**, serially. Estimated wall time on the VPS: ~3-5 min (each smoke is currently 5-30s).

## Methodology

- "Smoke" = `tests/test_*_smoke.py` and `tests/test_*.py` that mint fixtures, exercise real endpoints/services against the dev DB, and clean up.
- Classification rules:
  - **KEEP**: covers a live production surface; would catch a real regression on next-developer changes.
  - **RENAME**: same as KEEP, but the filename references a shipped historical phase (`phase5/6/8/9/10`) and should adopt a stable domain name.
  - **MERGE**: redundant with another smoke; coverage folded into that file.
  - **ARCHIVE**: file is kept for historical/audit value, but excluded from the handoff suite — either a one-shot migration validation, a heavy schema probe, or a phase-shipping e2e whose component pieces already have direct coverage.
  - **DELETE**: redundant *and* without unique value worth preserving.
- Conservative default: when in doubt, **ARCHIVE** rather than **DELETE**.

## Inventory by domain

### 1. auth / security / session

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_auth_smoke.py](../tests/test_auth_smoke.py) | 63 | **KEEP** | — | Tiny token round-trip + scope claim. Foundational. |
| [test_logout_smoke.py](../tests/test_logout_smoke.py) | 303 | **KEEP** | — | D2 server-side logout. Token revocation is core auth surface. |
| [test_password_hash_smoke.py](../tests/test_password_hash_smoke.py) | 277 | **KEEP** | — | bcrypt round-trip + legacy verify. Login is critical. |
| [test_password_reset_smoke.py](../tests/test_password_reset_smoke.py) | 449 | **KEEP** | — | D4 reset flow + SHA-256 token storage. Active surface. |
| [test_d3_cookie_auth_smoke.py](../tests/test_d3_cookie_auth_smoke.py) | 358 | **KEEP** | — | D3 HttpOnly + CSRF gates. All admin auth depends on it. |
| [test_jwt_migration_smoke.py](../tests/test_jwt_migration_smoke.py) | 416 | **RENAME** | `test_jwt_smoke.py` | Still exercises JWT validation modes. Drop "migration" — the swap shipped. |
| [test_security_headers_smoke.py](../tests/test_security_headers_smoke.py) | 138 | **KEEP** | — | E3 headers middleware. Cheap regression coverage. |
| [test_booking_token_ttl_revocation_smoke.py](../tests/test_booking_token_ttl_revocation_smoke.py) | 379 | **KEEP** | — | G1 booking portal TTL + post-cancel/reschedule revocation. Customer-facing. |

### 2. booking / customer

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_booking_smoke.py](../tests/test_booking_smoke.py) | 390 | **KEEP** | — | Public booking widget end-to-end. The flagship customer flow. |
| [test_booking_marketing_consent_smoke.py](../tests/test_booking_marketing_consent_smoke.py) | 246 | **KEEP** | — | Recent feature, ongoing marketing-opt-in coverage. |
| [test_boutique_experience_smoke.py](../tests/test_boutique_experience_smoke.py) | 589 | **KEEP** | — | Boutique-experience profile + token validation. Customer-facing. |
| [test_confirmation_code_entropy_smoke.py](../tests/test_confirmation_code_entropy_smoke.py) | 320 | **KEEP** | — | D1 BX+20-char codes + lookup + rate limit layering. Critical booking surface. |

### 3. admin appointments / calendar / staff

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_admin_booking_smoke.py](../tests/test_admin_booking_smoke.py) | 277 | **KEEP** | — | Admin appointment CRUD + audit. Daily-driver surface. |
| [test_admin_booking_settings_smoke.py](../tests/test_admin_booking_settings_smoke.py) | 252 | **KEEP** | — | Widget config (theme, availability, blackouts). Owner-managed. |
| [test_admin_staff_compensation_smoke.py](../tests/test_admin_staff_compensation_smoke.py) | 392 | **KEEP** | — | Staff Profiles compensation fields + no-leak security contract. Owner-managed payroll-adjacent surface. |

### 4. contacts / events

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_contacts_smoke.py](../tests/test_contacts_smoke.py) | 404 | **KEEP** | — | CRM contacts PATCH + normalization + collision. Core. |
| [test_events_smoke.py](../tests/test_events_smoke.py) | 452 | **KEEP** | — | CRM events promote/board/status. Core. |
| [test_event_documents_smoke.py](../tests/test_event_documents_smoke.py) | 590 | **KEEP** | — | Document upload/download lifecycle. Owner-managed. |
| [test_event_participants_smoke.py](../tests/test_event_participants_smoke.py) | 470 | **KEEP** | — | Add-participant + find_or_create_contact. Ongoing. |

### 5. catalog

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_catalog_smoke.py](../tests/test_catalog_smoke.py) | 369 | **KEEP** | — | SKU obfuscation + public code allocation. Foundational. |
| [test_catalog_router_smoke.py](../tests/test_catalog_router_smoke.py) | 195 | **KEEP** | — | Staff catalog API + role gates. Daily-driver. |
| [test_catalog_search_smoke.py](../tests/test_catalog_search_smoke.py) | 344 | **KEEP** | — | Ranked search (exact/prefix/substring) + normalization. |
| [test_special_orders_smoke.py](../tests/test_special_orders_smoke.py) | 847 | **KEEP** | — | Special-orders lifecycle. Owner-managed. |
| [test_catalog_samples_smoke.py](../tests/test_catalog_samples_smoke.py) | 503 | **ARCHIVE** | — | `is_sample` flag is covered indirectly by `test_catalog_router_smoke.py`; this is an exhaustive Phase 6 validation. Keep for reference. |
| [test_catalog_email_render_smoke.py](../tests/test_catalog_email_render_smoke.py) | 193 | **ARCHIVE** | — | Phase 4 email leak audit (forbidden-field absence). The forbidden-field check is duplicated in `test_catalog_render_swap_smoke.py` and `test_catalog_hardening_smoke.py`. |
| [test_catalog_lines_smoke.py](../tests/test_catalog_lines_smoke.py) | 741 | **ARCHIVE** | — | Phase 2 catalog-backed line items. Migrating-state behavior; current behavior covered by `test_invoices_smoke.py` + `test_quotes_smoke.py`. |
| [test_catalog_render_swap_smoke.py](../tests/test_catalog_render_swap_smoke.py) | 897 | **KEEP** | — | Customer-render contract: BVX code + safe desc, no leaks. Renders on PDF + portal. Load-bearing. |
| [test_catalog_hardening_smoke.py](../tests/test_catalog_hardening_smoke.py) | 414 | **ARCHIVE** | — | Hardening probes overlap with `test_catalog_render_swap_smoke.py`. |
| [test_catalog_cleanup_report_smoke.py](../tests/test_catalog_cleanup_report_smoke.py) | 399 | **ARCHIVE** | — | Tests an admin script (`scripts/catalog_cleanup_report.py`), not a runtime surface. |
| [test_catalog_seed_import_smoke.py](../tests/test_catalog_seed_import_smoke.py) | 447 | **ARCHIVE** | — | Tests the seed import script. Run on demand, not per release. |
| [test_ariana_vara_scraper_smoke.py](../tests/test_ariana_vara_scraper_smoke.py) | 230 | **ARCHIVE** | — | Vendor scraper (single supplier). Useful when extending scrapers; not handoff-critical. |

### 6. quotes / invoices / payments

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_invoices_smoke.py](../tests/test_invoices_smoke.py) | 1118 | **KEEP** | — | Full invoice service lifecycle. Money-mover. |
| [test_quotes_smoke.py](../tests/test_quotes_smoke.py) | 969 | **KEEP** | — | Full quote service lifecycle. Money-mover. |
| [test_payments_smoke.py](../tests/test_payments_smoke.py) | 771 | **KEEP** | — | Payment service (deposit/balance/refund/void). Money-mover. |
| [test_invoice_pdf_smoke.py](../tests/test_invoice_pdf_smoke.py) | 735 | **KEEP** | — | WeasyPrint render + cache invalidation. Customer-facing. |
| [test_pdf_totals_smoke.py](../tests/test_pdf_totals_smoke.py) | 460 | **KEEP** | — | PDF discount breakdown rows. Customer-facing. |
| [test_stacked_discounts_smoke.py](../tests/test_stacked_discounts_smoke.py) | 514 | **KEEP** | — | Stacking + 50% cap + quote→invoice copy. Ongoing. |
| [test_quote_installments_phase4_smoke.py](../tests/test_quote_installments_phase4_smoke.py) | 493 | **RENAME** | `test_quote_installments_smoke.py` | Drop phase marker. Active surface. |
| [test_plan_selector_phase5_smoke.py](../tests/test_plan_selector_phase5_smoke.py) | 512 | **RENAME** | `test_invoice_payment_plans_smoke.py` | Covers deposit_below_floor + plan_count_invalid + custom_amounts flag — rules not exercised elsewhere. |
| [test_invoice_quote_discount_phase2a_smoke.py](../tests/test_invoice_quote_discount_phase2a_smoke.py) | 585 | **ARCHIVE** | — | Phase 2a migration validation. Snapshot stability + pre-tax math; partially overlaps `test_stacked_discounts_smoke.py`. |
| [test_invoice_schema_smoke.py](../tests/test_invoice_schema_smoke.py) | 1872 | **ARCHIVE** | — | Heavy schema probe (CHECK/UNIQUE/numbering_state row-lock). Schema doesn't regress; rerun when migrations change. |
| [test_invoices_concurrent.py](../tests/test_invoices_concurrent.py) | 459 | **ARCHIVE** | — | Concurrent `mark_sent` numbering test. Slow + thread-heavy; one-shot validation. |
| [test_phase6_e2e_smoke.py](../tests/test_phase6_e2e_smoke.py) | 506 | **DELETE** | — | Phase 6 e2e (build full quote → convert → verify). Each surface (discount, installments, PDF totals, conversion) is covered by its own smoke. |
| [test_quote_signature_hmac_smoke.py](../tests/test_quote_signature_hmac_smoke.py) | 349 | **KEEP** | — | C3 signature integrity + immutability trigger. Legal-grade evidence. |
| [test_payment_refund_auth_smoke.py](../tests/test_payment_refund_auth_smoke.py) | 113 | **KEEP** | — | A2 refund scope gate. Money-mover authorization. |
| [test_line_discount_slider_math.mjs](../tests/test_line_discount_slider_math.mjs) | 121 | **ARCHIVE** | — | Pure frontend math; runs via `node`, not in CI's Python pipeline. Keep for the React component. |

### 7. sales portal / PIN

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_sales_auth_smoke.py](../tests/test_sales_auth_smoke.py) | 359 | **KEEP** | — | PIN mint + login + lockout. Sales-floor entry point. |
| [test_sales_appointments_smoke.py](../tests/test_sales_appointments_smoke.py) | 417 | **KEEP** | — | Sales reads (today/mine/detail). Daily-driver. |
| [test_sales_appointments_actions_smoke.py](../tests/test_sales_appointments_actions_smoke.py) | 535 | **KEEP** | — | Composite status handler + activity log. Daily-driver. |
| [test_sales_tried_on_smoke.py](../tests/test_sales_tried_on_smoke.py) | 536 | **KEEP** | — | Try-on log + activity field redaction. Daily-driver. |
| [test_sales_quote_sign_convert_smoke.py](../tests/test_sales_quote_sign_convert_smoke.py) | 480 | **KEEP** | — | In-store sign + convert. Sales close-the-sale flow. |
| [test_sales_participants_smoke.py](../tests/test_sales_participants_smoke.py) | 191 | **ARCHIVE** | — | Phone find_or_create. Covered by `test_event_participants_smoke.py` (admin equivalent of same service). |
| [test_portal_smoke.py](../tests/test_portal_smoke.py) | 779 | **KEEP** | — | Customer portal (invitations, view-count, sig submit). Customer-facing. |

### 8. attendance / clock-in

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_clock_in_smoke.py](../tests/test_clock_in_smoke.py) | 434 | **KEEP** | — | Geofence + punch state machine. Daily-driver. |
| [test_clock_selfie_and_gate_smoke.py](../tests/test_clock_selfie_and_gate_smoke.py) | 686 | **KEEP** | — | Selfie validation + attendance gate. Daily-driver. |
| [test_attendance_review_smoke.py](../tests/test_attendance_review_smoke.py) | 771 | **KEEP** | — | Owner review (confirm/adjust/void). Owner-managed. |
| [test_phase10_attendance_smoke.py](../tests/test_phase10_attendance_smoke.py) | 582 | **RENAME** | `test_schedule_attendance_stamping_smoke.py` | Schedule-entry stamping is distinct from clock-in. Drop phase marker. |
| [test_phase9_attendance_reporting_smoke.py](../tests/test_phase9_attendance_reporting_smoke.py) | 417 | **RENAME** | `test_attendance_reporting_smoke.py` | Range presets + CSV export. Active surface. |
| [test_phase9_staff_locations_smoke.py](../tests/test_phase9_staff_locations_smoke.py) | 278 | **RENAME** | `test_staff_locations_smoke.py` | Geofence config + probe. Active surface. |
| [test_phase9_owner_settings_smoke.py](../tests/test_phase9_owner_settings_smoke.py) | 280 | **RENAME** | `test_attendance_owner_settings_smoke.py` | Owner attendance/selfie settings + EXIF strip. Active surface. |

### 9. schedule / time-off / auto-scheduler

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_phase10_schedule_smoke.py](../tests/test_phase10_schedule_smoke.py) | 886 | **RENAME** | `test_schedule_smoke.py` | Per-day schedule + resolver precedence. The canonical schedule smoke. |
| [test_phase10_presets_smoke.py](../tests/test_phase10_presets_smoke.py) | 489 | **RENAME** | `test_schedule_presets_smoke.py` | Shift presets. Owner-managed. |
| [test_phase10_team_schedule_smoke.py](../tests/test_phase10_team_schedule_smoke.py) | 417 | **RENAME** | `test_sales_team_schedule_smoke.py` | Sales-scoped team schedule view. Sales-floor surface. |
| [test_phase10_stability_smoke.py](../tests/test_phase10_stability_smoke.py) | 894 | **RENAME** | `test_schedule_stability_smoke.py` | Missing-out-punch cron, publish vs time-off race. |
| [test_auto_scheduler_smoke.py](../tests/test_auto_scheduler_smoke.py) | 629 | **KEEP** | — | Auto-scheduler draft generation. Recently added; already well-named. |
| [test_phase8_resolver_smoke.py](../tests/test_phase8_resolver_smoke.py) | 963 | **RENAME** | `test_schedule_resolver_smoke.py` | Shift-resolver precedence (override > assigned > location default) + time-off suppression. |
| [test_phase8_endpoints_smoke.py](../tests/test_phase8_endpoints_smoke.py) | 703 | **RENAME** | `test_time_off_endpoints_smoke.py` | Time-off cancel + terminal-state 409s. |
| [test_phase8_schema_smoke.py](../tests/test_phase8_schema_smoke.py) | 588 | **DELETE** | — | One-shot ORM schema validation for `staff_shifts` + `time_off_requests`. Tables are exercised by the resolver + endpoints + schedule smokes. Schema doesn't regress without a migration. |

### 10. crons / retention / reminders

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_attendance_crons_smoke.py](../tests/test_attendance_crons_smoke.py) | 878 | **KEEP** | — | Four-cron family (selfie retention, auto-close, pre-close reminders, health). Background-worker contract. |
| [test_reminder_runner_smoke.py](../tests/test_reminder_runner_smoke.py) | 828 | **KEEP** | — | Reminder + quote-expiry cron. Customer-facing email cadence. |
| [test_attendance_geo_retention_smoke.py](../tests/test_attendance_geo_retention_smoke.py) | 361 | **KEEP** | — | G2 PII scrub + audit stamping. Compliance-grade. |
| [test_webhook_ingest_smoke.py](../tests/test_webhook_ingest_smoke.py) | 301 | **ARCHIVE** | — | C2 webhook redaction + retention sweep. Stable; one-shot validation. |

### 11. rate limiting

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_redis_rate_limit_smoke.py](../tests/test_redis_rate_limit_smoke.py) | 148 | **KEEP** | — | B1 base limiter. Foundation for the others. |
| [test_auth_rate_limit_smoke.py](../tests/test_auth_rate_limit_smoke.py) | 284 | **KEEP** | — | B2 login + PIN throttles. Brute-force protection. |
| [test_booking_rate_limit_smoke.py](../tests/test_booking_rate_limit_smoke.py) | 239 | **ARCHIVE** | — | B3 booking + code rate limits. Stable; foundation is in `test_redis_rate_limit_smoke.py`. |
| [test_portal_rate_limit_smoke.py](../tests/test_portal_rate_limit_smoke.py) | 161 | **ARCHIVE** | — | B4 portal token limits. Stable. |
| [test_rate_limit_smoke.py](../tests/test_rate_limit_smoke.py) | 178 | **ARCHIVE** | — | Pre-Redis staff-money limiter (in-process). Foundation has moved to Redis. |

### 12. audit / activity logging

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_audit_append_only_smoke.py](../tests/test_audit_append_only_smoke.py) | 293 | **KEEP** | — | C4 append-only triggers. Catches accidental UPDATE/DELETE on audit tables. |
| [test_activity_log_smoke.py](../tests/test_activity_log_smoke.py) | 693 | **KEEP** | — | Activity timeline (invoice/quote/payment/event lifecycle). Owner-managed surface. |

### 13. search

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_search_smoke.py](../tests/test_search_smoke.py) | 646 | **KEEP** | — | Global search (auth, type filter, tiered ranking, accent folding). Owner-managed. |

### 14. integrations / webhooks / business profile

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_integration_tokens_smoke.py](../tests/test_integration_tokens_smoke.py) | 265 | **KEEP** | — | C1 Fernet encryption + key rotation. Token write-path is ongoing. |
| [test_upload_validation_smoke.py](../tests/test_upload_validation_smoke.py) | 343 | **KEEP** | — | E1/E2 magic-byte + forced attachment. Active upload paths (documents + logo). |
| [test_business_profile_smoke.py](../tests/test_business_profile_smoke.py) | 601 | **KEEP** | — | Profile validators + logo CRUD. Owner-managed. |
| [test_delete_policy_guardrail_smoke.py](../tests/test_delete_policy_guardrail_smoke.py) | 322 | **KEEP** | — | G3 scans the codebase for new delete sites. Active guardrail. |

### 15. dashboard / notifications

| File | Lines | Disposition | New name | Reason |
|------|-------|-------------|----------|--------|
| [test_dashboard_smoke.py](../tests/test_dashboard_smoke.py) | 844 | **KEEP** | — | Admin dashboard rollups (outstanding, overdue, deposits, recent payments). Owner-managed. |
| [test_notifications_smoke.py](../tests/test_notifications_smoke.py) | 588 | **KEEP** | — | Booking notification enqueue + drain + cancel. Customer-facing emails. |

### Helpers (not classified)

- [tests/_attendance_helpers.py](../tests/_attendance_helpers.py) — shared `snapshot_and_disable_gate` / `restore_gate` used by sales-mutation smokes. **KEEP.**
- [tests/__init__.py](../tests/__init__.py) — explains the smoke discipline. **KEEP.**

## Renames (file moves only — no behavior change)

1. `test_jwt_migration_smoke.py` → `test_jwt_smoke.py`
2. `test_quote_installments_phase4_smoke.py` → `test_quote_installments_smoke.py`
3. `test_plan_selector_phase5_smoke.py` → `test_invoice_payment_plans_smoke.py`
4. `test_phase10_attendance_smoke.py` → `test_schedule_attendance_stamping_smoke.py`
5. `test_phase9_attendance_reporting_smoke.py` → `test_attendance_reporting_smoke.py`
6. `test_phase9_staff_locations_smoke.py` → `test_staff_locations_smoke.py`
7. `test_phase9_owner_settings_smoke.py` → `test_attendance_owner_settings_smoke.py`
8. `test_phase10_schedule_smoke.py` → `test_schedule_smoke.py`
9. `test_phase10_presets_smoke.py` → `test_schedule_presets_smoke.py`
10. `test_phase10_team_schedule_smoke.py` → `test_sales_team_schedule_smoke.py`
11. `test_phase10_stability_smoke.py` → `test_schedule_stability_smoke.py`
12. `test_phase8_resolver_smoke.py` → `test_schedule_resolver_smoke.py`
13. `test_phase8_endpoints_smoke.py` → `test_time_off_endpoints_smoke.py`

(Internal docstrings will be cleaned up to drop "Phase N" framing — the test bodies don't change.)

## Deletions

1. **`test_phase6_e2e_smoke.py`** (506 lines)
   Reason: e2e flow (build quote with all surface area → convert to invoice → verify). Each surface is covered by a dedicated smoke (`test_quotes_smoke.py`, `test_invoices_smoke.py`, `test_pdf_totals_smoke.py`, `test_stacked_discounts_smoke.py`, `test_quote_installments_smoke.py`). Replacement coverage: composite of the five.

2. **`test_phase8_schema_smoke.py`** (588 lines)
   Reason: ORM-side companion to a shipped migration. The `staff_shifts` + `time_off_requests` schema is exercised behaviorally by the resolver + endpoints + (renamed) schedule smokes. Schema CHECK/FK/NULLS-NOT-DISTINCT probes don't regress without a migration. Replacement coverage: `test_schedule_resolver_smoke.py`, `test_time_off_endpoints_smoke.py`, `test_schedule_smoke.py`.

## Handoff suite

Defined in [`scripts/smoke_handoff.sh`](../scripts/smoke_handoff.sh). Runs **serially** (per `tests/__init__.py` rule on `numbering_state`).

Suite contents (by domain):

| Domain | Smokes |
|--------|--------|
| auth | `test_auth_smoke.py`, `test_logout_smoke.py`, `test_password_hash_smoke.py`, `test_password_reset_smoke.py`, `test_d3_cookie_auth_smoke.py`, `test_jwt_smoke.py`, `test_security_headers_smoke.py`, `test_booking_token_ttl_revocation_smoke.py` |
| booking | `test_booking_smoke.py`, `test_booking_marketing_consent_smoke.py`, `test_boutique_experience_smoke.py`, `test_confirmation_code_entropy_smoke.py` |
| admin | `test_admin_booking_smoke.py`, `test_admin_booking_settings_smoke.py`, `test_admin_staff_compensation_smoke.py` |
| contacts/events | `test_contacts_smoke.py`, `test_events_smoke.py`, `test_event_documents_smoke.py`, `test_event_participants_smoke.py` |
| catalog | `test_catalog_smoke.py`, `test_catalog_router_smoke.py`, `test_catalog_search_smoke.py`, `test_catalog_render_swap_smoke.py`, `test_special_orders_smoke.py` |
| money | `test_invoices_smoke.py`, `test_quotes_smoke.py`, `test_payments_smoke.py`, `test_invoice_pdf_smoke.py`, `test_pdf_totals_smoke.py`, `test_stacked_discounts_smoke.py`, `test_quote_installments_smoke.py`, `test_invoice_payment_plans_smoke.py`, `test_quote_signature_hmac_smoke.py`, `test_payment_refund_auth_smoke.py` |
| sales | `test_sales_auth_smoke.py`, `test_sales_appointments_smoke.py`, `test_sales_appointments_actions_smoke.py`, `test_sales_tried_on_smoke.py`, `test_sales_quote_sign_convert_smoke.py`, `test_sales_team_schedule_smoke.py`, `test_portal_smoke.py` |
| attendance | `test_clock_in_smoke.py`, `test_clock_selfie_and_gate_smoke.py`, `test_attendance_review_smoke.py`, `test_schedule_attendance_stamping_smoke.py`, `test_attendance_reporting_smoke.py`, `test_staff_locations_smoke.py`, `test_attendance_owner_settings_smoke.py` |
| schedule | `test_schedule_smoke.py`, `test_schedule_presets_smoke.py`, `test_schedule_stability_smoke.py`, `test_schedule_resolver_smoke.py`, `test_time_off_endpoints_smoke.py`, `test_auto_scheduler_smoke.py` |
| crons | `test_attendance_crons_smoke.py`, `test_reminder_runner_smoke.py`, `test_attendance_geo_retention_smoke.py` |
| rate limits | `test_redis_rate_limit_smoke.py`, `test_auth_rate_limit_smoke.py` |
| audit | `test_audit_append_only_smoke.py`, `test_activity_log_smoke.py` |
| search | `test_search_smoke.py` |
| integrations | `test_integration_tokens_smoke.py`, `test_upload_validation_smoke.py`, `test_business_profile_smoke.py`, `test_delete_policy_guardrail_smoke.py` |
| dashboards | `test_dashboard_smoke.py`, `test_notifications_smoke.py` |

**Total: 68 smokes in the handoff suite.** Each domain has 2-4 ongoing-surface smokes; folding more would lose useful coverage. The size is still serial-runnable in a few minutes on the VPS.

Smokes excluded from the handoff suite (still in repo, runnable individually):

```
test_catalog_samples_smoke.py
test_catalog_email_render_smoke.py
test_catalog_lines_smoke.py
test_catalog_hardening_smoke.py
test_catalog_cleanup_report_smoke.py
test_catalog_seed_import_smoke.py
test_ariana_vara_scraper_smoke.py
test_invoice_quote_discount_phase2a_smoke.py
test_invoice_schema_smoke.py
test_invoices_concurrent.py
test_line_discount_slider_math.mjs       (frontend; run with node)
test_sales_participants_smoke.py
test_booking_rate_limit_smoke.py
test_portal_rate_limit_smoke.py
test_rate_limit_smoke.py
test_webhook_ingest_smoke.py
```

## Notes for the next developer

- The handoff suite is the day-one regression net. Each entry is a domain-level smoke; if you add a feature in that domain, extend the existing smoke before writing a new file.
- "Archived" tests are deeper validations (schema probes, migration-shipping checks, single-vendor scrapers). Run them on demand — when touching schema, when rotating Fernet keys, when adding a vendor scraper.
- Phase markers (`phase5`, `phase8`, etc.) in the codebase refer to historical project phases documented in `docs/*_PHASES.md`. After this audit, no test filename carries a phase marker.
