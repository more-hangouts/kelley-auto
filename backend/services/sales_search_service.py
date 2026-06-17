"""Sales-portal lead search.

Drives `GET /api/sales/search/leads` behind the rep dashboard. Deliberately
parallel to `services.search_service`, not a flag on it, because the sales
surface must NEVER expose invoice/quote/payment fields. The admin search at
`services/search_service.py:528` (`_search_invoices`) and `:671`
(`_search_quotes`) explicitly weave monetary values into the sublabel.
Stripping those out at the response layer is fragile; a parallel service
with its own SQL is the safer cut.

Result types:
  - appointment: matched against confirmation code, celebrant/parent names,
    phone digits, contact display name (joined), event name (joined).
    Routes to /appointments/{id}.
  - contact: matched against display name, first/last name, email, phone
    digits. Routes to the contact's most-recent appointment when one
    exists; contacts with no appointment are skipped (the sales portal
    has no contact detail page to drill into).
  - event: matched against event name and quince theme. Routes to the
    event's most-recent appointment when one exists; events with no
    appointment are skipped.

Allowed shape per result: id, label, sublabel (presentational strings only),
contact_id, assigned_user_id, route. No monetary fields, no notes, no
document storage keys, no marketing attribution, no tokens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_type

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session


# Public constants -----------------------------------------------------------

MIN_QUERY_LENGTH = 2
DEFAULT_LIMIT = 5
MAX_LIMIT = 10

ALLOWED_TYPES: frozenset[str] = frozenset({"appointment", "contact", "event"})


@dataclass(frozen=True)
class SalesSearchResult:
    type: str
    id: int
    label: str
    sublabel: str
    contact_id: int | None
    assigned_user_id: int | None
    route: str


# Query-shape preprocessing --------------------------------------------------

_PHONE_QUERY_RE = re.compile(r"^[\d\s\-\(\)\+]+$")
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]")


def _phone_digits(q: str) -> str:
    if not _PHONE_QUERY_RE.match(q):
        return ""
    return re.sub(r"\D", "", q)


def _confirmation_code_form(q: str) -> str:
    """Canonical confirmation-code form: uppercase alphanumerics only.

    Booked customers see hyphenated codes ("ABC-123-DEF"); the column
    stores the canonical form ("ABC123DEF"). Normalize the query to the
    canonical form before exact/prefix matching so either rendering hits.
    Returns empty string for queries that don't look code-shaped.
    """
    s = _NON_ALNUM_RE.sub("", q).upper()
    # Confirmation codes are >= 6 alphanumerics in v1; under that, the
    # query is almost certainly a name fragment and a prefix match against
    # the code column would be noisy.
    if len(s) < 4:
        return ""
    return s


# Public API -----------------------------------------------------------------


def search_leads(
    db: Session, *, q: str, limit: int = DEFAULT_LIMIT
) -> list[SalesSearchResult]:
    """Run the sales-portal lead search.

    Returns ranked results across appointments, contacts, and events.
    Per-type cap is `limit`; total results = sum across types. Queries
    shorter than `MIN_QUERY_LENGTH` after trim return [] (router gates
    too, but the service short-circuits to skip DB roundtrips).
    """
    qn = q.strip()
    if len(qn) < MIN_QUERY_LENGTH:
        return []

    cap = max(1, min(int(limit), MAX_LIMIT))

    results: list[SalesSearchResult] = []
    results.extend(_search_appointments(db, qn=qn, limit=cap))
    results.extend(_search_events(db, qn=qn, limit=cap))
    results.extend(_search_contacts(db, qn=qn, limit=cap))
    return results


# Appointments ---------------------------------------------------------------

_APPOINTMENTS_SQL = sql_text(
    """
    WITH q AS (
        SELECT
            f_unaccent(lower(CAST(:q AS text))) AS qn,
            CAST(:q_digits AS text)            AS qd,
            CAST(:q_code AS text)              AS qc
    ),
    candidates AS (
        -- confirmation code: exact (tier 0)
        SELECT a.id, 0 AS tier
          FROM appointments a, q
         WHERE q.qc != '' AND a.confirmation_code = q.qc

        UNION ALL
        -- confirmation code: prefix (tier 1)
        SELECT a.id, 1
          FROM appointments a, q
         WHERE q.qc != '' AND a.confirmation_code LIKE q.qc || '%'

        UNION ALL
        -- celebrant/parent names: substring (tier 2)
        SELECT a.id, 2
          FROM appointments a, q
         WHERE (
                f_unaccent(lower(coalesce(a.celebrant_first_name, ''))) LIKE '%' || q.qn || '%'
             OR f_unaccent(lower(coalesce(a.celebrant_last_name,  ''))) LIKE '%' || q.qn || '%'
             OR f_unaccent(lower(coalesce(a.parent_first_name,    ''))) LIKE '%' || q.qn || '%'
             OR f_unaccent(lower(coalesce(a.parent_last_name,     ''))) LIKE '%' || q.qn || '%'
           )

        UNION ALL
        -- phone digits substring (tier 2). Lets the floor type the last
        -- four off a caller ID and find the booking.
        SELECT a.id, 2
          FROM appointments a, q
         WHERE q.qd != ''
           AND a.phone_e164 IS NOT NULL
           AND a.phone_e164 LIKE '%' || q.qd || '%'

        UNION ALL
        -- email substring (tier 3). Lower priority than phone because
        -- staff rarely search by email on the floor.
        SELECT a.id, 3
          FROM appointments a, q
         WHERE f_unaccent(lower(coalesce(a.email, ''))) LIKE '%' || q.qn || '%'

        UNION ALL
        -- joined contact display_name (tier 2)
        SELECT a.id, 2
          FROM appointments a
          JOIN contacts c ON c.id = a.contact_id, q
         WHERE f_unaccent(lower(c.display_name)) LIKE '%' || q.qn || '%'

        UNION ALL
        -- joined event name (tier 3) — softer signal than direct
        -- appointment fields.
        SELECT a.id, 3
          FROM appointments a
          JOIN events e ON e.id = a.crm_event_id, q
         WHERE f_unaccent(lower(e.event_name)) LIKE '%' || q.qn || '%'
    ),
    ranked AS (
        SELECT id, MIN(tier) AS tier FROM candidates GROUP BY id
    )
    SELECT a.id,
           a.confirmation_code,
           a.celebrant_first_name,
           a.celebrant_last_name,
           a.parent_first_name,
           a.parent_last_name,
           a.slot_start_at,
           a.status,
           a.contact_id,
           a.assigned_user_id,
           a.crm_event_id,
           r.tier
      FROM ranked r
      JOIN appointments a ON a.id = r.id
     ORDER BY r.tier ASC, a.slot_start_at DESC
     LIMIT :lim
    """
)


def _search_appointments(
    db: Session, *, qn: str, limit: int
) -> list[SalesSearchResult]:
    digits = _phone_digits(qn)
    code = _confirmation_code_form(qn)

    rows = db.execute(
        _APPOINTMENTS_SQL,
        {
            "q": qn.lower(),
            "q_digits": digits,
            "q_code": code,
            "lim": limit,
        },
    ).all()

    out: list[SalesSearchResult] = []
    for row in rows:
        celebrant = " ".join(
            p for p in (row.celebrant_first_name, row.celebrant_last_name) if p
        )
        parent = " ".join(
            p for p in (row.parent_first_name, row.parent_last_name) if p
        )
        label = celebrant or parent or f"Appointment {row.confirmation_code}"
        sublabel_parts: list[str] = []
        status = (row.status or "").replace("_", " ").title()
        if status:
            sublabel_parts.append(status)
        if parent and parent != label:
            sublabel_parts.append(f"Parent: {parent}")
        if row.slot_start_at:
            sublabel_parts.append(row.slot_start_at.strftime("%b %-d %Y"))
        sublabel_parts.append(row.confirmation_code)
        out.append(
            SalesSearchResult(
                type="appointment",
                id=int(row.id),
                label=label,
                sublabel=" · ".join(sublabel_parts),
                contact_id=int(row.contact_id) if row.contact_id is not None else None,
                assigned_user_id=(
                    int(row.assigned_user_id)
                    if row.assigned_user_id is not None
                    else None
                ),
                route=f"/appointments/{int(row.id)}",
            )
        )
    return out


# Contacts -------------------------------------------------------------------

_CONTACTS_SQL = sql_text(
    """
    WITH q AS (
        SELECT
            f_unaccent(lower(CAST(:q AS text))) AS qn,
            CAST(:q_digits AS text)            AS qd
    ),
    candidates AS (
        -- display_name: substring (tier 1)
        SELECT c.id, 1 AS tier
          FROM contacts c, q
         WHERE f_unaccent(lower(c.display_name)) LIKE '%' || q.qn || '%'

        UNION ALL
        -- first/last name
        SELECT c.id, 1
          FROM contacts c, q
         WHERE (
                f_unaccent(lower(coalesce(c.first_name, ''))) LIKE '%' || q.qn || '%'
             OR f_unaccent(lower(coalesce(c.last_name,  ''))) LIKE '%' || q.qn || '%'
           )

        UNION ALL
        -- email substring (tier 2)
        SELECT c.id, 2
          FROM contacts c, q
         WHERE c.email IS NOT NULL
           AND f_unaccent(lower(c.email)) LIKE '%' || q.qn || '%'

        UNION ALL
        -- phone digits substring (tier 2)
        SELECT c.id, 2
          FROM contacts c, q
         WHERE q.qd != ''
           AND c.phone_e164 IS NOT NULL
           AND c.phone_e164 LIKE '%' || q.qd || '%'
    ),
    ranked AS (
        SELECT id, MIN(tier) AS tier FROM candidates GROUP BY id
    ),
    -- The contact must have at least one appointment to be navigable
    -- from the sales portal. Pick the most-recent one as the result's
    -- target route.
    most_recent_appt AS (
        SELECT DISTINCT ON (a.contact_id)
               a.contact_id, a.id AS appointment_id, a.assigned_user_id
          FROM appointments a
         WHERE a.contact_id IN (SELECT id FROM ranked)
         ORDER BY a.contact_id, a.slot_start_at DESC
    )
    SELECT c.id,
           c.display_name,
           c.phone,
           c.phone_e164,
           c.email,
           mra.appointment_id,
           mra.assigned_user_id,
           r.tier
      FROM ranked r
      JOIN contacts c ON c.id = r.id
      JOIN most_recent_appt mra ON mra.contact_id = c.id
     ORDER BY r.tier ASC, c.updated_at DESC
     LIMIT :lim
    """
)


def _search_contacts(
    db: Session, *, qn: str, limit: int
) -> list[SalesSearchResult]:
    digits = _phone_digits(qn)
    rows = db.execute(
        _CONTACTS_SQL,
        {
            "q": qn.lower(),
            "q_digits": digits,
            "lim": limit,
        },
    ).all()

    out: list[SalesSearchResult] = []
    for row in rows:
        sublabel_parts: list[str] = []
        if row.phone:
            sublabel_parts.append(row.phone)
        elif row.phone_e164:
            sublabel_parts.append(row.phone_e164)
        if row.email:
            sublabel_parts.append(row.email)
        out.append(
            SalesSearchResult(
                type="contact",
                id=int(row.id),
                label=row.display_name,
                sublabel=" · ".join(sublabel_parts),
                contact_id=int(row.id),
                assigned_user_id=(
                    int(row.assigned_user_id)
                    if row.assigned_user_id is not None
                    else None
                ),
                route=f"/appointments/{int(row.appointment_id)}",
            )
        )
    return out


# Events ---------------------------------------------------------------------

_EVENTS_SQL = sql_text(
    """
    WITH q AS (
        SELECT f_unaccent(lower(CAST(:q AS text))) AS qn
    ),
    candidates AS (
        SELECT e.id, 1 AS tier
          FROM events e, q
         WHERE f_unaccent(lower(e.event_name)) LIKE '%' || q.qn || '%'

        UNION ALL
        SELECT e.id, 2
          FROM events e, q
         WHERE e.quince_theme IS NOT NULL
           AND f_unaccent(lower(e.quince_theme)) LIKE '%' || q.qn || '%'
    ),
    ranked AS (
        SELECT id, MIN(tier) AS tier FROM candidates GROUP BY id
    ),
    most_recent_appt AS (
        SELECT DISTINCT ON (a.crm_event_id)
               a.crm_event_id, a.id AS appointment_id
          FROM appointments a
         WHERE a.crm_event_id IN (SELECT id FROM ranked)
         ORDER BY a.crm_event_id, a.slot_start_at DESC
    )
    SELECT e.id,
           e.event_name,
           e.status,
           e.event_date,
           e.primary_contact_id,
           e.owner_user_id,
           c.display_name AS contact_display_name,
           mra.appointment_id,
           r.tier
      FROM ranked r
      JOIN events e   ON e.id = r.id
      JOIN contacts c ON c.id = e.primary_contact_id
      JOIN most_recent_appt mra ON mra.crm_event_id = e.id
     ORDER BY r.tier ASC, e.updated_at DESC
     LIMIT :lim
    """
)


def _search_events(
    db: Session, *, qn: str, limit: int
) -> list[SalesSearchResult]:
    rows = db.execute(
        _EVENTS_SQL,
        {"q": qn.lower(), "lim": limit},
    ).all()

    out: list[SalesSearchResult] = []
    for row in rows:
        sublabel_parts: list[str] = []
        status = (row.status or "").replace("_", " ").title()
        if status:
            sublabel_parts.append(status)
        if row.contact_display_name:
            sublabel_parts.append(row.contact_display_name)
        if isinstance(row.event_date, date_type):
            sublabel_parts.append(row.event_date.strftime("%b %-d %Y"))
        out.append(
            SalesSearchResult(
                type="event",
                id=int(row.id),
                label=row.event_name,
                sublabel=" · ".join(sublabel_parts),
                contact_id=(
                    int(row.primary_contact_id)
                    if row.primary_contact_id is not None
                    else None
                ),
                assigned_user_id=(
                    int(row.owner_user_id)
                    if row.owner_user_id is not None
                    else None
                ),
                route=f"/appointments/{int(row.appointment_id)}",
            )
        )
    return out
