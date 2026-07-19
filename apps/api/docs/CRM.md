# CRM: Contacts, Events, Kanban

The CRM domain — what happens after a lead books an appointment. Consists of
contact identity, event lifecycle, the quince workflow, and the kanban view.

## Mental model

```
Contact            the person/family — root identity
   |
   +-- Appointment     the lead origin — booking widget submission
   |
   +-- Event           the "deal" — one quinceañera
          |
          +-- EventParticipant   the celebrant + (eventually) court members
          |
          +-- EventStatusChangeEvent
                                 audit row per status transition
          |
          +-- Appointment.crm_event_id
                                 link from appointment back to event
```

The `Appointment` row is the **lead origin**. The `Event` is what staff create
when the lead is qualified. Most leads will have exactly one appointment +
one event; rescheduled appointments share a `contact_id` and link to the same
event.

For promoted bookings, the contact remains the stable CRM identity, but the
appointment's celebrant name is the event context. This prevents a shared phone
or parent contact from making every lead inherit the first contact name.

## Files

| File | Purpose |
|---|---|
| [database/migrations/014_create_contacts.py](../database/migrations/014_create_contacts.py) | Contacts table + backfill from appointments |
| [database/migrations/015_create_events.py](../database/migrations/015_create_events.py) | Events, participants, status_change_events |
| [database/models.py](../database/models.py) | `Contact`, `Event`, `EventParticipant`, `EventStatusChangeEvent` |
| [services/event_workflow.py](../services/event_workflow.py) | Status definitions per event type |
| [services/event_service.py](../services/event_service.py) | Promote, change_status, board, walk-in |
| [services/contact_service.py](../services/contact_service.py) | Find-or-create contact identity |
| [api/routers/events.py](../api/routers/events.py) | All `/api/events/*` endpoints |
| [frontend/src/pages/Pipeline.jsx](../frontend/src/pages/Pipeline.jsx) | Kanban page |
| [frontend/src/components/EventQuickViewDrawer.jsx](../frontend/src/components/EventQuickViewDrawer.jsx) | Card click drawer |
| [frontend/src/pages/EventDetail.jsx](../frontend/src/pages/EventDetail.jsx) | Full event view |
| [tests/test_events_smoke.py](../tests/test_events_smoke.py) | End-to-end events smoke |

## Contact identity

Phone (E.164) is the canonical identity. Email is fallback only when phone
normalization fails.

- `contacts.phone_e164` has a partial unique index (where not null).
- `contacts.email` is indexed but not unique — parents and quinces share inboxes.
- Booking widget calls `contact_service.find_or_create_contact()` before
  inserting an appointment. Race-safe via SAVEPOINT.

Backfill from existing appointments is in migration 014: pass 1 dedupes by
phone_e164, pass 2 dedupes by email-only for legacy rows where phone wasn't
normalized.

## Quinceañera status workflow

Defined in [services/event_workflow.py](../services/event_workflow.py) and
mirrored as a CHECK constraint in migration 015. **Keep both in sync.**

| Code | Label | Terminal | Meaning |
|---|---|---|---|
| `lead` | Lead | | Appointment booked, not yet attended |
| `consulted` | Consulted | | Came in, browsed, no purchase yet |
| `sold` | Sold | | Deposit paid, dress selected |
| `on_order` | On Order | | Special order with vendor |
| `arrived` | Arrived | | Dress in store |
| `in_alterations` | In Alterations | | Being altered |
| `ready_for_pickup` | Ready for Pickup | | Awaiting customer |
| `picked_up` | Picked Up | ✓ | Completed |
| `cancelled` | Cancelled | ✓ | Lost or refunded |

Sample sales (no `on_order` step) are tracked at the dress-order level, not
event status. Event status reflects the bigger lifecycle.

## Promotion: appointment -> event

The standard path is **automatic on booking**. When a customer submits the
public widget, `api/routers/booking.py:create_appointment` immediately
promotes the new appointment to a quinceañera `events` row in `lead` status.
Reschedules carry `crm_event_id` forward so the same lead keeps both visits;
customer-token cancellations and admin cancellations mirror onto the linked
event as `cancelled`.

```
services.event_service.promote_appointment_to_event:
  - reads appointment + linked enrichment + linked contact
  - names the event from the appointment celebrant unless an explicit
    event_name override was provided
  - creates event with prefilled fields:
      court_size, quince_theme, quince_theme_colors, budget_range, event_date
  - creates EventParticipant(role='quinceanera') from the appointment celebrant
    and booking contact fields
  - writes audit row: from_status=NULL, to_status='lead'
  - sets appointment.crm_event_id = new event id
```

The appointments admin tab and manual "Promote to Event" button were removed
once auto-promotion landed — every booking is already a lead in the kanban.

Idempotency: a second call for the same appointment returns 409.

Backfill / escape hatch: `POST /api/events { from_appointment_id }` still
exists for one-off promotions of pre-auto-promotion appointments, and walk-ins
post body `{ primary_contact_id, event_name }` against the same endpoint
(shared seed logic via `_seed_initial_event_state`).

## Status transitions

```
PATCH /api/events/{id}/status
  body: { status: 'consulted', notes: 'Loved the trumpet silhouette' }
```

`event_service.change_event_status`:
- Validates the new status is in the workflow
- No-op if status unchanged (no audit noise)
- Updates `events.status` + `events.status_changed_at`
- Inserts an `event_status_change_events` row

`status_changed_at` is the basis for "time in column" badges and at-risk
queries. It updates only on real transitions, separate from `updated_at`.

## Kanban data flow

```
GET /api/events/board?event_type=quinceanera

services.event_service.get_board_data:
  - one query joining events + contacts + (optional) users
  - subquery: max(slot_start_at) per crm_event_id for "last appointment" badge
  - groups cards by status
  - returns columns in workflow sort order
```

Front-end (Pipeline.jsx):
- `useQuery` caches the board for 30s.
- Drag-drop and the quick-view drawer's status dropdown both call a
  `commitStatusChange` helper that applies the optimistic move to the cache
  **before** clearing the drag overlay, then fires the mutation.
- On error, the mutation's `onError` rolls back to the snapshot captured by
  the helper (passed through as a mutation variable).
- `onSettled` invalidates so the next refetch reconciles with the server.

See [FRONTEND.md](FRONTEND.md#optimistic-mutation-the-kanban) for the full
helper + hook shape and why optimistic state lives outside `onMutate`.

## Quick view drawer

Click a card -> drawer with:
- Event facts (date, court size, theme, last appointment, owner)
- Status dropdown (uses the same `patchEventStatus` mutation)
- "Open full view" -> `/events/{id}`

Drag activation distance is 5px so a click under that threshold opens the
drawer rather than starting a drag.

## Event detail page

`/events/{id}` -> `frontend/src/pages/EventDetail.jsx`

Shows:
- All event fields
- Primary contact (name + phone + email)
- Participants list
- Linked appointments
- Status history (last 20 transitions)
- Placeholder note: dress orders / alterations / payments coming later.

This page is intentionally light. It exists so the "Open full view" button
isn't a dead link, and so navigating to `/events/{id}` directly works (e.g.
from a notification email later).

## Smoke testing

Run [tests/test_events_smoke.py](../tests/test_events_smoke.py). Covers
unauthenticated 401, validator edges, promote, idempotency, status patch
+ audit row, board read, walk-in, missing-id 404. Cleans up its own rows.

## What's NOT in v1

Deferred deliberately to keep the spine shippable:
- Court members as participants (just `court_size: int` for now).
- Dress orders / alterations / payments (no tables).
- Per-status workflow rules (e.g. "can't go from lead to picked_up").
- Event search / filters on the kanban.
- Activity timeline (notes / calls / emails) — only status history exists.

Add the next layer when staff ask for it, not before.
