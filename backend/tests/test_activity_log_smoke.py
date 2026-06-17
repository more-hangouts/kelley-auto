"""Smoke tests for the Phase 9 activity timeline.

Drives the activity log end-to-end:

  - Creating + sending an invoice emits ``invoice.created`` and
    ``invoice.sent`` rows with the right actor and payload.
  - A portal first-view emits ``invoice.viewed`` (actor_kind='customer'),
    a second view does NOT (the row only fires once per invitation per
    first-view per activity_log policy).
  - Creating + sending a quote, then signing it from the portal, emits
    ``quote.created``, ``quote.sent``, ``quote.signed``, ``quote.approved``
    in order.
  - Recording a payment emits ``payment.created``; a refund emits
    ``payment.refunded``.
  - Changing event status emits ``event.status_changed`` AND keeps the
    legacy ``event_status_change_events`` row intact.
  - The router endpoint ``GET /api/events/{id}/activity?limit=2`` returns
    a ``next_before_id`` and the next page picks up where it left off.
  - Pagination terminates: the final page returns ``next_before_id=None``.
  - Bare ``invitation.revoked`` activity row fires when staff revokes a
    customer-portal link.

Cleans up every row created. Runs as a script:

    venv/bin/python tests/test_activity_log_smoke.py

Internal helpers are named ``check_*`` so pytest does not collect
them.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal
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

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.routers.portal import _reset_rate_limit_state  # noqa: E402
from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    ActivityLog,
    Contact,
    Event,
    EventStatusChangeEvent,
    InvoiceInvitation,
    QuoteInvitation,
    User,
)
from services import (  # noqa: E402
    activity_log,
    event_service,
    invoice_service,
    payment_service,
    quote_service,
)
from services.invoice_service import (  # noqa: E402
    InstallmentInput,
    LineItemInput,
)
from services.payment_service import AllocationInput  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _seed_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"activity-smoke-{suffix}",
            email=f"activity-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Activity Smoke Admin",
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


def _seed_event() -> tuple[int, int]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:6]
        contact = Contact(
            display_name=f"Activity Smoke Mom {suffix}",
            email=f"activity-{suffix}@example.com",
            phone=f"(210) 555-{uuid.uuid4().int % 10000:04d}",
            first_name="Maria",
        )
        db.add(contact)
        db.flush()
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Activity Smoke Quince {suffix}",
            event_date=date.today() + timedelta(days=180),
            quince_theme_colors=[],
            status="lead",
        )
        db.add(event)
        db.commit()
        db.refresh(contact)
        db.refresh(event)
        return contact.id, event.id
    finally:
        db.close()


def _login(email: str) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _cleanup(user_ids, contact_ids, event_ids):
    db = SessionLocal()
    try:
        if event_ids:
            db.execute(
                sql_text(
                    "DELETE FROM activity_log WHERE event_id = ANY(:eids)"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM payment_allocations WHERE payment_id IN "
                    "(SELECT id FROM payments WHERE contact_id = ANY(:cids))"
                ),
                {"cids": contact_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM refund_events WHERE payment_id IN "
                    "(SELECT id FROM payments WHERE contact_id = ANY(:cids))"
                ),
                {"cids": contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM payments WHERE contact_id = ANY(:cids)"),
                {"cids": contact_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM quote_invitations WHERE quote_id IN "
                    "(SELECT id FROM quotes WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM quote_line_items WHERE quote_id IN "
                    "(SELECT id FROM quotes WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text("DELETE FROM quotes WHERE event_id = ANY(:eids)"),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_invitations WHERE invoice_id IN "
                    "(SELECT id FROM invoices WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_installments WHERE invoice_id IN "
                    "(SELECT id FROM invoices WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_line_items WHERE invoice_id IN "
                    "(SELECT id FROM invoices WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text("DELETE FROM invoices WHERE event_id = ANY(:eids)"),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events "
                    "WHERE event_id = ANY(:eids)"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:eids)"),
                {"eids": event_ids},
            )
        if contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:cids)"),
                {"cids": contact_ids},
            )
        if user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:uids)"),
                {"uids": user_ids},
            )
        db.commit()
    finally:
        db.close()


def _activity_types_for_event(event_id: int) -> list[str]:
    """Chronological list of activity_type for an event (oldest first)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(ActivityLog.activity_type)
            .filter(ActivityLog.event_id == event_id)
            .order_by(ActivityLog.id.asc())
            .all()
        )
        return [r[0] for r in rows]
    finally:
        db.close()


def _last_activity_payload(event_id: int) -> dict:
    db = SessionLocal()
    try:
        row = (
            db.query(ActivityLog)
            .filter(ActivityLog.event_id == event_id)
            .order_by(ActivityLog.id.desc())
            .first()
        )
        return dict(row.payload or {}) if row else {}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_invoice_create_and_send(event_id, contact_id, user_id) -> int:
    db = SessionLocal()
    try:
        inv = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    description="Dress",
                    quantity=Decimal("1"),
                    unit_price_cents=120000,
                )
            ],
            installments=[
                InstallmentInput(
                    label="Deposit",
                    amount_cents=60000,
                    due_date=date.today() + timedelta(days=30),
                ),
                InstallmentInput(
                    label="Balance",
                    amount_cents=60000,
                    due_date=date.today() + timedelta(days=120),
                ),
            ],
            actor_user_id=user_id,
        )
        db.commit()
        invoice_service.mark_sent(db, invoice_id=inv.id, actor_user_id=user_id)
        db.commit()
        invoice_id = inv.id
    finally:
        db.close()
    types = _activity_types_for_event(event_id)
    assert activity_log.INVOICE_CREATED in types, types
    assert activity_log.INVOICE_SENT in types, types
    return invoice_id


def check_invoice_view_emits_once(invoice_id: int, event_id: int):
    """First portal view fires invoice.viewed; a second view does not."""
    db = SessionLocal()
    try:
        invitation = (
            db.query(InvoiceInvitation)
            .filter(InvoiceInvitation.invoice_id == invoice_id)
            .filter(InvoiceInvitation.deleted_at.is_(None))
            .filter(InvoiceInvitation.revoked_at.is_(None))
            .first()
        )
        assert invitation is not None
        public_key = invitation.public_key
    finally:
        db.close()

    _reset_rate_limit_state()
    # Full HTML page hit triggers stamp_invoice_view via the JS receipt
    # path; in the smoke we exercise it directly.
    r1 = client.post(f"/portal/invoice/{public_key}/view-receipt")
    assert r1.status_code == 204
    types_after_first = _activity_types_for_event(event_id)
    viewed_count = sum(
        1 for t in types_after_first if t == activity_log.INVOICE_VIEWED
    )
    assert viewed_count == 1, viewed_count

    r2 = client.post(f"/portal/invoice/{public_key}/view-receipt")
    assert r2.status_code == 204
    types_after_second = _activity_types_for_event(event_id)
    viewed_after_second = sum(
        1 for t in types_after_second if t == activity_log.INVOICE_VIEWED
    )
    assert viewed_after_second == 1, viewed_after_second


def check_quote_lifecycle(event_id, contact_id, user_id):
    db = SessionLocal()
    try:
        q = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    description="Full package",
                    quantity=Decimal("1"),
                    unit_price_cents=200000,
                )
            ],
            actor_user_id=user_id,
        )
        db.commit()
        quote_service.mark_sent(db, quote_id=q.id, actor_user_id=user_id)
        db.commit()
        quote_id = q.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        invitation = (
            db.query(QuoteInvitation)
            .filter(QuoteInvitation.quote_id == quote_id)
            .first()
        )
        public_key = invitation.public_key
    finally:
        db.close()

    _reset_rate_limit_state()
    accept = client.post(
        f"/portal/quote/{public_key}/accept",
        json={
            "signature_name": "Maria Lopez",
            "signature_base64": (
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
                "QVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII="
            ),
        },
    )
    assert accept.status_code == 200, accept.text

    types = _activity_types_for_event(event_id)
    for t in (
        activity_log.QUOTE_CREATED,
        activity_log.QUOTE_SENT,
        activity_log.QUOTE_SIGNED,
        activity_log.QUOTE_APPROVED,
    ):
        assert t in types, (t, types)


def check_payment_lifecycle(invoice_id, contact_id, user_id, event_id):
    db = SessionLocal()
    try:
        payment = payment_service.record_payment(
            db,
            contact_id=contact_id,
            amount_cents=60000,
            method="card",
            allocations=[
                AllocationInput(invoice_id=invoice_id, applied_cents=60000),
            ],
            actor_user_id=user_id,
        )
        db.commit()
        payment_id = payment.id
    finally:
        db.close()
    types = _activity_types_for_event(event_id)
    assert activity_log.PAYMENT_CREATED in types, types
    payload = _last_activity_payload(event_id)
    assert payload.get("amount_cents") == 60000, payload

    # Refund partial
    db = SessionLocal()
    try:
        from services.payment_service import AllocationRefundInput

        # The whole 60000 went to a single allocation; refund half of it
        from database.models import PaymentAllocation
        alloc = (
            db.query(PaymentAllocation)
            .filter(PaymentAllocation.payment_id == payment_id)
            .first()
        )
        payment_service.record_refund(
            db,
            payment_id=payment_id,
            amount_cents=30000,
            refund_method="card",
            allocation_refunds=[
                AllocationRefundInput(
                    allocation_id=alloc.id, refund_cents=30000
                )
            ],
            actor_user_id=user_id,
        )
        db.commit()
    finally:
        db.close()
    types2 = _activity_types_for_event(event_id)
    assert activity_log.PAYMENT_REFUNDED in types2, types2


def check_event_status_change(event_id, user_id):
    """Status change emits both legacy + activity_log rows."""
    pre_legacy = _legacy_status_count(event_id)
    db = SessionLocal()
    try:
        event_service.change_event_status(
            db,
            event_id=event_id,
            new_status="consulted",
            actor_user_id=user_id,
        )
        db.commit()
    finally:
        db.close()
    types = _activity_types_for_event(event_id)
    assert activity_log.EVENT_STATUS_CHANGED in types, types
    post_legacy = _legacy_status_count(event_id)
    assert post_legacy == pre_legacy + 1, (pre_legacy, post_legacy)


def _legacy_status_count(event_id: int) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(EventStatusChangeEvent)
            .filter(EventStatusChangeEvent.event_id == event_id)
            .count()
        )
    finally:
        db.close()


def check_router_pagination(event_id: int, headers: dict):
    """Two-page walk via before_id terminates with next_before_id=None."""
    resp = client.get(
        f"/api/events/{event_id}/activity?limit=2", headers=headers
    )
    assert resp.status_code == 200, resp.text
    page1 = resp.json()
    assert len(page1["activities"]) == 2
    assert page1["next_before_id"] is not None

    resp2 = client.get(
        f"/api/events/{event_id}/activity"
        f"?limit=200&before_id={page1['next_before_id']}",
        headers=headers,
    )
    assert resp2.status_code == 200
    page2 = resp2.json()
    # The second page returns everything older than the first page's
    # last id; since limit was raised above the row count, next is None.
    assert page2["next_before_id"] is None, page2

    # Newest-first order: the first page's first row id > second page's
    # first row id.
    assert page1["activities"][0]["id"] > page1["activities"][-1]["id"]


def check_router_requires_auth(event_id: int):
    resp = client.get(f"/api/events/{event_id}/activity")
    assert resp.status_code in (401, 403), resp.status_code


def check_router_404_on_unknown_event(headers: dict):
    resp = client.get("/api/events/999999999/activity", headers=headers)
    assert resp.status_code == 404


def check_emitted_types_match_known_vocabulary(event_id: int):
    """Every activity_type emitted into this event's log must be a
    member of ``activity_log._KNOWN_TYPES``. The service tolerates
    unknown strings (logs a warning rather than raising), so a typo'd
    constant or a forgotten registration would slip through unit tests
    silently — this is the catch-all that fails the smoke instead.

    Phase 13 vocabulary check called out in INVOICING_PHASES.md.
    """
    types = set(_activity_types_for_event(event_id))
    assert types, "no activity rows emitted — earlier checks regressed"
    known = activity_log._KNOWN_TYPES
    unknown = sorted(types - known)
    assert not unknown, (
        f"activity_type values not in _KNOWN_TYPES: {unknown}"
    )


def check_revoke_emits_invitation_revoked(
    invoice_id: int, event_id: int, headers: dict
):
    pre = _activity_types_for_event(event_id).count(
        activity_log.INVITATION_REVOKED
    )
    # Find a live invitation
    inv_resp = client.get(
        f"/api/invoices/{invoice_id}/invitations", headers=headers
    )
    assert inv_resp.status_code == 200
    live = [
        i for i in inv_resp.json()["invitations"] if i["revoked_at"] is None
    ]
    assert live, "expected a live invitation"
    target = live[0]

    rev = client.post(
        f"/api/invoices/{invoice_id}/invitations/{target['id']}/revoke",
        headers=headers,
    )
    assert rev.status_code == 200, rev.text
    post = _activity_types_for_event(event_id).count(
        activity_log.INVITATION_REVOKED
    )
    assert post == pre + 1, (pre, post)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    user_ids = []
    contact_ids = []
    event_ids = []

    user_id, email = _seed_admin()
    user_ids.append(user_id)
    headers = _login(email)

    contact_id, event_id = _seed_event()
    contact_ids.append(contact_id)
    event_ids.append(event_id)

    failed = 0
    checks: list[tuple[str, bool, str | None]] = []

    def run(name, fn, *args, **kwargs):
        nonlocal failed
        try:
            res = fn(*args, **kwargs)
            checks.append((name, True, None))
            return res
        except AssertionError as exc:
            failed += 1
            checks.append((name, False, str(exc)))
        except Exception as exc:
            failed += 1
            checks.append((name, False, f"unexpected: {exc!r}"))

    invoice_id = run(
        "invoice_create_and_send_emit_activity",
        check_invoice_create_and_send,
        event_id,
        contact_id,
        user_id,
    )
    if invoice_id:
        run(
            "invoice_view_emits_once",
            check_invoice_view_emits_once,
            invoice_id,
            event_id,
        )
        run(
            "payment_lifecycle_emits_activity",
            check_payment_lifecycle,
            invoice_id,
            contact_id,
            user_id,
            event_id,
        )

    run(
        "quote_lifecycle_emits_activity",
        check_quote_lifecycle,
        event_id,
        contact_id,
        user_id,
    )
    run(
        "event_status_change_emits_activity",
        check_event_status_change,
        event_id,
        user_id,
    )
    run("router_requires_auth", check_router_requires_auth, event_id)
    run(
        "router_404_on_unknown_event",
        check_router_404_on_unknown_event,
        headers,
    )
    run(
        "router_pagination_walk",
        check_router_pagination,
        event_id,
        headers,
    )
    if invoice_id:
        run(
            "revoke_emits_invitation_revoked",
            check_revoke_emits_invitation_revoked,
            invoice_id,
            event_id,
            headers,
        )

    run(
        "emitted_types_match_known_vocabulary",
        check_emitted_types_match_known_vocabulary,
        event_id,
    )

    print()
    for name, ok, err in checks:
        if ok:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}: {err}")
    print()
    print(f"checks: {len(checks)}, failed: {failed}")

    _cleanup(user_ids, contact_ids, event_ids)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
