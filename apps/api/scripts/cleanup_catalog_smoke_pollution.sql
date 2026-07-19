-- Cleanup of leaked rows from catalog smoke tests.
--
-- These rows are created by tests/test_catalog_*_smoke.py when the script
-- crashes between _seed() and _cleanup() and leaves seed rows behind. The
-- SKU obfuscation phase runs (P2..P7) are the most common offenders.
--
-- Usage on the VPS:
--   1. Run this file with psql:  psql "$DATABASE_URL" -f scripts/cleanup_catalog_smoke_pollution.sql
--      The script ends in ROLLBACK so nothing is applied. Inspect the
--      preview-before counts and the post-delete counts (which should
--      all be 0 inside the transaction).
--   2. If the counts look right, change the final `ROLLBACK;` to
--      `COMMIT;` and run again to apply.
--
-- Scope (all matched on smoke-only fields, not customer-entered text):
--   catalog_items.internal_sku LIKE  TEST-%, P3-SEARCH-%, P4-RENDER-%,
--                                    P6-SAMP-%, P7-HARD-%, CAT-ROUTER-%
--   contacts.display_name     LIKE  TEST-P2-%, P4-RENDER-%, P7-HARD-%
--   events.event_name         LIKE  TEST-P2-%, P4-RENDER-%, P7-HARD-%
--   quotes / invoices         via   event_id IN (smoke events)
--   users.email               LIKE  p6-%@example.com, catalog-router-%@example.com

\set ON_ERROR_STOP on

BEGIN;

\echo ''
\echo '=== preview: catalog_items by prefix ==='
SELECT
  CASE
    WHEN internal_sku LIKE 'TEST-P2-%'    THEN 'TEST-P2-'
    WHEN internal_sku LIKE 'TEST-%'       THEN 'TEST-'
    WHEN internal_sku LIKE 'P3-SEARCH-%'  THEN 'P3-SEARCH-'
    WHEN internal_sku LIKE 'P4-RENDER-%'  THEN 'P4-RENDER-'
    WHEN internal_sku LIKE 'P6-SAMP-%'    THEN 'P6-SAMP-'
    WHEN internal_sku LIKE 'P7-HARD-%'    THEN 'P7-HARD-'
    WHEN internal_sku LIKE 'CAT-ROUTER-%' THEN 'CAT-ROUTER-'
  END AS prefix,
  COUNT(*) AS rows
FROM catalog_items
WHERE internal_sku LIKE 'TEST-%'
   OR internal_sku LIKE 'P3-SEARCH-%'
   OR internal_sku LIKE 'P4-RENDER-%'
   OR internal_sku LIKE 'P6-SAMP-%'
   OR internal_sku LIKE 'P7-HARD-%'
   OR internal_sku LIKE 'CAT-ROUTER-%'
GROUP BY 1
ORDER BY 1;

\echo ''
\echo '=== preview: sample contacts (smoke prefix on display_name) ==='
SELECT id, display_name
FROM contacts
WHERE display_name LIKE 'TEST-P2-%'
   OR display_name LIKE 'P4-RENDER-%'
   OR display_name LIKE 'P7-HARD-%'
ORDER BY display_name
LIMIT 30;

\echo ''
\echo '=== preview: sample events (smoke prefix on event_name) ==='
SELECT id, event_name, status, created_at
FROM events
WHERE event_name LIKE 'TEST-P2-%'
   OR event_name LIKE 'P4-RENDER-%'
   OR event_name LIKE 'P7-HARD-%'
ORDER BY event_name
LIMIT 30;

\echo ''
\echo '=== preview: row counts before delete ==='
WITH smoke_events AS (
  SELECT id FROM events
  WHERE event_name LIKE 'TEST-P2-%'
     OR event_name LIKE 'P4-RENDER-%'
     OR event_name LIKE 'P7-HARD-%'
),
smoke_quotes AS (
  SELECT id FROM quotes WHERE event_id IN (SELECT id FROM smoke_events)
),
smoke_invoices AS (
  SELECT id FROM invoices WHERE event_id IN (SELECT id FROM smoke_events)
)
SELECT
  (SELECT COUNT(*) FROM catalog_items
     WHERE internal_sku LIKE 'TEST-%'
        OR internal_sku LIKE 'P3-SEARCH-%'
        OR internal_sku LIKE 'P4-RENDER-%'
        OR internal_sku LIKE 'P6-SAMP-%'
        OR internal_sku LIKE 'P7-HARD-%'
        OR internal_sku LIKE 'CAT-ROUTER-%')                               AS catalog_items,
  (SELECT COUNT(*) FROM contacts
     WHERE display_name LIKE 'TEST-P2-%'
        OR display_name LIKE 'P4-RENDER-%'
        OR display_name LIKE 'P7-HARD-%')                                  AS contacts,
  (SELECT COUNT(*) FROM smoke_events)                                      AS events,
  (SELECT COUNT(*) FROM smoke_quotes)                                      AS quotes,
  (SELECT COUNT(*) FROM quote_line_items
     WHERE quote_id IN (SELECT id FROM smoke_quotes))                      AS quote_line_items,
  (SELECT COUNT(*) FROM quote_invitations
     WHERE quote_id IN (SELECT id FROM smoke_quotes))                      AS quote_invitations,
  (SELECT COUNT(*) FROM smoke_invoices)                                    AS invoices,
  (SELECT COUNT(*) FROM invoice_line_items
     WHERE invoice_id IN (SELECT id FROM smoke_invoices))                  AS invoice_line_items,
  (SELECT COUNT(*) FROM invoice_installments
     WHERE invoice_id IN (SELECT id FROM smoke_invoices))                  AS invoice_installments,
  (SELECT COUNT(*) FROM invoice_invitations
     WHERE invoice_id IN (SELECT id FROM smoke_invoices))                  AS invoice_invitations,
  (SELECT COUNT(*) FROM users
     WHERE email LIKE 'p6-%@example.com'
        OR email LIKE 'catalog-router-%@example.com')                      AS users
;

\echo ''
\echo '=== deleting in dependency order ==='

-- Quote children, then quotes (before invoices, so the quote->invoice
-- chk_quote_converted_consistent CHECK doesn't see a transitional state).
DELETE FROM quote_invitations
WHERE quote_id IN (
  SELECT id FROM quotes WHERE event_id IN (
    SELECT id FROM events
    WHERE event_name LIKE 'TEST-P2-%'
       OR event_name LIKE 'P4-RENDER-%'
       OR event_name LIKE 'P7-HARD-%'
  )
);

DELETE FROM quote_line_items
WHERE quote_id IN (
  SELECT id FROM quotes WHERE event_id IN (
    SELECT id FROM events
    WHERE event_name LIKE 'TEST-P2-%'
       OR event_name LIKE 'P4-RENDER-%'
       OR event_name LIKE 'P7-HARD-%'
  )
);

DELETE FROM quotes
WHERE event_id IN (
  SELECT id FROM events
  WHERE event_name LIKE 'TEST-P2-%'
     OR event_name LIKE 'P4-RENDER-%'
     OR event_name LIKE 'P7-HARD-%'
);

-- Invoice children, then invoices.
DELETE FROM invoice_invitations
WHERE invoice_id IN (
  SELECT id FROM invoices WHERE event_id IN (
    SELECT id FROM events
    WHERE event_name LIKE 'TEST-P2-%'
       OR event_name LIKE 'P4-RENDER-%'
       OR event_name LIKE 'P7-HARD-%'
  )
);

DELETE FROM invoice_installments
WHERE invoice_id IN (
  SELECT id FROM invoices WHERE event_id IN (
    SELECT id FROM events
    WHERE event_name LIKE 'TEST-P2-%'
       OR event_name LIKE 'P4-RENDER-%'
       OR event_name LIKE 'P7-HARD-%'
  )
);

DELETE FROM invoice_line_items
WHERE invoice_id IN (
  SELECT id FROM invoices WHERE event_id IN (
    SELECT id FROM events
    WHERE event_name LIKE 'TEST-P2-%'
       OR event_name LIKE 'P4-RENDER-%'
       OR event_name LIKE 'P7-HARD-%'
  )
);

DELETE FROM invoices
WHERE event_id IN (
  SELECT id FROM events
  WHERE event_name LIKE 'TEST-P2-%'
     OR event_name LIKE 'P4-RENDER-%'
     OR event_name LIKE 'P7-HARD-%'
);

-- Events, contacts, catalog rows.
DELETE FROM events
WHERE event_name LIKE 'TEST-P2-%'
   OR event_name LIKE 'P4-RENDER-%'
   OR event_name LIKE 'P7-HARD-%';

DELETE FROM contacts
WHERE display_name LIKE 'TEST-P2-%'
   OR display_name LIKE 'P4-RENDER-%'
   OR display_name LIKE 'P7-HARD-%';

DELETE FROM catalog_items
WHERE internal_sku LIKE 'TEST-%'
   OR internal_sku LIKE 'P3-SEARCH-%'
   OR internal_sku LIKE 'P4-RENDER-%'
   OR internal_sku LIKE 'P6-SAMP-%'
   OR internal_sku LIKE 'P7-HARD-%'
   OR internal_sku LIKE 'CAT-ROUTER-%';

-- Smoke users (only test_catalog_router_smoke and test_catalog_samples_smoke
-- write to users, both with @example.com fixtures).
DELETE FROM users
WHERE email LIKE 'p6-%@example.com'
   OR email LIKE 'catalog-router-%@example.com';

\echo ''
\echo '=== post-delete counts (all should be 0 inside this transaction) ==='
WITH smoke_events AS (
  SELECT id FROM events
  WHERE event_name LIKE 'TEST-P2-%'
     OR event_name LIKE 'P4-RENDER-%'
     OR event_name LIKE 'P7-HARD-%'
),
smoke_quotes AS (
  SELECT id FROM quotes WHERE event_id IN (SELECT id FROM smoke_events)
),
smoke_invoices AS (
  SELECT id FROM invoices WHERE event_id IN (SELECT id FROM smoke_events)
)
SELECT
  (SELECT COUNT(*) FROM catalog_items
     WHERE internal_sku LIKE 'TEST-%'
        OR internal_sku LIKE 'P3-SEARCH-%'
        OR internal_sku LIKE 'P4-RENDER-%'
        OR internal_sku LIKE 'P6-SAMP-%'
        OR internal_sku LIKE 'P7-HARD-%'
        OR internal_sku LIKE 'CAT-ROUTER-%')                               AS catalog_items,
  (SELECT COUNT(*) FROM contacts
     WHERE display_name LIKE 'TEST-P2-%'
        OR display_name LIKE 'P4-RENDER-%'
        OR display_name LIKE 'P7-HARD-%')                                  AS contacts,
  (SELECT COUNT(*) FROM smoke_events)                                      AS events,
  (SELECT COUNT(*) FROM smoke_quotes)                                      AS quotes,
  (SELECT COUNT(*) FROM quote_line_items
     WHERE quote_id IN (SELECT id FROM smoke_quotes))                      AS quote_line_items,
  (SELECT COUNT(*) FROM quote_invitations
     WHERE quote_id IN (SELECT id FROM smoke_quotes))                      AS quote_invitations,
  (SELECT COUNT(*) FROM smoke_invoices)                                    AS invoices,
  (SELECT COUNT(*) FROM invoice_line_items
     WHERE invoice_id IN (SELECT id FROM smoke_invoices))                  AS invoice_line_items,
  (SELECT COUNT(*) FROM invoice_installments
     WHERE invoice_id IN (SELECT id FROM smoke_invoices))                  AS invoice_installments,
  (SELECT COUNT(*) FROM invoice_invitations
     WHERE invoice_id IN (SELECT id FROM smoke_invoices))                  AS invoice_invitations,
  (SELECT COUNT(*) FROM users
     WHERE email LIKE 'p6-%@example.com'
        OR email LIKE 'catalog-router-%@example.com')                      AS users
;

-- Default: discard. Change to COMMIT; once the counts above look right.
ROLLBACK;
