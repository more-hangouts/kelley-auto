"""Smoke tests for grouping a lead's raw browsing stream into visits.

The Lead Journey panel used to render every beacon hit, so a shopper who
came back six times over three weeks read as "138 events" — which looks
like we tailed them around the internet all day. This proves the grouping
that turns that stream back into the handful of times someone actually
visited:

  - events inside the 30-minute window are ONE visit; a longer gap starts
    a new one,
  - the duplicate pair the beacon emits for a single listing open
    (page_view + vehicle_view on the same car) is listed once per visit,
  - the visit containing lead_submitted is the one marked converted,
  - top_interests ranks by DISTINCT VISITS, so opening a car twice in one
    sitting doesn't outrank returning to another car on three days,
  - a lead with no tracking at all still answers cleanly.

Run as a script (matches the repo convention):
    .venv/bin/python tests/test_lead_journey_visits_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from sqlalchemy import text as sql_text  # noqa: E402

from database.connection import SessionLocal  # noqa: E402
from modules.analytics.services.storefront_analytics_service import (  # noqa: E402
    get_lead_journey,
)

_TAG = uuid.uuid4().hex[:8]
_BASE = datetime(2026, 7, 17, 12, 40, tzinfo=timezone.utc)


def _assert(cond: bool, label: str, detail: object = "") -> None:
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _seed_visitor() -> tuple[int, int]:
    """Returns (visitor_id, session_id)."""
    db = SessionLocal()
    try:
        vid = db.execute(
            sql_text(
                "INSERT INTO storefront_visitors "
                "(visitor_key, first_seen_at, last_seen_at, "
                " first_touch_attribution, last_touch_attribution) "
                "VALUES (:k, :t, :t, '{}'::jsonb, '{}'::jsonb) RETURNING id"
            ),
            {"k": f"journey-smoke-{_TAG}", "t": _BASE},
        ).scalar()
        sid = db.execute(
            sql_text(
                "INSERT INTO storefront_sessions "
                "(visitor_id, session_key, started_at, last_seen_at, initial_utm) "
                "VALUES (:v, :k, :t, :t, '{}'::jsonb) RETURNING id"
            ),
            {"v": vid, "k": f"journey-sess-{_TAG}", "t": _BASE},
        ).scalar()
        db.commit()
        return int(vid), int(sid)
    finally:
        db.close()


def _seed_event(
    visitor_id: int,
    session_id: int,
    name: str,
    minutes: int,
    *,
    vehicle_meta: tuple[int, str, str] | None = None,
) -> None:
    meta = "{}"
    if vehicle_meta:
        year, make, model = vehicle_meta
        meta = (
            f'{{"vehicle_year": {year}, "vehicle_make": "{make}", '
            f'"vehicle_model": "{model}"}}'
        )
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "INSERT INTO storefront_events "
                "(visitor_id, session_id, event_name, path, utm, metadata, occurred_at) "
                "VALUES (:v, :s, :n, :p, '{}'::jsonb, CAST(:m AS jsonb), :t)"
            ),
            {
                "v": visitor_id,
                "s": session_id,
                "n": name,
                "p": "/inventory/TEST",
                "m": meta,
                "t": _BASE + timedelta(minutes=minutes),
            },
        )
        db.commit()
    finally:
        db.close()


def _seed_deal_with_attribution(visitor_id: int, session_id: int) -> tuple[int, int]:
    db = SessionLocal()
    try:
        cid = db.execute(
            sql_text(
                "INSERT INTO contacts (display_name, tags) "
                "VALUES ('Journey Smoke Shopper', '[\"journey-smoke\"]'::jsonb) "
                "RETURNING id"
            )
        ).scalar()
        eid = db.execute(
            sql_text(
                "INSERT INTO events "
                "(primary_contact_id, event_type, event_name, status) "
                "VALUES (:c, 'vehicle_sale', 'Journey Smoke Deal', 'new_lead') "
                "RETURNING id"
            ),
            {"c": cid},
        ).scalar()
        db.execute(
            sql_text(
                "INSERT INTO lead_attribution "
                "(event_id, visitor_id, session_id, source_page) "
                "VALUES (:e, :v, :s, '/contact-us')"
            ),
            {"e": eid, "v": visitor_id, "s": session_id},
        )
        db.commit()
        return int(cid), int(eid)
    finally:
        db.close()


def _cleanup(contact_ids: list[int], visitor_ids: list[int]) -> None:
    db = SessionLocal()
    try:
        if contact_ids:
            db.execute(
                sql_text(
                    "DELETE FROM lead_attribution WHERE event_id IN "
                    "(SELECT id FROM events WHERE primary_contact_id = ANY(:ids))"
                ),
                {"ids": contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE primary_contact_id = ANY(:ids)"),
                {"ids": contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": contact_ids},
            )
        if visitor_ids:
            db.execute(
                sql_text("DELETE FROM storefront_events WHERE visitor_id = ANY(:ids)"),
                {"ids": visitor_ids},
            )
            db.execute(
                sql_text("DELETE FROM storefront_sessions WHERE visitor_id = ANY(:ids)"),
                {"ids": visitor_ids},
            )
            db.execute(
                sql_text("DELETE FROM storefront_visitors WHERE id = ANY(:ids)"),
                {"ids": visitor_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> int:
    contact_ids: list[int] = []
    visitor_ids: list[int] = []
    try:
        visitor_id, session_id = _seed_visitor()
        visitor_ids.append(visitor_id)

        CAMARO = (2010, "Chevrolet", "Camaro")
        CHALLENGER = (2012, "Dodge", "Challenger")

        # --- Visit 1: opens the Camaro listing (the beacon emits BOTH a
        #     page_view and a vehicle_view for that single act), then the
        #     Challenger 20 minutes later — still the same sitting.
        _seed_event(visitor_id, session_id, "page_view", 0, vehicle_meta=CAMARO)
        _seed_event(visitor_id, session_id, "vehicle_view", 0, vehicle_meta=CAMARO)
        _seed_event(visitor_id, session_id, "page_view", 20, vehicle_meta=CHALLENGER)
        _seed_event(visitor_id, session_id, "vehicle_view", 20, vehicle_meta=CHALLENGER)

        # --- Visit 2 (next day): back to the Camaro, then converts.
        _seed_event(visitor_id, session_id, "vehicle_view", 1440, vehicle_meta=CAMARO)
        _seed_event(visitor_id, session_id, "lead_submitted", 1445)

        _cid, deal_id = _seed_deal_with_attribution(visitor_id, session_id)
        contact_ids.append(_cid)

        db = SessionLocal()
        try:
            journey = get_lead_journey(db, crm_event_id=deal_id)
        finally:
            db.close()

        _assert(journey["has_attribution"] is True, "has attribution", journey)
        visits = journey["visits"]
        _assert(len(visits) == 2, "two visits, not six events", visits)
        print("visit grouping ok")

        # --- the duplicate pair collapses ---------------------------------
        first = visits[0]
        _assert(
            first["vehicles"] == ["2010 Chevrolet Camaro", "2012 Dodge Challenger"],
            "each car listed once per visit",
            first["vehicles"],
        )
        _assert(first["event_count"] == 4, "raw count preserved", first)
        _assert(first["converted"] is False, "first visit did not convert", first)
        print("duplicate page_view/vehicle_view collapsed ok")

        # --- conversion lands on the right visit --------------------------
        second = visits[1]
        _assert(second["converted"] is True, "second visit converted", second)
        _assert(
            second["vehicles"] == ["2010 Chevrolet Camaro"],
            "second visit vehicles",
            second["vehicles"],
        )
        print("conversion marked on the right visit ok")

        # --- interest ranks by distinct visits ----------------------------
        interests = journey["top_interests"]
        _assert(interests, "top interests present", interests)
        _assert(
            interests[0]["label"] == "2010 Chevrolet Camaro",
            "the car they returned to ranks first",
            interests,
        )
        _assert(interests[0]["visits"] == 2, "counted by visit not view", interests)
        print("interest ranking ok")

        # --- the 30-minute boundary ---------------------------------------
        # 29 minutes after the last event is the same visit; 31 is a new one.
        _seed_event(visitor_id, session_id, "page_view", 1445 + 29)
        db = SessionLocal()
        try:
            journey = get_lead_journey(db, crm_event_id=deal_id)
        finally:
            db.close()
        _assert(len(journey["visits"]) == 2, "29-minute gap stays one visit", journey["visits"])

        _seed_event(visitor_id, session_id, "page_view", 1445 + 29 + 31)
        db = SessionLocal()
        try:
            journey = get_lead_journey(db, crm_event_id=deal_id)
        finally:
            db.close()
        _assert(len(journey["visits"]) == 3, "31-minute gap starts a visit", journey["visits"])
        print("visit gap boundary ok")

        # --- first_seen is the first touch, not the conversion ------------
        _assert(
            journey["first_seen_at"].startswith("2026-07-17"),
            "first seen is the first touch",
            journey["first_seen_at"],
        )
        print("first seen ok")

        # --- a deal with no tracking at all --------------------------------
        db = SessionLocal()
        try:
            cid = db.execute(
                sql_text(
                    "INSERT INTO contacts (display_name, tags) "
                    "VALUES ('Phone-in Shopper', '[\"journey-smoke\"]'::jsonb) "
                    "RETURNING id"
                )
            ).scalar()
            untracked = db.execute(
                sql_text(
                    "INSERT INTO events "
                    "(primary_contact_id, event_type, event_name, status) "
                    "VALUES (:c, 'vehicle_sale', 'Phone-in', 'new_lead') RETURNING id"
                ),
                {"c": cid},
            ).scalar()
            db.commit()
            contact_ids.append(int(cid))
            plain = get_lead_journey(db, crm_event_id=int(untracked))
        finally:
            db.close()
        _assert(plain == {"has_attribution": False}, "untracked lead", plain)
        print("untracked lead ok")

        print()
        print("lead journey visits smoke ok")
        return 0
    finally:
        _cleanup(contact_ids, visitor_ids)
        print("cleanup done")


if __name__ == "__main__":
    sys.exit(main())
