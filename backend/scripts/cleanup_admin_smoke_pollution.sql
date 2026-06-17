-- Cleanup leaked admin-dashboard smoke rows.
--
-- These are fixtures from smoke tests that show up in owner-facing admin
-- surfaces when a smoke aborts before its per-test cleanup runs. The most
-- visible leak is /settings/staff/profiles because smoke staff are real
-- rows in users.
--
-- Usage:
--   Preview only, rolls back:
--     psql "$DATABASE_URL" -f scripts/cleanup_admin_smoke_pollution.sql
--
--   Apply after preview looks right:
--     psql "$DATABASE_URL" -v apply=true -f scripts/cleanup_admin_smoke_pollution.sql
--
-- Scope is intentionally smoke-only prefixes/names used by tests. It avoids
-- broad names like "Smoke Admin" unless paired with the known smoke username
-- families.

\set ON_ERROR_STOP on

\if :{?apply}
\else
  \set apply false
\endif

BEGIN;

-- Mirrors the deliberate cleanup path used by smoke tests. This is scoped to
-- the current transaction and is needed for append-only audit tables.
SET LOCAL audit_tables.allow_mutation = on;

CREATE TEMP TABLE smoke_users ON COMMIT DROP AS
SELECT id
FROM users
WHERE username LIKE ANY (ARRAY[
    'docs-smoke-%',
    'portal-smoke-%',
    'pdf-smoke-%',
    'discount-2a-smoke-%',
    'sales-p8b-%',
    'admin-p5-%',
    'sales-p5-%',
    'admin-p7s2-%',
    'sales-p7s2-%',
    'admin-smoke-%',
    'sales-smoke-%',
    'smoke-notif-routing-%',
    'smoke-notif-prefs-%',
    'smoke-digest-runner-%',
    'smoke-resend-sched-%',
    'smoke-shift-events-%',
    'reminder-smoke-%',
    'sales-participant-%',
    '%-comp-%',
    '%-p0sched-%',
    '%-p10s4-%',
    'd1-dep-smoke-admin-%',
    'd2-soft-smoke-admin-%',
    'd3b-arch-ep-smoke-admin-%',
    'd3c-recycle-smoke-%'
  ])
  OR (
    username LIKE ANY (ARRAY[
      'docs-smoke-%',
      'portal-smoke-%',
      'pdf-smoke-%',
      'discount-2a-smoke-%',
      'sales-p8b-%',
      'admin-p5-%',
      'sales-p5-%',
      'admin-p7s2-%',
      'sales-p7s2-%'
    ])
    AND full_name LIKE ANY (ARRAY[
      'Docs Smoke %',
      'Portal Smoke %',
      'PDF Smoke %',
      'Phase 2a Smoke %',
      'P5 %',
      'P7S2 %',
      'P8B %'
    ])
  );

CREATE TEMP TABLE smoke_contacts ON COMMIT DROP AS
SELECT id
FROM contacts
WHERE display_name LIKE ANY (ARRAY[
    'Docs Smoke %',
    'PDF Smoke %',
    'Phase 2a %',
    'Portal %',
    'Phase 5 %',
    'Phase 7 %',
    'P3 %',
    'P7S2 %',
    'P8B %',
    'TEST-P2-%',
    'P4-RENDER-%',
    'P7-HARD-%',
    'AdminBypass%',
    'Sales Search Smoke %',
    'Walk-In Assign Smoke %',
    'Sales Assign Smoke %',
    'Admin Notes Audit Smoke %',
    'Quote Approval Notif Smoke %',
    'Digest In-Store Smoke %',
    'Phase 10 Smoke %',
    'Admin Owner Reassign Smoke %',
    'PhaseSeven Smoke',
    'Smoke Tester',
    'Notif% Tester',
    'D1 Dep Smoke %',
    'D2 Soft Smoke %',
    'D3A Arch Smoke %',
    'D3B Arch Ep Smoke %',
    'D3C Recycle Smoke %'
  ])
  OR email LIKE ANY (ARRAY[
    'docs-smoke-%@example.com',
    'portal-smoke-%@example.com',
    'pdf-smoke-%@example.com',
    'discount-2a-smoke-%@example.com',
    'p3-%@example.com',
    'sssmoke-%@example.com',
    'walkin-assign-%@example.com',
    'sa-smoke-%@example.com',
    'sa-cascade-%@example.com',
    'admin-notes-audit-%@example.com',
    'quote-approval-notif-%@example.com',
    'digest-instore-%@example.com',
    'phase10-smoke-%@example.com',
    'admin-owner-reassign-%@example.com',
    'calc-first-%@example.com',
    'reschedule-%@example.com',
    'notif-smoke@example.com',
    'smoke+test@example.com',
    'should-not-merge-by-email@example.com',
    'd1-dep-smoke-%@example.com',
    'd2-soft-smoke-%@example.com',
    'd3a-arch-smoke-%@example.com',
    'd3b-arch-ep-smoke-%@example.com',
    'd3c-recycle-smoke-%@example.com'
  ]);

CREATE TEMP TABLE smoke_events ON COMMIT DROP AS
SELECT id
FROM events
WHERE primary_contact_id IN (SELECT id FROM smoke_contacts)
   OR event_name LIKE ANY (ARRAY[
    'Docs Smoke %',
    'PDF Smoke %',
    'Phase 2a %',
    'Portal %',
    'Phase 5 %',
    'Phase 7 %',
    'P3 %',
    'P7S2 %',
    'P8B %',
    'TEST-P2-%',
    'P4-RENDER-%',
    'P7-HARD-%',
    'Sales Search Smoke %',
    'Walk-In Assign Smoke %',
    'Sales Assign Smoke %',
    'Admin Notes Audit Smoke %',
    'Quote Approval Notif Smoke %',
    'Digest In-Store Smoke %',
    'Phase 10 Smoke %',
    'Admin Owner Reassign Smoke %',
    'Notif Smoke Tester''s Quince',
    'calc-first smoke',
    'reschedule smoke',
    'Sales Participant Smoke Event'
  ]);

CREATE TEMP TABLE smoke_invoices ON COMMIT DROP AS
SELECT id
FROM invoices
WHERE event_id IN (SELECT id FROM smoke_events)
   OR contact_id IN (SELECT id FROM smoke_contacts);

CREATE TEMP TABLE smoke_quotes ON COMMIT DROP AS
SELECT id
FROM quotes
WHERE event_id IN (SELECT id FROM smoke_events)
   OR contact_id IN (SELECT id FROM smoke_contacts)
   OR converted_invoice_id IN (SELECT id FROM smoke_invoices);

CREATE TEMP TABLE smoke_payments ON COMMIT DROP AS
SELECT id
FROM payments
WHERE contact_id IN (SELECT id FROM smoke_contacts)
   OR id IN (
      SELECT payment_id
      FROM payment_allocations
      WHERE invoice_id IN (SELECT id FROM smoke_invoices)
   );

CREATE TEMP TABLE smoke_appointments ON COMMIT DROP AS
SELECT id
FROM appointments
WHERE contact_id IN (SELECT id FROM smoke_contacts)
   OR crm_event_id IN (SELECT id FROM smoke_events)
   OR email LIKE ANY (ARRAY[
      'docs-smoke-%@example.com',
      'portal-smoke-%@example.com',
      'pdf-smoke-%@example.com',
      'discount-2a-smoke-%@example.com',
      'calc-first-%@example.com',
      'reschedule-%@example.com',
      'notif-smoke@example.com',
      'smoke+test@example.com'
   ])
   OR event_id LIKE ANY (ARRAY[
      'notif-smoke-%',
      'smoke-%',
      'evt-calc-first-%',
      'evt-reschedule-%'
   ]);

\echo ''
\echo '=== preview: smoke staff profiles ==='
SELECT u.id, u.username, u.full_name, u.role, u.created_at
FROM users u
JOIN smoke_users su ON su.id = u.id
ORDER BY u.created_at NULLS LAST, u.id;

\echo ''
\echo '=== preview: smoke pipeline/events ==='
SELECT e.id, e.event_name, e.status, e.primary_contact_id, e.created_at
FROM events e
JOIN smoke_events se ON se.id = e.id
ORDER BY e.created_at NULLS LAST, e.id;

\echo ''
\echo '=== preview: row counts before delete ==='
SELECT
  (SELECT COUNT(*) FROM smoke_users) AS users,
  (SELECT COUNT(*) FROM smoke_contacts) AS contacts,
  (SELECT COUNT(*) FROM smoke_events) AS events,
  (SELECT COUNT(*) FROM smoke_appointments) AS appointments,
  (SELECT COUNT(*) FROM smoke_quotes) AS quotes,
  (SELECT COUNT(*) FROM smoke_invoices) AS invoices,
  (SELECT COUNT(*) FROM smoke_payments) AS payments,
  (SELECT COUNT(*) FROM staff_punches WHERE user_id IN (SELECT id FROM smoke_users)) AS staff_punches,
  (SELECT COUNT(*) FROM staff_shifts WHERE user_id IN (SELECT id FROM smoke_users)) AS staff_shifts,
  (SELECT COUNT(*) FROM time_off_requests WHERE user_id IN (SELECT id FROM smoke_users)) AS time_off_requests;

\echo ''
\echo '=== deleting in dependency order ==='

-- Staff/attendance dependents that block deleting smoke users.
DELETE FROM attendance_pre_close_reminders
WHERE punch_id IN (
  SELECT id FROM staff_punches WHERE user_id IN (SELECT id FROM smoke_users)
);

DELETE FROM staff_punch_audit_events
WHERE actor_user_id IN (SELECT id FROM smoke_users)
   OR punch_id IN (
      SELECT id FROM staff_punches WHERE user_id IN (SELECT id FROM smoke_users)
   );

UPDATE staff_schedule_entries
SET actual_clock_in_punch_id = NULL
WHERE actual_clock_in_punch_id IN (
  SELECT id FROM staff_punches WHERE user_id IN (SELECT id FROM smoke_users)
);

UPDATE staff_schedule_entries
SET actual_clock_out_punch_id = NULL
WHERE actual_clock_out_punch_id IN (
  SELECT id FROM staff_punches WHERE user_id IN (SELECT id FROM smoke_users)
);

DELETE FROM staff_punches
WHERE user_id IN (SELECT id FROM smoke_users);

DELETE FROM time_off_decision_events
WHERE actor_user_id IN (SELECT id FROM smoke_users)
   OR request_id IN (
      SELECT id FROM time_off_requests WHERE user_id IN (SELECT id FROM smoke_users)
   );

DELETE FROM time_off_requests
WHERE user_id IN (SELECT id FROM smoke_users);

DELETE FROM staff_shift_overrides
WHERE user_id IN (SELECT id FROM smoke_users)
   OR shift_id IN (
      SELECT id FROM staff_shifts WHERE user_id IN (SELECT id FROM smoke_users)
   );

DELETE FROM staff_shifts
WHERE user_id IN (SELECT id FROM smoke_users);

-- Scheduling Phase 1-3 shift requests + open posts. Events are append-only;
-- the allow_mutation GUC set at the top of this transaction permits the
-- delete. Remove requests before the schedule entries they reference.
DELETE FROM staff_shift_request_events
WHERE request_id IN (
    SELECT id FROM staff_shift_requests
    WHERE requester_user_id IN (SELECT id FROM smoke_users)
);

DELETE FROM staff_shift_requests
WHERE requester_user_id IN (SELECT id FROM smoke_users);

DELETE FROM open_shift_posts
WHERE created_by_user_id IN (SELECT id FROM smoke_users)
   OR claimed_by_user_id IN (SELECT id FROM smoke_users);

DELETE FROM staff_schedule_entries
WHERE user_id IN (SELECT id FROM smoke_users);

DELETE FROM recurring_unavailability
WHERE user_id IN (SELECT id FROM smoke_users);

DELETE FROM password_reset_tokens
WHERE user_id IN (SELECT id FROM smoke_users);

-- Appointment children, then appointments.
DELETE FROM appointment_session_events
WHERE appointment_id IN (SELECT id FROM smoke_appointments);

DELETE FROM appointment_tried_on_items
WHERE appointment_id IN (SELECT id FROM smoke_appointments);

DELETE FROM appointment_enrichment_responses
WHERE appointment_id IN (SELECT id FROM smoke_appointments);

DELETE FROM notification_jobs
WHERE appointment_id IN (SELECT id FROM smoke_appointments)
   OR recipient_user_id IN (SELECT id FROM smoke_users)
   OR (payload ->> 'tag') IN (
        'smoke-notif-routing', 'smoke-notif-prefs', 'smoke-digest-runner'
      );

-- staff_notification_events smoke cleanup. The FK to users is ON DELETE
-- SET NULL, so actor_user_id can be NULL'd before this DELETE runs if a
-- prior smoke deleted the actor — the actor_user_id filter alone would
-- miss the row. The subject_id filters (matched against the smoke_events
-- and smoke_appointments temp tables built at the top of this script,
-- before any deletes) catch event- and appointment-anchored rows even
-- after the FK has been nulled. payload.tag stays as the explicit hook
-- for B1/B2.5/B2.3 routing/preferences/digest-runner smokes that tag
-- their events directly.
DELETE FROM staff_notification_events
WHERE (payload ->> 'tag') IN (
        'smoke-notif-routing', 'smoke-notif-prefs', 'smoke-digest-runner'
      )
   OR actor_user_id IN (SELECT id FROM smoke_users)
   OR (subject_kind = 'event' AND subject_id IN (SELECT id FROM smoke_events))
   OR (subject_kind = 'appointment' AND subject_id IN (SELECT id FROM smoke_appointments))
   OR payload::text LIKE ANY (ARRAY[
        '%Walk-In Assign Smoke%',
        '%Heal Smoke%',
        '%Notif68 Tester%',
        '%Notif Smoke%',
        '%PhaseSeven Smoke%',
        '%Smoke Tester%'
      ]);

UPDATE appointments
SET rescheduled_from_id = NULL
WHERE rescheduled_from_id IN (SELECT id FROM smoke_appointments);

DELETE FROM appointments
WHERE id IN (SELECT id FROM smoke_appointments);

-- Payments and refund rows.
DELETE FROM refund_events
WHERE payment_id IN (SELECT id FROM smoke_payments);

DELETE FROM payment_allocations
WHERE payment_id IN (SELECT id FROM smoke_payments)
   OR invoice_id IN (SELECT id FROM smoke_invoices);

DELETE FROM payments
WHERE id IN (SELECT id FROM smoke_payments);

-- Quotes before invoices so converted quote consistency never sees a
-- half-deleted invoice tree.
DELETE FROM quote_installments
WHERE quote_id IN (SELECT id FROM smoke_quotes);

DELETE FROM quote_invitations
WHERE quote_id IN (SELECT id FROM smoke_quotes)
   OR contact_id IN (SELECT id FROM smoke_contacts);

DELETE FROM quote_order_discounts
WHERE quote_id IN (SELECT id FROM smoke_quotes);

DELETE FROM quote_line_items
WHERE quote_id IN (SELECT id FROM smoke_quotes);

DELETE FROM quotes
WHERE id IN (SELECT id FROM smoke_quotes);

-- Invoice children, then invoices.
UPDATE event_documents
SET linked_invoice_id = NULL
WHERE linked_invoice_id IN (SELECT id FROM smoke_invoices);

DELETE FROM invoice_installments
WHERE invoice_id IN (SELECT id FROM smoke_invoices);

DELETE FROM invoice_invitations
WHERE invoice_id IN (SELECT id FROM smoke_invoices)
   OR contact_id IN (SELECT id FROM smoke_contacts);

DELETE FROM installment_reminder_state
WHERE installment_id IN (
  SELECT id FROM invoice_installments WHERE invoice_id IN (SELECT id FROM smoke_invoices)
);

DELETE FROM invoice_order_discounts
WHERE invoice_id IN (SELECT id FROM smoke_invoices);

DELETE FROM invoice_line_items
WHERE invoice_id IN (SELECT id FROM smoke_invoices);

DELETE FROM invoices
WHERE id IN (SELECT id FROM smoke_invoices);

DELETE FROM special_orders
WHERE event_id IN (SELECT id FROM smoke_events);

-- Event/contact tree.
DELETE FROM event_participants
WHERE event_id IN (SELECT id FROM smoke_events)
   OR contact_id IN (SELECT id FROM smoke_contacts);

DELETE FROM event_status_change_events
WHERE event_id IN (SELECT id FROM smoke_events)
   OR changed_by_user_id IN (SELECT id FROM smoke_users);

DELETE FROM activity_log
WHERE event_id IN (SELECT id FROM smoke_events)
   OR actor_user_id IN (SELECT id FROM smoke_users);

DELETE FROM event_documents
WHERE event_id IN (SELECT id FROM smoke_events)
   OR uploaded_by_user_id IN (SELECT id FROM smoke_users);

DELETE FROM events
WHERE id IN (SELECT id FROM smoke_events);

DELETE FROM contacts
WHERE id IN (SELECT id FROM smoke_contacts);

DELETE FROM users
WHERE id IN (SELECT id FROM smoke_users);

\echo ''
\echo '=== post-delete counts (all should be 0 inside this transaction) ==='
SELECT
  (SELECT COUNT(*) FROM users WHERE id IN (SELECT id FROM smoke_users)) AS users,
  (SELECT COUNT(*) FROM contacts WHERE id IN (SELECT id FROM smoke_contacts)) AS contacts,
  (SELECT COUNT(*) FROM events WHERE id IN (SELECT id FROM smoke_events)) AS events,
  (SELECT COUNT(*) FROM appointments WHERE id IN (SELECT id FROM smoke_appointments)) AS appointments,
  (SELECT COUNT(*) FROM quotes WHERE id IN (SELECT id FROM smoke_quotes)) AS quotes,
  (SELECT COUNT(*) FROM invoices WHERE id IN (SELECT id FROM smoke_invoices)) AS invoices,
  (SELECT COUNT(*) FROM payments WHERE id IN (SELECT id FROM smoke_payments)) AS payments,
  (SELECT COUNT(*) FROM staff_punches WHERE user_id IN (SELECT id FROM smoke_users)) AS staff_punches,
  (SELECT COUNT(*) FROM staff_shifts WHERE user_id IN (SELECT id FROM smoke_users)) AS staff_shifts,
  (SELECT COUNT(*) FROM time_off_requests WHERE user_id IN (SELECT id FROM smoke_users)) AS time_off_requests;

-- Machine-readable total of top-level entities that were found polluting
-- the DB. Sums users+contacts+events at start-of-script (before deletes).
-- The post-suite sweep in scripts/smoke_handoff.sh greps for this exact
-- prefix to decide whether the suite leaked. Don't rename without
-- updating that script.
SELECT
  (SELECT COUNT(*) FROM smoke_users) +
  (SELECT COUNT(*) FROM smoke_contacts) +
  (SELECT COUNT(*) FROM smoke_events) AS total
\gset
\echo 'POST_RUN_RESIDUE_TOTAL=':total

\if :apply
  COMMIT;
  \echo 'APPLIED cleanup_admin_smoke_pollution.sql'
\else
  ROLLBACK;
  \echo 'ROLLED BACK cleanup_admin_smoke_pollution.sql (pass -v apply=true to apply)'
\endif
