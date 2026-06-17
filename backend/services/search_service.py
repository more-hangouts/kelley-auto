"""Global search service.

Powers the GET /api/search endpoint behind the command-palette UI.
Phase 1 covers events + contacts. Phase 4 extends the same endpoint to
invoices and quotes. Special orders stay indexed but disabled until a
staff UI exists, because search results must route to a page that can
actually render the thing staff selected.

Ranking is tiered (exact > prefix > substring > trigram-fuzzy), tied
together with `updated_at DESC`. Accent and case folding happen in
SQL via the `f_unaccent` IMMUTABLE wrapper introduced in migration
045 so the runtime query expression matches the GIN index expression
exactly. If those expressions ever diverge the planner silently
sequential-scans, so all SQL goes through the helpers in this module
to keep the two sides in lock-step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session


# Public types and constants -------------------------------------------------

ALLOWED_TYPES: frozenset[str] = frozenset(
    {"event", "contact", "invoice", "quote"}
)
"""Searchable entity discriminators in v1."""

MIN_QUERY_LENGTH = 2
DEFAULT_LIMIT = 8
MAX_LIMIT = 20

# Threshold for the trigram-fuzzy tier. pg_trgm's default is 0.3 and
# that lines up with what we want: "Hernandes" matches "Hernández"
# but "Lopez" does not match "Lorena".
_TRIGRAM_THRESHOLD = 0.3


class SearchServiceError(Exception):
    """Domain-level rejection. Surfaced as 4xx by the router."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SearchResult:
    type: str
    id: int
    label: str
    sublabel: str
    score: float
    route: str


# Query-shape preprocessing --------------------------------------------------

_PHONE_QUERY_RE = re.compile(r"^[\d\s\-\(\)\+]+$")


def _normalize(q: str) -> str:
    """Trim only. Accent + case folding lives in SQL on both sides
    of the comparison so the bind parameter and the index expression
    cannot drift. Doing it Python-side would risk Python's case map
    and Postgres's `lower()` disagreeing on edge characters."""
    return q.strip()


def _phone_digits(q: str) -> str:
    """Digits-only form for phone-shaped queries. Empty string when
    the query has no phone shape; callers gate on that."""
    if not _PHONE_QUERY_RE.match(q):
        return ""
    digits = re.sub(r"\D", "", q)
    return digits


def _email_local_part(q: str) -> str:
    """Substring before the first ``@`` when the query is email-
    shaped. Empty string otherwise. The point of this is to let
    "lor@nonsense" still find Lorena's email by the local part even
    when the typed domain is wrong; the full-string substring branch
    catches the inverse ("@example.com" finds the domain)."""
    if "@" not in q:
        return ""
    return q.split("@", 1)[0]


# Public API -----------------------------------------------------------------


def search(
    db: Session,
    *,
    q: str,
    types: frozenset[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[SearchResult]:
    """Run the global search. Returns ranked, capped results across
    the requested entity types.

    `q` shorter than `MIN_QUERY_LENGTH` (after trim) returns []; the
    router gates on this too but the service short-circuits as a
    defense-in-depth so any future caller skips the DB roundtrip.
    """
    qn = _normalize(q)
    if len(qn) < MIN_QUERY_LENGTH:
        return []

    selected = types if types is not None else ALLOWED_TYPES
    unknown = selected - ALLOWED_TYPES
    if unknown:
        raise SearchServiceError(
            f"unknown types: {sorted(unknown)}",
            code="unknown_types",
        )

    cap = max(1, min(int(limit), MAX_LIMIT))

    results: list[SearchResult] = []
    if "contact" in selected:
        results.extend(_search_contacts(db, qn=qn, limit=cap))
    if "event" in selected:
        results.extend(_search_events(db, qn=qn, limit=cap))
    if "invoice" in selected:
        results.extend(_search_invoices(db, qn=qn, limit=cap))
    if "quote" in selected:
        results.extend(_search_quotes(db, qn=qn, limit=cap))
    return results


# Per-entity queries ---------------------------------------------------------


def _score(tier: int, sim: float) -> float:
    """Higher is better. tier 0 with sim 1.0 -> 11.0; tier 3 with sim
    0.3 -> 7.3. Reserved for future cross-type interleave; v1 results
    are already grouped by type."""
    return float(10 - tier) + float(sim)


# Both entity queries follow the same shape:
#
#   WITH q AS (SELECT f_unaccent(lower(:q)) AS qn, ...)
#   <UNION ALL of per-tier candidate selects>
#   <GROUP BY id taking MIN tier, MAX sim>
#   <JOIN back to the entity table>
#   ORDER BY tier ASC, sim DESC, updated_at DESC
#   LIMIT :lim
#
# Per-tier candidate branches are deliberately small so each one
# either uses the GIN expression index (substring/fuzzy) or a btree
# (exact phone). Tier 0 (exact) and tier 1 (prefix) branches do not
# overlap with tier 2 (substring) at the WHERE level, but the GROUP
# BY collapses any duplicate that does slip through.

_CONTACTS_SQL = sql_text(
    """
    WITH q AS (
        SELECT
            f_unaccent(lower(CAST(:q AS text)))             AS qn,
            CAST(:q_digits AS text)                         AS qd,
            CAST(:q_phone_e164 AS text)                     AS qp,
            f_unaccent(lower(CAST(:q_email_local AS text))) AS qel
    ),
    candidates AS (
        -- display_name: exact (tier 0)
        SELECT c.id AS id, 0 AS tier, 1.0::float AS sim
          FROM contacts c, q
         WHERE f_unaccent(lower(c.display_name)) = q.qn

        UNION ALL
        -- display_name: prefix (tier 1)
        SELECT c.id, 1, 0.9::float
          FROM contacts c, q
         WHERE f_unaccent(lower(c.display_name)) LIKE q.qn || '%'

        UNION ALL
        -- display_name: substring (tier 2)
        SELECT c.id, 2,
               similarity(f_unaccent(lower(c.display_name)), q.qn)
          FROM contacts c, q
         WHERE f_unaccent(lower(c.display_name)) LIKE '%' || q.qn || '%'

        UNION ALL
        -- display_name: trigram fuzzy (tier 3); only meaningful for
        -- queries long enough to form trigrams.
        SELECT c.id, 3,
               similarity(f_unaccent(lower(c.display_name)), q.qn)
          FROM contacts c, q
         WHERE length(q.qn) >= 3
           AND f_unaccent(lower(c.display_name)) % q.qn
           AND similarity(f_unaccent(lower(c.display_name)), q.qn) > :sim_threshold

        UNION ALL
        -- email: full-string substring (tier 2). Only when q is a
        -- "real word" not just digits, otherwise digit-typing
        -- customers would match every email containing those digits.
        SELECT c.id, 2,
               similarity(f_unaccent(lower(c.email)), q.qn)
          FROM contacts c, q
         WHERE c.email IS NOT NULL
           AND q.qd = ''
           AND f_unaccent(lower(c.email)) LIKE '%' || q.qn || '%'

        UNION ALL
        -- email: local-part substring (tier 2). When the query
        -- contains '@' we also match the substring before it
        -- against the email column. This is the documented
        -- preprocessing branch: "lor@wrongdomain" still finds the
        -- contact via the local part "lor". When q has no '@' the
        -- local part is empty and this branch contributes nothing.
        SELECT c.id, 2,
               similarity(f_unaccent(lower(c.email)), q.qel)
          FROM contacts c, q
         WHERE c.email IS NOT NULL
           AND q.qel != ''
           AND f_unaccent(lower(c.email)) LIKE '%' || q.qel || '%'

        UNION ALL
        -- phone: exact E.164 match (tier 0). Only when query is
        -- phone-shaped and normalizes to a usable E.164.
        SELECT c.id, 0, 1.0::float
          FROM contacts c, q
         WHERE q.qp != ''
           AND c.phone_e164 = q.qp

        UNION ALL
        -- phone: substring on phone_e164 digits (tier 2). Use the
        -- raw digit fragment rather than the E.164 normalization so
        -- a 4-digit "last four" lookup ("4567") works.
        SELECT c.id, 2, 0.5::float
          FROM contacts c, q
         WHERE q.qd != ''
           AND c.phone_e164 IS NOT NULL
           AND c.phone_e164 LIKE '%' || q.qd || '%'
    ),
    ranked AS (
        SELECT id, MIN(tier) AS tier, MAX(sim) AS sim
          FROM candidates
         GROUP BY id
    )
    SELECT c.id,
           c.display_name,
           c.email,
           c.phone,
           c.phone_e164,
           r.tier,
           r.sim
      FROM ranked r
      JOIN contacts c ON c.id = r.id
     ORDER BY r.tier ASC, r.sim DESC, c.updated_at DESC
     LIMIT :lim
    """
)


def _search_contacts(
    db: Session, *, qn: str, limit: int
) -> list[SearchResult]:
    digits = _phone_digits(qn)
    # E.164 mint mirrors booking_service.normalize_phone_e164 but
    # only the 10-digit US case so the search service does not pull
    # in the booking module.
    phone_e164 = ""
    if len(digits) == 10:
        phone_e164 = f"+1{digits}"
    elif len(digits) == 11 and digits.startswith("1"):
        phone_e164 = f"+{digits}"

    rows = db.execute(
        _CONTACTS_SQL,
        {
            "q": qn,
            "q_digits": digits,
            "q_phone_e164": phone_e164,
            "q_email_local": _email_local_part(qn),
            "sim_threshold": _TRIGRAM_THRESHOLD,
            "lim": limit,
        },
    ).all()

    out: list[SearchResult] = []
    for row in rows:
        out.append(
            SearchResult(
                type="contact",
                id=int(row.id),
                label=row.display_name,
                sublabel=_contact_sublabel(row),
                score=_score(int(row.tier), float(row.sim or 0.0)),
                route=f"/contacts/{int(row.id)}",
            )
        )
    return out


def _contact_sublabel(row: Any) -> str:
    parts: list[str] = []
    if row.phone:
        parts.append(row.phone)
    elif row.phone_e164:
        parts.append(row.phone_e164)
    if row.email:
        parts.append(row.email)
    return " · ".join(parts)


_EVENTS_SQL = sql_text(
    """
    WITH q AS (
        SELECT f_unaccent(lower(CAST(:q AS text))) AS qn
    ),
    candidates AS (
        -- event_name: exact (tier 0)
        SELECT e.id AS id, 0 AS tier, 1.0::float AS sim
          FROM events e, q
         WHERE f_unaccent(lower(e.event_name)) = q.qn

        UNION ALL
        -- event_name: prefix (tier 1)
        SELECT e.id, 1, 0.9::float
          FROM events e, q
         WHERE f_unaccent(lower(e.event_name)) LIKE q.qn || '%'

        UNION ALL
        -- event_name: substring (tier 2)
        SELECT e.id, 2,
               similarity(f_unaccent(lower(e.event_name)), q.qn)
          FROM events e, q
         WHERE f_unaccent(lower(e.event_name)) LIKE '%' || q.qn || '%'

        UNION ALL
        -- event_name: trigram fuzzy (tier 3)
        SELECT e.id, 3,
               similarity(f_unaccent(lower(e.event_name)), q.qn)
          FROM events e, q
         WHERE length(q.qn) >= 3
           AND f_unaccent(lower(e.event_name)) % q.qn
           AND similarity(f_unaccent(lower(e.event_name)), q.qn) > :sim_threshold

        UNION ALL
        -- quince_theme: substring (tier 2). Themes are short
        -- ("Floral", "Rose Gold") so prefix and substring collapse
        -- to one branch in practice.
        SELECT e.id, 2,
               similarity(f_unaccent(lower(e.quince_theme)), q.qn)
          FROM events e, q
         WHERE e.quince_theme IS NOT NULL
           AND f_unaccent(lower(e.quince_theme)) LIKE '%' || q.qn || '%'
    ),
    ranked AS (
        SELECT id, MIN(tier) AS tier, MAX(sim) AS sim
          FROM candidates
         GROUP BY id
    )
    SELECT e.id,
           e.event_name,
           e.event_date,
           e.status,
           e.quince_theme,
           c.display_name AS contact_display_name,
           r.tier,
           r.sim
      FROM ranked r
      JOIN events e   ON e.id = r.id
      JOIN contacts c ON c.id = e.primary_contact_id
     ORDER BY r.tier ASC, r.sim DESC, e.updated_at DESC
     LIMIT :lim
    """
)


def _search_events(
    db: Session, *, qn: str, limit: int
) -> list[SearchResult]:
    rows = db.execute(
        _EVENTS_SQL,
        {
            "q": qn,
            "sim_threshold": _TRIGRAM_THRESHOLD,
            "lim": limit,
        },
    ).all()

    out: list[SearchResult] = []
    for row in rows:
        out.append(
            SearchResult(
                type="event",
                id=int(row.id),
                label=row.event_name,
                sublabel=_event_sublabel(row),
                score=_score(int(row.tier), float(row.sim or 0.0)),
                route=f"/events/{int(row.id)}",
            )
        )
    return out


def _event_sublabel(row: Any) -> str:
    # Status first because that is the strongest disambiguator when
    # two events share a name. Theme is appended only when present.
    parts: list[str] = []
    status = (row.status or "").replace("_", " ").title()
    if status:
        parts.append(status)
    if row.contact_display_name:
        parts.append(row.contact_display_name)
    if row.event_date:
        parts.append(row.event_date.strftime("%b %-d %Y"))
    if row.quince_theme:
        parts.append(row.quince_theme)
    return " · ".join(parts)


_INVOICES_SQL = sql_text(
    """
    WITH q AS (
        SELECT
            lower(CAST(:q AS text))              AS qn,
            f_unaccent(lower(CAST(:q AS text))) AS qn_name
    ),
    candidates AS (
        -- invoice_number: exact (tier 0)
        SELECT i.id AS id, 0 AS tier, 1.0::float AS sim
          FROM invoices i, q
         WHERE i.deleted_at IS NULL
           AND i.invoice_number IS NOT NULL
           AND lower(i.invoice_number) = q.qn

        UNION ALL
        -- invoice_number: prefix (tier 1)
        SELECT i.id, 1, 0.9::float
          FROM invoices i, q
         WHERE i.deleted_at IS NULL
           AND i.invoice_number IS NOT NULL
           AND lower(i.invoice_number) LIKE q.qn || '%'

        UNION ALL
        -- invoice_number: substring (tier 2)
        SELECT i.id, 2, similarity(lower(i.invoice_number), q.qn)
          FROM invoices i, q
         WHERE i.deleted_at IS NULL
           AND i.invoice_number IS NOT NULL
           AND lower(i.invoice_number) LIKE '%' || q.qn || '%'

        UNION ALL
        -- invoice_number: trigram fuzzy (tier 3)
        SELECT i.id, 3, similarity(lower(i.invoice_number), q.qn)
          FROM invoices i, q
         WHERE i.deleted_at IS NULL
           AND i.invoice_number IS NOT NULL
           AND length(q.qn) >= 3
           AND lower(i.invoice_number) % q.qn
           AND similarity(lower(i.invoice_number), q.qn) > :sim_threshold

        UNION ALL
        -- joined event/contact names let "Hernandez invoice" style
        -- searches find issued documents without exposing route logic
        -- to the frontend.
        SELECT i.id, 2,
               greatest(
                   similarity(f_unaccent(lower(e.event_name)), q.qn_name),
                   similarity(f_unaccent(lower(c.display_name)), q.qn_name)
               )
          FROM invoices i
          JOIN events e ON e.id = i.event_id
          JOIN contacts c ON c.id = i.contact_id
          JOIN q ON true
         WHERE i.deleted_at IS NULL
           AND (
               f_unaccent(lower(e.event_name)) LIKE '%' || q.qn_name || '%'
               OR f_unaccent(lower(c.display_name)) LIKE '%' || q.qn_name || '%'
           )

        UNION ALL
        SELECT i.id, 3,
               greatest(
                   similarity(f_unaccent(lower(e.event_name)), q.qn_name),
                   similarity(f_unaccent(lower(c.display_name)), q.qn_name)
               )
          FROM invoices i
          JOIN events e ON e.id = i.event_id
          JOIN contacts c ON c.id = i.contact_id
          JOIN q ON true
         WHERE i.deleted_at IS NULL
           AND length(q.qn_name) >= 3
           AND (
               f_unaccent(lower(e.event_name)) % q.qn_name
               OR f_unaccent(lower(c.display_name)) % q.qn_name
           )
    ),
    ranked AS (
        SELECT id, MIN(tier) AS tier, MAX(sim) AS sim
          FROM candidates
         GROUP BY id
    )
    SELECT i.id,
           i.event_id,
           i.invoice_number,
           i.status,
           i.issue_date,
           i.due_date,
           i.total_cents,
           i.balance_cents,
           e.event_name,
           c.display_name AS contact_display_name,
           r.tier,
           r.sim
      FROM ranked r
      JOIN invoices i ON i.id = r.id
      JOIN events e   ON e.id = i.event_id
      JOIN contacts c ON c.id = i.contact_id
     ORDER BY r.tier ASC, r.sim DESC, i.updated_at DESC
     LIMIT :lim
    """
)


def _search_invoices(
    db: Session, *, qn: str, limit: int
) -> list[SearchResult]:
    rows = db.execute(
        _INVOICES_SQL,
        {
            "q": qn,
            "sim_threshold": _TRIGRAM_THRESHOLD,
            "lim": limit,
        },
    ).all()

    out: list[SearchResult] = []
    for row in rows:
        number = row.invoice_number or f"Draft invoice #{int(row.id)}"
        out.append(
            SearchResult(
                type="invoice",
                id=int(row.id),
                label=number,
                sublabel=_invoice_sublabel(row),
                score=_score(int(row.tier), float(row.sim or 0.0)),
                route=f"/events/{int(row.event_id)}/invoices",
            )
        )
    return out


def _invoice_sublabel(row: Any) -> str:
    parts: list[str] = []
    status = (row.status or "").replace("_", " ").title()
    if status:
        parts.append(status)
    if row.event_name:
        parts.append(row.event_name)
    if row.contact_display_name:
        parts.append(row.contact_display_name)
    if row.balance_cents is not None:
        parts.append(f"Balance ${int(row.balance_cents) / 100:,.2f}")
    return " · ".join(parts)


_QUOTES_SQL = sql_text(
    """
    WITH q AS (
        SELECT
            lower(CAST(:q AS text))              AS qn,
            f_unaccent(lower(CAST(:q AS text))) AS qn_name
    ),
    candidates AS (
        -- quote_number: exact (tier 0)
        SELECT qt.id AS id, 0 AS tier, 1.0::float AS sim
          FROM quotes qt, q
         WHERE qt.deleted_at IS NULL
           AND qt.quote_number IS NOT NULL
           AND lower(qt.quote_number) = q.qn

        UNION ALL
        -- quote_number: prefix (tier 1)
        SELECT qt.id, 1, 0.9::float
          FROM quotes qt, q
         WHERE qt.deleted_at IS NULL
           AND qt.quote_number IS NOT NULL
           AND lower(qt.quote_number) LIKE q.qn || '%'

        UNION ALL
        -- quote_number: substring (tier 2)
        SELECT qt.id, 2, similarity(lower(qt.quote_number), q.qn)
          FROM quotes qt, q
         WHERE qt.deleted_at IS NULL
           AND qt.quote_number IS NOT NULL
           AND lower(qt.quote_number) LIKE '%' || q.qn || '%'

        UNION ALL
        -- quote_number: trigram fuzzy (tier 3)
        SELECT qt.id, 3, similarity(lower(qt.quote_number), q.qn)
          FROM quotes qt, q
         WHERE qt.deleted_at IS NULL
           AND qt.quote_number IS NOT NULL
           AND length(q.qn) >= 3
           AND lower(qt.quote_number) % q.qn
           AND similarity(lower(qt.quote_number), q.qn) > :sim_threshold

        UNION ALL
        -- joined event/contact names.
        SELECT qt.id, 2,
               greatest(
                   similarity(f_unaccent(lower(e.event_name)), q.qn_name),
                   similarity(f_unaccent(lower(c.display_name)), q.qn_name)
               )
          FROM quotes qt
          JOIN events e ON e.id = qt.event_id
          JOIN contacts c ON c.id = qt.contact_id
          JOIN q ON true
         WHERE qt.deleted_at IS NULL
           AND (
               f_unaccent(lower(e.event_name)) LIKE '%' || q.qn_name || '%'
               OR f_unaccent(lower(c.display_name)) LIKE '%' || q.qn_name || '%'
           )

        UNION ALL
        SELECT qt.id, 3,
               greatest(
                   similarity(f_unaccent(lower(e.event_name)), q.qn_name),
                   similarity(f_unaccent(lower(c.display_name)), q.qn_name)
               )
          FROM quotes qt
          JOIN events e ON e.id = qt.event_id
          JOIN contacts c ON c.id = qt.contact_id
          JOIN q ON true
         WHERE qt.deleted_at IS NULL
           AND length(q.qn_name) >= 3
           AND (
               f_unaccent(lower(e.event_name)) % q.qn_name
               OR f_unaccent(lower(c.display_name)) % q.qn_name
           )
    ),
    ranked AS (
        SELECT id, MIN(tier) AS tier, MAX(sim) AS sim
          FROM candidates
         GROUP BY id
    )
    SELECT qt.id,
           qt.event_id,
           qt.quote_number,
           qt.status,
           qt.issue_date,
           qt.expires_at,
           qt.total_cents,
           e.event_name,
           c.display_name AS contact_display_name,
           r.tier,
           r.sim
      FROM ranked r
      JOIN quotes qt ON qt.id = r.id
      JOIN events e  ON e.id = qt.event_id
      JOIN contacts c ON c.id = qt.contact_id
     ORDER BY r.tier ASC, r.sim DESC, qt.updated_at DESC
     LIMIT :lim
    """
)


def _search_quotes(
    db: Session, *, qn: str, limit: int
) -> list[SearchResult]:
    rows = db.execute(
        _QUOTES_SQL,
        {
            "q": qn,
            "sim_threshold": _TRIGRAM_THRESHOLD,
            "lim": limit,
        },
    ).all()

    out: list[SearchResult] = []
    for row in rows:
        number = row.quote_number or f"Draft quote #{int(row.id)}"
        out.append(
            SearchResult(
                type="quote",
                id=int(row.id),
                label=number,
                sublabel=_quote_sublabel(row),
                score=_score(int(row.tier), float(row.sim or 0.0)),
                route=f"/events/{int(row.event_id)}/quotes",
            )
        )
    return out


def _quote_sublabel(row: Any) -> str:
    parts: list[str] = []
    status = (row.status or "").replace("_", " ").title()
    if status:
        parts.append(status)
    if row.event_name:
        parts.append(row.event_name)
    if row.contact_display_name:
        parts.append(row.contact_display_name)
    if row.total_cents is not None:
        parts.append(f"Total ${int(row.total_cents) / 100:,.2f}")
    return " · ".join(parts)
