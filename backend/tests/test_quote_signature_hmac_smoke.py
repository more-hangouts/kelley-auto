"""Smoke for C3: quote signature HMAC + immutability trigger.

Covers the five user-specified acceptance cases plus one tampering
demonstration:

  1. New signature gets an HMAC stamped at sign time.
  2. Repeat accept does not overwrite (existing idempotency path
     short-circuits before re-stamping).
  3. Direct DB UPDATE of any signature field raises CheckViolation.
  4. Unsigned (draft) quote can still transition to signed (the
     trigger gates "non-null → different", not first-time assignment).
  5. A pre-C3 row that the migration backfilled still verifies.
  6. Tampering bonus: change a stable business term (total_cents)
     via direct SQL on a signed row — verify() returns False,
     proving the HMAC binds the business terms, not just the image.

Run with: venv/bin/python tests/test_quote_signature_hmac_smoke.py
"""

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from sqlalchemy import text as sql_text  # noqa: E402

from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Contact,
    Event,
    Quote,
    QuoteLineItem,
    User,
)
from services import quote_service, quote_signature_hmac as h  # noqa: E402


_ids = {"contacts": [], "events": [], "quotes": [], "users": []}


def _seed_quote(*, status: str = "draft") -> tuple[int, int]:
    """Return (quote_id, contact_id) after seeding the row chain."""
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        # Sales user — only needed if we go through approve_in_store; we
        # use approve_quote (portal path) which does NOT require an actor,
        # so the user is purely for the activity-log foreign keys.
        u = User(
            username=f"c3-sales-{suffix}",
            email=f"c3-sales-{suffix}@example.com",
            hashed_password=hash_password("unused"),
            full_name="C3 Sales",
            is_active=True,
            role="sales",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.flush()
        _ids["users"].append(u.id)

        c = Contact(
            display_name=f"C3 Customer {suffix}",
            phone_e164=f"+1210555{uuid.uuid4().int % 10_000:04d}",
            phone="(210) 555-0001",
            email=f"c3-{suffix}@example.com",
        )
        db.add(c)
        db.flush()
        _ids["contacts"].append(c.id)

        e = Event(
            primary_contact_id=c.id,
            event_type="quinceanera",
            event_name=f"C3 Test {suffix}",
            event_date=date.today() + timedelta(days=200),
            status="consulted",
        )
        db.add(e)
        db.flush()
        _ids["events"].append(e.id)

        q = Quote(
            event_id=e.id,
            contact_id=c.id,
            status=status,
            issue_date=date.today(),
            subtotal_cents=200_000,
            discount_cents=0,
            tax_cents=0,
            total_cents=200_000,
            created_by_user_id=u.id,
        )
        if status != "draft":
            q.quote_number = f"Q-C3-SMOKE-{suffix.upper()}"
            q.sent_at = datetime.now(timezone.utc)
        db.add(q)
        db.flush()
        _ids["quotes"].append(q.id)

        db.add(
            QuoteLineItem(
                quote_id=q.id,
                description="Dress package",
                quantity=1,
                unit_price_cents=200_000,
                line_subtotal_cents=200_000,
                line_tax_cents=0,
                line_total_cents=200_000,
                sort_order=1,
            )
        )
        db.commit()
        return q.id, c.id
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _ids["quotes"]:
            # Convert wrapper rows are not created in this smoke; just drop
            # the quote_line_items + quotes, then events + contacts + users.
            db.execute(
                sql_text("DELETE FROM quote_line_items WHERE quote_id = ANY(:ids)"),
                {"ids": _ids["quotes"]},
            )
            db.execute(
                sql_text("DELETE FROM activity_log WHERE subject_kind = 'quote' AND subject_id = ANY(:ids)"),
                {"ids": _ids["quotes"]},
            )
            db.execute(
                sql_text("DELETE FROM quotes WHERE id = ANY(:ids)"),
                {"ids": _ids["quotes"]},
            )
        if _ids["events"]:
            db.execute(
                sql_text("DELETE FROM event_status_change_events WHERE event_id = ANY(:ids)"),
                {"ids": _ids["events"]},
            )
            db.execute(
                sql_text("DELETE FROM event_participants WHERE event_id = ANY(:ids)"),
                {"ids": _ids["events"]},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _ids["events"]},
            )
        if _ids["contacts"]:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _ids["contacts"]},
            )
        if _ids["users"]:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _ids["users"]},
            )
        db.commit()
    finally:
        db.close()


try:
    # ---------------------------------------------------------------------
    # 1. New signature gets an HMAC stamped at sign time.
    # ---------------------------------------------------------------------
    quote_id, contact_id = _seed_quote(status="sent")

    db = SessionLocal()
    try:
        q = quote_service.approve_quote(
            db,
            quote_id=quote_id,
            signature_base64="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA",
            signature_name="Smoke Signer",
            signature_ip="192.0.2.50",
        )
        db.commit()
        db.refresh(q)
        assert q.status == "approved", q.status
        assert q.signature_hmac is not None and len(q.signature_hmac) == 64, q.signature_hmac
        # Recompute and confirm verify().
        assert h.verify(q), "verify() should pass on a freshly signed quote"
        original_hmac = q.signature_hmac
    finally:
        db.close()
    print("new signature stamped + verifies ok")

    # ---------------------------------------------------------------------
    # 2. Repeat accept is a no-op (idempotent). The service short-circuits
    # because status == "approved", so signature_hmac stays identical.
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        q = quote_service.approve_quote(
            db,
            quote_id=quote_id,
            signature_base64="this-does-not-matter",
            signature_name="Different Name",
            signature_ip="203.0.113.99",
        )
        db.commit()
        db.refresh(q)
        assert q.signature_hmac == original_hmac, (
            f"repeat accept should not change HMAC: {q.signature_hmac!r} vs {original_hmac!r}"
        )
        assert q.signature_name == "Smoke Signer", (
            f"repeat accept should not change signature_name: {q.signature_name!r}"
        )
    finally:
        db.close()
    print("repeat accept idempotent (HMAC preserved) ok")

    # ---------------------------------------------------------------------
    # 3. Direct DB UPDATE of any signature field raises. Test each guarded
    # column with its own savepoint so one failure doesn't poison the
    # next. signature_user_agent is allowed to stay null on this row, so
    # changing it from NULL is permitted (gate is "OLD IS NOT NULL"). We
    # only test the columns that ARE non-null after signing.
    # ---------------------------------------------------------------------
    guarded_targets = [
        ("signature_base64", "'tampered-image'"),
        ("signature_signed_at", "NOW()"),
        ("signature_ip", "'10.0.0.1'"),
        ("signature_name", "'Tampered Name'"),
        ("signature_hmac", "REPEAT('a', 64)"),
    ]
    for col, expr in guarded_targets:
        db = SessionLocal()
        try:
            try:
                db.execute(
                    sql_text(f"UPDATE quotes SET {col} = {expr} WHERE id = :i"),
                    {"i": quote_id},
                )
                db.commit()
            except Exception as exc:
                db.rollback()
                msg = str(exc)
                assert "immutable once signed" in msg, (col, msg)
            else:
                raise AssertionError(
                    f"trigger should have blocked UPDATE of {col}"
                )
        finally:
            db.close()
    print(f"trigger blocks direct UPDATE of all {len(guarded_targets)} guarded columns ok")

    # ---------------------------------------------------------------------
    # 4. Unsigned quote can still transition to signed.
    # Seed a fresh draft quote and run approve_in_store (the path that
    # accepts both 'draft' and 'sent'). The first signing must succeed
    # despite the trigger now being in place.
    # ---------------------------------------------------------------------
    fresh_quote_id, _ = _seed_quote(status="draft")
    actor_id = _ids["users"][-1]
    db = SessionLocal()
    try:
        q = quote_service.approve_in_store(
            db,
            quote_id=fresh_quote_id,
            signature_base64="data:image/png;base64,iVBORw0KGgoAAAA-fresh",
            signature_name="In Store Signer",
            signature_ip="198.51.100.7",
            actor_user_id=actor_id,
            signature_user_agent="Smoke/1.0 (C3)",
        )
        db.commit()
        db.refresh(q)
        assert q.status == "approved", q.status
        assert q.signature_hmac and len(q.signature_hmac) == 64, q.signature_hmac
        assert q.signature_user_agent == "Smoke/1.0 (C3)", q.signature_user_agent
        assert h.verify(q), "verify() should pass on in-store signed quote"
    finally:
        db.close()
    print("unsigned draft → signed transition still works ok")

    # ---------------------------------------------------------------------
    # 5. A pre-C3 backfilled quote (one of the 13 the migration handled)
    # still verifies. This is the load-bearing acceptance: backfill
    # must not have introduced a canonicalisation drift between the
    # migration's compute_hmac() and the service-layer compute_hmac().
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT id FROM quotes WHERE signature_signed_at IS NOT NULL "
                "AND id NOT IN :exclude ORDER BY id LIMIT 1"
            ),
            {"exclude": tuple(_ids["quotes"]) or (0,)},
        ).first()
        assert row is not None, "expected a pre-existing signed quote on prod"
        existing = db.get(Quote, row.id)
        assert h.verify(existing), (
            f"backfilled HMAC on existing quote id={existing.id} no longer verifies"
        )
    finally:
        db.close()
    print(f"pre-C3 backfilled quote id={row.id} still verifies ok")

    # ---------------------------------------------------------------------
    # 6. Tampering with a stable business term (NOT a signature column)
    # is NOT blocked by the trigger — but verify() catches it. This
    # demonstrates why the HMAC covers totals/identity in addition to
    # the image: the trigger protects the signature record, the HMAC
    # protects the agreement context.
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE quotes SET total_cents = total_cents + 1 WHERE id = :i"
            ),
            {"i": fresh_quote_id},
        )
        db.commit()
        tampered = db.get(Quote, fresh_quote_id)
        assert not h.verify(tampered), (
            "verify() should fail after total_cents tampering — HMAC binds totals"
        )
    finally:
        db.close()
    print("verify() detects total_cents tampering on signed row ok")

finally:
    _cleanup()
    print("cleanup done")

print("\ntest_quote_signature_hmac_smoke OK")
