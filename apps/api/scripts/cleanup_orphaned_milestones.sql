-- Delete server-side funnel milestones whose deal no longer exists.
--
-- Server milestones (payment_received, chat_escalated) record their deal as
-- `metadata->>'crm_event_id'`. There is no FK — storefront_events is a
-- write-heavy telemetry table and deliberately doesn't take one — so a
-- milestone outlives the deal it describes. Smoke runs create a deal, take a
-- payment, then delete the deal on cleanup, and the milestone stays behind.
--
-- On 2026-08-05 that left 381 orphaned payment_received rows totalling
-- $207,230, which the admin analytics dashboard was summing into "Revenue
-- attributed" — against a payments table containing nothing at all.
--
-- The dashboard no longer counts them (see _live_deal_exists in
-- modules/analytics/services/storefront_analytics_service.py), so this script
-- is OPTIONAL housekeeping, not a fix. Run it to stop the junk accumulating
-- in the table; skip it and nothing user-visible changes.
--
-- Usage (review first, then commit):
--     psql "$DATABASE_URL" -f scripts/cleanup_orphaned_milestones.sql
--
-- NOTE: page_view / vehicle_view rows from smoke runs are NOT touched. They
-- carry no crm_event_id, so they can't be told apart from real traffic
-- without guessing, and inflating a page-view count is a far smaller lie
-- than inventing revenue.

BEGIN;

-- What would go, before it goes.
SELECT
    event_name,
    COUNT(*)                                                   AS rows_to_delete,
    SUM((metadata->>'amount_cents')::bigint) / 100.0           AS dollars_removed,
    MIN(occurred_at)                                           AS oldest,
    MAX(occurred_at)                                           AS newest
  FROM storefront_events se
 WHERE se.metadata ? 'crm_event_id'
   AND NOT EXISTS (
       SELECT 1 FROM events e
        WHERE e.id::text = se.metadata->>'crm_event_id'
   )
 GROUP BY event_name
 ORDER BY rows_to_delete DESC;

DELETE FROM storefront_events se
 WHERE se.metadata ? 'crm_event_id'
   AND NOT EXISTS (
       SELECT 1 FROM events e
        WHERE e.id::text = se.metadata->>'crm_event_id'
   );

-- Should report 0 remaining orphans.
SELECT COUNT(*) AS orphans_remaining
  FROM storefront_events se
 WHERE se.metadata ? 'crm_event_id'
   AND NOT EXISTS (
       SELECT 1 FROM events e
        WHERE e.id::text = se.metadata->>'crm_event_id'
   );

COMMIT;
