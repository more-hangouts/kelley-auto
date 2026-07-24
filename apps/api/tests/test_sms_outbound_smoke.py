"""Smoke tests for outbound SMS (Phase 3, A2P-approved).

The Twilio transport is MOCKED throughout — this test NEVER sends a real
text and never depends on live creds. It exercises the guardrail order and
the delivery-status callback mapping directly against the service:

  - send is 503 when SMS_SENDING_ENABLED is off,
  - opt-out contact -> 409 recipient_opted_out (no send attempted),
  - quiet hours -> 409 quiet_hours; override sends,
  - a successful send writes a 'sent' row carrying the Twilio SID,
  - a Twilio rejection rolls back (no phantom row) and raises sms_send_failed,
  - apply_delivery_status maps sent/delivered/failed monotonically and never
    downgrades a delivered row.

Run as a script (writes/removes its own rows):
    .venv/bin/python tests/test_sms_outbound_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest import mock

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
from database.models import Contact, Conversation, User  # noqa: E402
from modules.messaging.services import inbox_service  # noqa: E402
from modules.core.services.sms_transport import SmsSendResult  # noqa: E402

_TAG = uuid.uuid4().hex[:8]
_PHONE = f"+1830555{_TAG[:4].translate(str.maketrans('abcdef', '012345'))}"[:12]


def _assert(cond: bool, label: str, detail: object = "") -> None:
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _admin_id(db) -> int:
    return int(
        db.execute(
            sql_text("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
        ).scalar()
    )


def _make_sms_conversation(
    db,
    *,
    opted_out: bool = False,
    consent: bool = True,
    has_inbound: bool = False,
) -> int:
    """Create an SMS conversation fixture.

    Defaults to ``consent=True`` so the pre-existing send-path tests exercise
    their intended branch (quiet hours, failure rollback, etc.) rather than
    tripping the consent guard. The dedicated consent tests pass
    ``consent=False`` with/without ``has_inbound`` to exercise that gate.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    contact = Contact(
        display_name=f"SMS Out {_TAG}",
        phone=_PHONE,
        phone_e164=_PHONE,
        sms_consent_at=now if consent else None,
        sms_consent_source="test" if consent else None,
    )
    db.add(contact)
    db.flush()
    conv = Conversation(
        provider="twilio",
        channel="sms",
        external_id=_PHONE,
        business_ref="+18305550000",
        contact_id=contact.id,
        status="open",
    )
    if has_inbound:
        # A customer-originated inbound message is signalled by last_inbound_at.
        conv.last_inbound_at = now
    if opted_out:
        conv.conversation_metadata = {"sms_opted_out_at": now.isoformat()}
    db.add(conv)
    db.flush()
    return conv.id


def _cleanup() -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "DELETE FROM conversations WHERE contact_id IN "
                "(SELECT id FROM contacts WHERE display_name LIKE :n)"
            ),
            {"n": f"SMS Out {_TAG}%"},
        )
        db.execute(
            sql_text("DELETE FROM contacts WHERE display_name LIKE :n"),
            {"n": f"SMS Out {_TAG}%"},
        )
        db.commit()
    finally:
        db.close()


def test_gated_off() -> None:
    db = SessionLocal()
    try:
        conv_id = _make_sms_conversation(db)
        with mock.patch("config.settings.SMS_SENDING_ENABLED", False):
            try:
                inbox_service.send_reply(db, conv_id, body="hi", user_id=_admin_id(db))
                raise AssertionError("expected sms_sending_disabled")
            except inbox_service.InboxError as exc:
                _assert(exc.code == "sms_sending_disabled", "gated off", exc.code)
        db.rollback()
    finally:
        db.close()
    print("gated off ok")


def test_opt_out_blocks() -> None:
    db = SessionLocal()
    try:
        conv_id = _make_sms_conversation(db, opted_out=True)
        sent = mock.Mock()
        with mock.patch("config.settings.SMS_SENDING_ENABLED", True), mock.patch(
            "modules.core.services.sms_transport.sms_transport_configured", return_value=True
        ), mock.patch(
            "modules.core.services.sms_transport.get_sms_transport"
        ) as get_tx:
            get_tx.return_value.send_result = sent
            try:
                inbox_service.send_reply(
                    db, conv_id, body="hi", user_id=_admin_id(db), allow_quiet_hours=True
                )
                raise AssertionError("expected recipient_opted_out")
            except inbox_service.InboxError as exc:
                _assert(exc.code == "recipient_opted_out", "opt-out blocks", exc.code)
        sent.assert_not_called()  # never attempted the send
        db.rollback()
    finally:
        db.close()
    print("opt-out blocks ok")


def _consent_mocks():
    """Common patches: sending enabled, transport configured, transport mocked
    so a consent decision is reached without any real send."""
    return (
        mock.patch("config.settings.SMS_SENDING_ENABLED", True),
        mock.patch(
            "modules.core.services.sms_transport.sms_transport_configured",
            return_value=True,
        ),
        mock.patch("modules.core.services.sms_transport.get_sms_transport"),
    )


def test_consent_required_blocks() -> None:
    """No form consent AND no inbound message → consent_required, no send."""
    db = SessionLocal()
    try:
        conv_id = _make_sms_conversation(db, consent=False, has_inbound=False)
        sent = mock.Mock()
        m_enabled, m_conf, m_tx = _consent_mocks()
        with m_enabled, m_conf, m_tx as get_tx:
            get_tx.return_value.send_result = sent
            try:
                inbox_service.send_reply(
                    db, conv_id, body="hi", user_id=_admin_id(db), allow_quiet_hours=True
                )
                raise AssertionError("expected recipient_no_sms_consent")
            except inbox_service.InboxError as exc:
                _assert(exc.code == "recipient_no_sms_consent", "consent required", exc.code)
        sent.assert_not_called()  # never attempted the send
        db.rollback()
    finally:
        db.close()
    print("consent required blocks ok")


def test_consent_recorded_allows() -> None:
    """Contact has sms_consent_at → send proceeds even with no inbound."""
    db = SessionLocal()
    try:
        conv_id = _make_sms_conversation(db, consent=True, has_inbound=False)
        ok_result = SmsSendResult(ok=True, provider_message_id=f"SM{_TAG}c", status="queued")
        m_enabled, m_conf, m_tx = _consent_mocks()
        with m_enabled, m_conf, m_tx as get_tx:
            get_tx.return_value.send_result.return_value = ok_result
            result = inbox_service.send_reply(
                db, conv_id, body="hi", user_id=_admin_id(db), allow_quiet_hours=True
            )
            _assert(result["message"]["status"] == "sent", "consent send", result)
        db.rollback()
    finally:
        db.close()
    print("consent recorded allows ok")


def test_inbound_thread_allows_without_form_consent() -> None:
    """No form consent BUT the customer texted first (last_inbound_at set) →
    the TCPA reply case is allowed."""
    db = SessionLocal()
    try:
        conv_id = _make_sms_conversation(db, consent=False, has_inbound=True)
        ok_result = SmsSendResult(ok=True, provider_message_id=f"SM{_TAG}i", status="queued")
        m_enabled, m_conf, m_tx = _consent_mocks()
        with m_enabled, m_conf, m_tx as get_tx:
            get_tx.return_value.send_result.return_value = ok_result
            result = inbox_service.send_reply(
                db, conv_id, body="hi", user_id=_admin_id(db), allow_quiet_hours=True
            )
            _assert(result["message"]["status"] == "sent", "inbound reply send", result)
        db.rollback()
    finally:
        db.close()
    print("inbound thread allows ok")


def test_quiet_hours_then_override() -> None:
    db = SessionLocal()
    try:
        conv_id = _make_sms_conversation(db)
        ok_result = SmsSendResult(ok=True, provider_message_id=f"SM{_TAG}", status="queued")
        with mock.patch("config.settings.SMS_SENDING_ENABLED", True), mock.patch(
            "modules.messaging.services.inbox_service.in_sms_quiet_hours", return_value=True
        ), mock.patch(
            "modules.core.services.sms_transport.sms_transport_configured", return_value=True
        ), mock.patch(
            "modules.core.services.sms_transport.get_sms_transport"
        ) as get_tx:
            get_tx.return_value.send_result.return_value = ok_result

            # Quiet hours blocks without override.
            try:
                inbox_service.send_reply(db, conv_id, body="hi", user_id=_admin_id(db))
                raise AssertionError("expected quiet_hours")
            except inbox_service.InboxError as exc:
                _assert(exc.code == "quiet_hours", "quiet hours blocks", exc.code)
            db.rollback()

            # Override sends.
            conv_id = _make_sms_conversation(db)
            result = inbox_service.send_reply(
                db, conv_id, body="hi", user_id=_admin_id(db), allow_quiet_hours=True
            )
            _assert(result["message"]["status"] == "sent", "override sends", result)
            _assert(
                result["message"].get("provider_message_id") is None
                or True,  # serializer may not expose it; check the row below
                "sent row",
            )
            sid = db.execute(
                sql_text(
                    "SELECT provider_message_id FROM conversation_messages "
                    "WHERE conversation_id = :c AND direction = 'outbound'"
                ),
                {"c": conv_id},
            ).scalar()
            _assert(sid == f"SM{_TAG}", "SID captured", sid)
        db.rollback()
    finally:
        db.close()
    print("quiet hours + override ok")


def test_send_failure_rolls_back() -> None:
    db = SessionLocal()
    try:
        conv_id = _make_sms_conversation(db)
        fail = SmsSendResult(ok=False, error_code="21610", error_message="unsubscribed recipient")
        with mock.patch("config.settings.SMS_SENDING_ENABLED", True), mock.patch(
            "modules.messaging.services.inbox_service.in_sms_quiet_hours", return_value=False
        ), mock.patch(
            "modules.core.services.sms_transport.sms_transport_configured", return_value=True
        ), mock.patch(
            "modules.core.services.sms_transport.get_sms_transport"
        ) as get_tx:
            get_tx.return_value.send_result.return_value = fail
            try:
                inbox_service.send_reply(db, conv_id, body="hi", user_id=_admin_id(db))
                raise AssertionError("expected sms_send_failed")
            except inbox_service.InboxError as exc:
                _assert(exc.code == "sms_send_failed", "send failure raises", exc.code)
                _assert("unsubscribed" in (exc.detail or ""), "carries reason", exc.detail)
        db.rollback()  # what the router would do
        # No phantom outbound row survives a rolled-back failure.
        n = db.execute(
            sql_text(
                "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = :c"
            ),
            {"c": conv_id},
        ).scalar()
        _assert(int(n) == 0, "no phantom row after rollback", n)
    finally:
        db.close()
    print("send failure rolls back ok")


def test_delivery_status_monotonic() -> None:
    db = SessionLocal()
    try:
        conv_id = _make_sms_conversation(db)
        sid = f"SM{_TAG}D"
        ok_result = SmsSendResult(ok=True, provider_message_id=sid, status="queued")
        with mock.patch("config.settings.SMS_SENDING_ENABLED", True), mock.patch(
            "modules.messaging.services.inbox_service.in_sms_quiet_hours", return_value=False
        ), mock.patch(
            "modules.core.services.sms_transport.sms_transport_configured", return_value=True
        ), mock.patch(
            "modules.core.services.sms_transport.get_sms_transport"
        ) as get_tx:
            get_tx.return_value.send_result.return_value = ok_result
            inbox_service.send_reply(db, conv_id, body="hi", user_id=_admin_id(db))

        _assert(inbox_service.apply_delivery_status(db, message_sid=sid, status="delivered"), "delivered applied")
        st = db.execute(
            sql_text("SELECT status, delivered_at IS NOT NULL FROM conversation_messages WHERE provider_message_id = :s"),
            {"s": sid},
        ).first()
        _assert(st[0] == "delivered" and st[1], "delivered stamped", tuple(st))

        # A late 'sent' must NOT downgrade a delivered row.
        inbox_service.apply_delivery_status(db, message_sid=sid, status="sent")
        st2 = db.execute(
            sql_text("SELECT status FROM conversation_messages WHERE provider_message_id = :s"),
            {"s": sid},
        ).scalar()
        _assert(st2 == "delivered", "no downgrade from delivered", st2)

        # Unknown SID is a no-op (returns False).
        _assert(
            inbox_service.apply_delivery_status(db, message_sid="SMnope", status="delivered") is False,
            "unknown sid no-op",
        )
        db.rollback()
    finally:
        db.close()
    print("delivery status monotonic ok")


if __name__ == "__main__":
    try:
        test_gated_off()
        test_opt_out_blocks()
        test_consent_required_blocks()
        test_consent_recorded_allows()
        test_inbound_thread_allows_without_form_consent()
        test_quiet_hours_then_override()
        test_send_failure_rolls_back()
        test_delivery_status_monotonic()
    finally:
        _cleanup()
    print("ALL SMS OUTBOUND SMOKES PASSED")
