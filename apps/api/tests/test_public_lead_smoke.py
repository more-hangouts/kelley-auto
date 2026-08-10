"""Smoke tests for public lead intake — POST /api/public/leads (Day 4).

Proves the bridge from public inventory browsing to the Day 3 deal pipeline:
  - unauthenticated submit creates a contact + a vehicle_sale deal in
    new_lead,
  - a listingCode (and a numeric vehicle id) links the vehicle,
  - a duplicate submit appends an activity to the existing open deal instead
    of opening a second one,
  - a ref to a sold/hidden/inactive/non-vehicle/unknown car degrades to a
    general (unlinked) lead — no crash, no leak,
  - missing phone+email is a 422,
  - a honeypot submission returns the generic ack and writes nothing,
  - the response is always the same generic ack (no IDs / existence hints),
  - the per-IP rate limit trips (soft-checked; skipped if Redis is flaky).

Run as a script (serial — shares numbering_state seq with catalog smokes):
    .venv/bin/python tests/test_public_lead_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from config.settings import APP_TIMEZONE  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8].upper()
_STOCK_PREFIX = f"LEADSTK-{_TAG}-"
_DRESS_SKU = f"LEADDRESS-{_TAG}"
_EMAIL_PREFIX = f"lead-{_TAG.lower()}-"
_PHONE_PREFIX = f"903{int(_TAG[:7], 16) % 10_000_000:07d}"
_ACK = {"ok": True, "message": "Thanks, we received your request."}


def _assert(cond: bool, label: str, detail: object = "") -> None:
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _email(who: str) -> str:
    return f"{_EMAIL_PREFIX}{who}@example.com"


def _phone(offset: int) -> str:
    return f"903{(int(_PHONE_PREFIX[3:]) + offset) % 10_000_000:07d}"


def _make_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"lead-{suffix}",
            email=f"lead-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Lead Smoke Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, u.email
    finally:
        db.close()


def _get_seq() -> int:
    db = SessionLocal()
    try:
        return int(
            db.execute(
                sql_text(
                    "SELECT catalog_public_code_seq FROM numbering_state WHERE id = 1"
                )
            ).scalar()
        )
    finally:
        db.close()


def _contact_id_by_email(email: str) -> int | None:
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT id FROM contacts WHERE lower(email) = :e "
                "AND deleted_at IS NULL"
            ),
            {"e": email.lower()},
        ).first()
        return int(row[0]) if row else None
    finally:
        db.close()


def _deals_for(contact_id: int) -> list[tuple[int, str, int | None]]:
    db = SessionLocal()
    try:
        return [
            (int(r[0]), r[1], r[2])
            for r in db.execute(
                sql_text(
                    "SELECT id, status, vehicle_catalog_item_id FROM events "
                    "WHERE primary_contact_id = :cid AND event_type = 'vehicle_sale' "
                    "AND deleted_at IS NULL ORDER BY id"
                ),
                {"cid": contact_id},
            ).all()
        ]
    finally:
        db.close()


def _lead_activity_count(event_id: int) -> int:
    db = SessionLocal()
    try:
        return int(
            db.execute(
                sql_text(
                    "SELECT COUNT(*) FROM activity_log WHERE event_id = :eid "
                    "AND activity_type = 'lead.public_submitted'"
                ),
                {"eid": event_id},
            ).scalar()
        )
    finally:
        db.close()


def _event_notes(event_id: int) -> str | None:
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text("SELECT notes FROM events WHERE id = :eid"),
            {"eid": event_id},
        ).first()
        return row[0] if row else None
    finally:
        db.close()


def _application_state_for(event_id: int) -> str | None:
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT driver_license_state FROM lead_applications "
                "WHERE event_id = :eid"
            ),
            {"eid": event_id},
        ).first()
        return row[0] if row else None
    finally:
        db.close()


def _application_has_dob(event_id: int) -> bool:
    """True when the application row exists AND actually holds an encrypted
    DOB. Guards the degrade path: dropping an unreadable state code must not
    take the rest of the credit application down with it."""
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT date_of_birth_ciphertext IS NOT NULL "
                "FROM lead_applications WHERE event_id = :eid"
            ),
            {"eid": event_id},
        ).first()
        return bool(row and row[0])
    finally:
        db.close()


def _appointment_for_event(event_id: int) -> tuple[str, int, str] | None:
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT status, slot_duration_minutes, internal_notes "
                "FROM appointments WHERE crm_event_id = :eid "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"eid": event_id},
        ).first()
        return (row[0], int(row[1]), row[2]) if row else None
    finally:
        db.close()


def _tomorrow_slot() -> tuple[str, int]:
    tomorrow = datetime.now(ZoneInfo(APP_TIMEZONE)).date() + timedelta(days=1)
    return tomorrow.isoformat(), 14


def _vehicle(suffix, *, status, price_cents=1_800_000):
    return {
        "is_vehicle": True,
        "stock_number": _STOCK_PREFIX + suffix,
        "make": "Subaru",
        "model": "Outback",
        "year": 2021,
        "mileage": 30000,
        "exterior_color": "Green",
        "vehicle_status": status,
        "unit_price_cents": price_cents,
    }


def _cleanup(user_id: int, baseline_seq: int) -> None:
    db = SessionLocal()
    try:
        # Find every contact this run created, drop their events (cascades
        # participants + status changes + activity_log), then the contacts.
        ids = [
            int(r[0])
            for r in db.execute(
                sql_text(
                    "SELECT id FROM contacts WHERE lower(email) LIKE :p"
                ),
                {"p": _EMAIL_PREFIX + "%"},
            ).all()
        ]
        if ids:
            event_ids = [
                int(r[0])
                for r in db.execute(
                    sql_text(
                        "SELECT id FROM events WHERE primary_contact_id = ANY(:ids)"
                    ),
                    {"ids": ids},
                ).all()
            ]
            if event_ids:
                db.execute(
                    sql_text(
                        "DELETE FROM appointments WHERE crm_event_id = ANY(:event_ids)"
                    ),
                    {"event_ids": event_ids},
                )
                # Queued Meta CAPI events for these test leads must not
                # survive — they'd deliver to the real dataset the moment
                # META_CAPI_ENABLED flips on.
                db.execute(
                    sql_text(
                        "DELETE FROM ad_conversion_events "
                        "WHERE lead_event_id = ANY(:event_ids)"
                    ),
                    {"event_ids": event_ids},
                )
            db.execute(
                sql_text("DELETE FROM events WHERE primary_contact_id = ANY(:ids)"),
                {"ids": ids},
            )
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"), {"ids": ids}
            )
        db.execute(
            sql_text(
                "DELETE FROM catalog_items "
                "WHERE internal_sku LIKE :stk OR internal_sku = :dress"
            ),
            {"stk": _STOCK_PREFIX + "%", "dress": _DRESS_SKU},
        )
        db.execute(sql_text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        db.execute(
            sql_text(
                "UPDATE numbering_state SET catalog_public_code_seq = :s WHERE id = 1"
            ),
            {"s": baseline_seq},
        )
        db.commit()
    finally:
        db.close()

    try:
        from api.redis_rate_limit import flush_for_testing

        flush_for_testing(["rl:public_lead_ip:*", "rl:public_lead_identifier:*"])
    except Exception:
        pass


def main() -> int:  # noqa: C901 - linear smoke script
    baseline_seq = _get_seq()
    admin_id, admin_email = _make_admin()
    try:
        resp = client.post(
            "/api/auth/login",
            json={"email": admin_email, "password": "smoke-pass-12345"},
        )
        _assert(resp.status_code == 200, "login", resp.text)
        auth = {"Authorization": f"Bearer {resp.json()['access_token']}"}

        # Capture staff-notification sends without real SMTP: swap the
        # service's mailer for a recorder. Recipients resolve to the seeded
        # business_profile.email or the active admin, so sends do fire.
        import modules.contacts.services.public_lead_service as _pls

        _notify_calls: list[dict] = []

        def _fake_send(*, to, rendered, scope="email"):
            if getattr(_fake_send, "boom", False):
                raise RuntimeError("smtp down (test)")
            _notify_calls.append({"to": to, "subject": rendered.subject})

        _pls.send_rendered_safely = _fake_send

        # seed cars + a dress (non-vehicle)
        codes: dict[str, str] = {}
        ids: dict[str, int] = {}
        for key, status in (
            ("avail", "available"),
            ("sold", "sold"),
            ("hidden", "hidden"),
        ):
            r = client.post("/api/catalog", headers=auth, json=_vehicle(key, status=status))
            _assert(r.status_code == 201, f"seed {key}", r.text)
            ids[key] = r.json()["id"]
            codes[key] = r.json()["public_code"]
        r = client.post(
            "/api/catalog",
            headers=auth,
            json={
                "internal_sku": _DRESS_SKU,
                "color": "Blush",
                "category": "quince_gown",
                "product_title": "Lead Boundary Gown",
            },
        )
        _assert(r.status_code == 201, "seed dress", r.text)
        dress_id = r.json()["id"]
        print("seeded inventory ok")

        # --- missing phone + email -> 422 --------------------------------
        r = client.post("/api/public/leads", json={"name": "No Channel", "message": "hi"})
        _assert(r.status_code == 422, "missing contact channel 422", r.text)
        print("missing phone+email rejected ok")

        # --- honeypot -> generic ack, no record --------------------------
        bot_email = _email("bot")
        r = client.post(
            "/api/public/leads",
            json={
                "name": "Bot",
                "email": bot_email,
                "listing_code": codes["avail"],
                "company_website": "http://spam.example",
            },
        )
        _assert(r.status_code == 200, "honeypot status", r.text)
        _assert(r.json() == _ACK, "honeypot generic ack", r.json())
        _assert(_contact_id_by_email(bot_email) is None, "honeypot wrote no contact")
        _assert(
            len(_notify_calls) == 0,
            "honeypot sends no staff notification",
            _notify_calls,
        )
        print("honeypot drops silently ok")

        # --- create + link by listingCode --------------------------------
        a_email = _email("a")
        r = client.post(
            "/api/public/leads",
            json={
                "name": "Carlos Ramirez",
                "email": a_email,
                "listing_code": codes["avail"],
                "message": "Is this still available?",
                "preferred_time": "2:00 PM tomorrow",
                "preferred_date": _tomorrow_slot()[0],
                "preferred_hour": _tomorrow_slot()[1],
                "source_page": "/inventory/" + codes["avail"],
                "utm_source": "google",
                "utm_campaign": "summer",
            },
        )
        _assert(r.status_code == 200, "create lead", r.text)
        _assert(r.json() == _ACK, "create generic ack", r.json())
        a_cid = _contact_id_by_email(a_email)
        _assert(a_cid is not None, "lead created contact")
        deals = _deals_for(a_cid)
        _assert(len(deals) == 1, "one deal created", deals)
        _assert(deals[0][1] == "new_lead", "deal in new_lead", deals)
        _assert(deals[0][2] == ids["avail"], "deal linked to vehicle", deals)
        _assert(_lead_activity_count(deals[0][0]) == 1, "one lead activity")
        _assert(
            _event_notes(deals[0][0]) == "Is this still available?",
            "notes keep only customer message",
            _event_notes(deals[0][0]),
        )
        _assert(
            _appointment_for_event(deals[0][0])
            == ("pending", 30, "Requested via storefront — call to confirm."),
            "preferred slot creates pending appointment",
            _appointment_for_event(deals[0][0]),
        )
        _assert(
            any(c["subject"].startswith("New vehicle lead") for c in _notify_calls),
            "new lead triggers 'New vehicle lead' notification",
            _notify_calls,
        )
        _new_count = len(_notify_calls)
        print("create + link by listingCode ok")

        # --- full state names normalize before max-length validation -----
        state_email = _email("state")
        r = client.post(
            "/api/public/leads",
            json={
                "name": "State Name",
                "email": state_email,
                "phone": _phone(1),
                "listing_code": codes["avail"],
                "date_of_birth": "09/29/1985",
                "driver_license_state": "TEXAS",
                "has_driver_license": True,
                "address_street": "123 Main St",
                "address_city": "Tyler",
                "address_state": "Texas",
                "address_zip": "75701",
            },
        )
        _assert(r.status_code == 200, "full state names accepted", r.text)
        state_deals = _deals_for(_contact_id_by_email(state_email))
        _assert(len(state_deals) == 1, "state lead created one deal", state_deals)
        _assert(
            _application_state_for(state_deals[0][0]) == "TX",
            "driver license state normalized",
            _application_state_for(state_deals[0][0]),
        )
        print("full state names normalize for BHPH application fields ok")

        # --- an unreadable state degrades, it never costs the lead --------
        # Regression: the loan form took the driver's-license state as free
        # text, so real submissions arrived as "TX DL" / "N/A" / "Tx." — all
        # longer than the 2-char column. Every one of them 422'd BEFORE any
        # row was written: no contact, no deal, no staff email, and a generic
        # "something went wrong" for the customer. 98 submissions were lost
        # this way between 2026-06-30 and 2026-08-10. A state code we cannot
        # read is worth less than the application it rides on, so it is
        # dropped and the lead still lands.
        junk_states = {
            "dlx1": ("TX DL", None),
            "dlx2": ("N/A", None),
            "dlx3": ("San Antonio, TX", None),
            "dlx4": ("12345678", None),
            # Recovered rather than dropped: punctuation/casing/spacing only.
            "dlx5": ("Tx.", "TX"),
            "dlx6": ("  texas  ", "TX"),
            "dlx7": ("tx", "TX"),
            "dlx8": ("new  york", "NY"),
        }
        for who, (typed, expected) in junk_states.items():
            em = _email(who)
            r = client.post(
                "/api/public/leads",
                json={
                    "name": "Free Text State",
                    "email": em,
                    "listing_code": codes["avail"],
                    "date_of_birth": "09/29/1985",
                    "driver_license_state": typed,
                    "has_driver_license": True,
                    "address_street": "123 Main St",
                    "address_state": typed,
                },
            )
            _assert(
                r.status_code == 200,
                f"state {typed!r} must not reject the application",
                r.text,
            )
            _assert(r.json() == _ACK, f"state {typed!r} generic ack", r.json())
            d = _deals_for(_contact_id_by_email(em))
            _assert(len(d) == 1, f"state {typed!r} created one deal", d)
            _assert(
                _application_state_for(d[0][0]) == expected,
                f"state {typed!r} -> {expected!r}",
                _application_state_for(d[0][0]),
            )
            # The application still has to be a real, readable row: dropping
            # an unusable state must not quietly drop the PII with it.
            _assert(
                _application_has_dob(d[0][0]),
                f"state {typed!r} still stored the encrypted DOB",
                typed,
            )
            # ...and none of it may leak into the deal's free-text notes.
            notes = _event_notes(d[0][0]) or ""
            for secret in ("09/29/1985", "1985", "123 Main St", typed):
                _assert(
                    secret not in notes,
                    f"state {typed!r}: {secret!r} must not reach event notes",
                    notes,
                )
        print("unreadable state degrades to null without losing the lead ok")

        # --- duplicate submit appends, no second deal --------------------
        r = client.post(
            "/api/public/leads",
            json={
                "name": "Carlos Ramirez",
                "email": a_email,
                "listing_code": codes["avail"],
                "message": "Following up!",
            },
        )
        _assert(r.status_code == 200, "duplicate status", r.text)
        deals = _deals_for(a_cid)
        _assert(len(deals) == 1, "still one deal after duplicate", deals)
        _assert(_lead_activity_count(deals[0][0]) == 2, "activity appended", deals)
        _assert(
            len(_notify_calls) > _new_count
            and _notify_calls[-1]["subject"].startswith("Updated vehicle lead"),
            "duplicate lead triggers 'Updated vehicle lead' notification",
            _notify_calls,
        )
        print("duplicate appends instead of new deal ok")

        # --- notification failure must NOT break the lead request --------
        _fake_send.boom = True
        rfail = client.post(
            "/api/public/leads",
            json={
                "name": "Mailer Down",
                "email": _email("fail"),
                "listing_code": codes["avail"],
                "message": "test",
            },
        )
        _assert(
            rfail.status_code == 200,
            "lead still succeeds when the mailer raises",
            rfail.text,
        )
        _fake_send.boom = False
        print("staff notification new/updated + honeypot-silent + mailer-safe ok")

        # --- link by numeric vehicle_id ----------------------------------
        b_email = _email("b")
        r = client.post(
            "/api/public/leads",
            json={"name": "Dana Lee", "email": b_email, "vehicle_id": ids["avail"]},
        )
        _assert(r.status_code == 200, "vehicle_id lead", r.text)
        b_deals = _deals_for(_contact_id_by_email(b_email))
        _assert(len(b_deals) == 1 and b_deals[0][2] == ids["avail"], "vehicle_id linked", b_deals)
        print("link by numeric vehicle_id ok")

        # --- non-linkable refs degrade to a general (unlinked) lead ------
        # sold (public detail but not for-sale), hidden, non-vehicle, unknown.
        cases = {
            "csold": {"vehicle_id": ids["sold"]},
            "chidden": {"listing_code": codes["hidden"]},
            "cdress": {"vehicle_id": dress_id},
            "cunknown": {"vehicle_id": 999_999_999},
        }
        for who, ref in cases.items():
            em = _email(who)
            r = client.post(
                "/api/public/leads",
                json={"name": who, "email": em, **ref, "message": "interested"},
            )
            _assert(r.status_code == 200, f"{who} lead status", r.text)
            _assert(r.json() == _ACK, f"{who} generic ack", r.json())
            d = _deals_for(_contact_id_by_email(em))
            _assert(len(d) == 1, f"{who} one general deal", d)
            _assert(d[0][2] is None, f"{who} unlinked (no stale link)", d)
        print("non-linkable refs degrade to general lead ok")

        # --- per-IP rate limit (soft; needs Redis) -----------------------
        # Honeypot path still runs the per-IP dep first, so we trip the IP
        # bucket without writing rows. limit=10/600 -> the 11th should 429.
        ip = f"198.51.100.{_TAG[0:2]}"
        try:
            from api.redis_rate_limit import flush_for_testing

            flush_for_testing([f"rl:public_lead_ip:*"])
        except Exception:
            pass
        statuses = []
        for _ in range(12):
            rr = client.post(
                "/api/public/leads",
                headers={"X-Forwarded-For": ip},
                json={"email": _email("rl"), "company_website": "x"},
            )
            statuses.append(rr.status_code)
        if 429 in statuses:
            _assert(statuses[:10].count(200) == 10, "first 10 allowed", statuses)
            print("per-IP rate limit trips ok")
        else:
            print(f"per-IP rate limit check SKIPPED (no 429; statuses={set(statuses)})")

        print()
        print("public lead smoke ok")
        return 0
    finally:
        _cleanup(admin_id, baseline_seq)
        print("cleanup done")


if __name__ == "__main__":
    sys.exit(main())
