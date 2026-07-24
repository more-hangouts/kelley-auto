"""Smoke: omnichannel inbox inbound SMS flow (Phase 2; migration 094).

Drives ``services.inbox_service`` against the real schema and asserts the
inbound path end to end:

  1. Inbound SMS threads a conversation and auto-links a known contact + deal.
  2. ``inbox.message_received`` fans out to the admin (role default) via
     ``notification_jobs``.
  3. A second inbound from the same number reuses the thread; a MessageSid
     retry is idempotent (no duplicate message).
  4. Per-user unread derives and clears on read.
  5. Inbound reopens a resolved thread.
  6. STOP records opt-out on the contact and the conversation.
  7. Outbound send is hard-gated off (503) pre-A2P.

Requires migration 094 applied. Seeds under a prefix and deletes every row on
exit (ordered by FK dependency) so a re-run never sees prior state.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please-with-pad",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("RATE_LIMIT_FAIL_OPEN", "true")

from sqlalchemy import text as sql_text  # noqa: E402

from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Contact,
    ConversationMessage,
    Event,
    NotificationJob,
    User,
)
from modules.messaging.services import inbox_service  # noqa: E402

SEED = "smoke-inbox"
TO_NUMBER = "+17265550000"


def _cleanup(db, *, user_ids, contact_ids, conv_ids):
    db.rollback()
    if conv_ids:
        db.execute(sql_text(
            "DELETE FROM notification_jobs WHERE subject_kind='conversation' "
            "AND subject_id = ANY(:ids)"), {"ids": conv_ids})
        db.execute(sql_text(
            "DELETE FROM staff_notification_events WHERE subject_kind='conversation' "
            "AND subject_id = ANY(:ids)"), {"ids": conv_ids})
        # conversation_messages + conversation_reads cascade on conversation delete
        db.execute(sql_text(
            "DELETE FROM conversations WHERE id = ANY(:ids)"), {"ids": conv_ids})
    if user_ids:
        db.execute(sql_text(
            "DELETE FROM notification_jobs WHERE recipient_user_id = ANY(:ids)"),
            {"ids": user_ids})
    if contact_ids:
        db.execute(sql_text("DELETE FROM events WHERE primary_contact_id = ANY(:ids)"),
                   {"ids": contact_ids})
        db.execute(sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                   {"ids": contact_ids})
    if user_ids:
        db.execute(sql_text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": user_ids})
    db.commit()


def main() -> int:
    db = SessionLocal()
    user_ids, contact_ids, conv_ids = [], [], []
    try:
        tag = uuid.uuid4().hex[:8]
        # A valid, unlikely-to-collide E.164 derived from the tag.
        phone = f"+1210555{int(tag, 16) % 10000:04d}"

        admin = User(
            username=f"{SEED}-{tag}", email=f"{SEED}-{tag}@example.com",
            hashed_password=hash_password("smoke-pw-not-real-1234567890"),
            full_name="Smoke Admin", is_active=True, role="admin", permissions=[],
        )
        db.add(admin); db.flush()
        user_ids.append(admin.id)

        contact = Contact(display_name=f"Smoke {tag}", phone=phone, phone_e164=phone)
        db.add(contact); db.flush()
        contact_ids.append(contact.id)
        ev = Event(primary_contact_id=contact.id, event_type="vehicle_sale",
                   event_name=f"Smoke deal {tag}", status="lead", owner_user_id=admin.id)
        db.add(ev); db.commit()

        # 1. First inbound — threads + auto-links contact/deal.
        msg, conv, created = inbox_service.record_inbound_sms(
            db, message_sid=f"SM{tag}1", from_number=phone, to_number=TO_NUMBER,
            body="Is it available?")
        conv_ids.append(conv.id)
        assert created and conv.contact_id == contact.id and conv.event_id == ev.id
        inbox_service.notify_inbound(db, conv, msg); db.commit()
        print("  ok   inbound threads + auto-links contact/deal")

        # 2. Admin notified.
        assert db.query(NotificationJob).filter_by(
            kind="inbox.message_received", subject_id=conv.id,
            recipient_user_id=admin.id).count() >= 1
        print("  ok   admin notified via inbox.message_received")

        # 3. Same number reuses thread; MessageSid retry idempotent.
        _, conv2, _ = inbox_service.record_inbound_sms(
            db, message_sid=f"SM{tag}2", from_number=phone, to_number=TO_NUMBER, body="hi")
        assert conv2.id == conv.id
        _, _, again = inbox_service.record_inbound_sms(
            db, message_sid=f"SM{tag}1", from_number=phone, to_number=TO_NUMBER, body="x")
        db.commit()
        assert again is False
        assert db.query(ConversationMessage).filter_by(conversation_id=conv.id).count() == 2
        print("  ok   thread reuse + MessageSid retry idempotent")

        # 4. Unread derives + clears. Assert the DELTA, not an absolute —
        # the prod DB legitimately carries real unread threads now.
        before = inbox_service.unread_count_for_user(db, admin.id)
        assert before >= 1  # ours is among them
        inbox_service.mark_read(db, conv.id, admin.id); db.commit()
        assert inbox_service.unread_count_for_user(db, admin.id) == before - 1
        print("  ok   per-user unread derives + clears on read")

        # 5. Reopen on inbound.
        conv.status = "resolved"; db.commit()
        inbox_service.record_inbound_sms(
            db, message_sid=f"SM{tag}3", from_number=phone, to_number=TO_NUMBER, body="?")
        db.commit(); db.refresh(conv)
        assert conv.status == "open"
        print("  ok   inbound reopens resolved thread")

        # 6. STOP opt-out.
        inbox_service.record_inbound_sms(
            db, message_sid=f"SM{tag}4", from_number=phone, to_number=TO_NUMBER, body="STOP")
        db.commit(); db.refresh(contact)
        assert contact.sms_opted_out_at is not None
        print("  ok   STOP records contact opt-out")

        # 7. Outbound gated off.
        try:
            inbox_service.send_reply(db, conv.id, body="hi", user_id=admin.id)
            raise AssertionError("outbound should be gated")
        except inbox_service.InboxError as exc:
            assert exc.code == "sms_sending_disabled" and exc.http_status == 503
        print("  ok   outbound hard-gated (503) pre-A2P")

        print("\ninbox inbound smoke ok")
        return 0
    finally:
        _cleanup(db, user_ids=user_ids, contact_ids=contact_ids, conv_ids=conv_ids)
        db.close()


if __name__ == "__main__":
    sys.exit(main())
