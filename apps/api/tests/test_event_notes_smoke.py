"""Smoke tests for deal notes + follow-up reminders (migration 100).

Covers the running log behind the Notes tab and the reminder pass that
delivers "call them back Thursday":

  - notes list newest-first, carry an author byline, and reject blank bodies,
  - editing a body stamps edited_at; soft delete drops it from the timeline,
  - a reminder defaults to the acting rep, and rejects an unsendable
    channel ('sms' has no transport yet) rather than accepting it silently,
  - the reminder pass sends only DUE, unsent, unresolved, undeleted rows,
    stamps reminder_sent_at, and is idempotent on a second pass,
  - resolving a follow-up retires it even when it is due,
  - a delivery failure leaves reminder_sent_at NULL so the next tick retries.

Email is stubbed at the transport boundary — the pass must not depend on a
live mailbox.

Run as a script (matches the repo convention):
    .venv/bin/python tests/test_event_notes_smoke.py
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

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import EventNote, User  # noqa: E402
from modules.deals.services import note_reminder_runner  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8]


def _assert(cond: bool, label: str, detail: object = "") -> None:
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _make_admin() -> tuple[int, str, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"notes-{suffix}",
            email=f"notes-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Note Smoke Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, u.email, u.username
    finally:
        db.close()


def _make_contact(display_name: str) -> int:
    db = SessionLocal()
    try:
        cid = db.execute(
            sql_text(
                "INSERT INTO contacts (display_name, first_name, tags) "
                "VALUES (:dn, :fn, '[\"notes-smoke\"]'::jsonb) RETURNING id"
            ),
            {"dn": display_name, "fn": display_name.split()[0]},
        ).scalar()
        db.commit()
        return int(cid)
    finally:
        db.close()


def _note_row(note_id: int) -> EventNote | None:
    db = SessionLocal()
    try:
        return db.get(EventNote, note_id)
    finally:
        db.close()


def _set_remind_at(note_id: int, when: datetime) -> None:
    """Backdate a reminder so the pass sees it as due."""
    db = SessionLocal()
    try:
        db.execute(
            sql_text("UPDATE event_notes SET remind_at = :w WHERE id = :id"),
            {"w": when, "id": note_id},
        )
        db.commit()
    finally:
        db.close()


def _run_pass() -> note_reminder_runner.NoteReminderResult:
    db = SessionLocal()
    try:
        return note_reminder_runner.run_note_reminder_pass(db)
    finally:
        db.close()


class _StubTransport:
    """Captures sends instead of talking to a mail server."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[object] = []
        self.fail = fail

    def send(self, payload) -> None:
        if self.fail:
            raise RuntimeError("stub transport failure")
        self.sent.append(payload)


def _cleanup(user_ids: list[int], contact_ids: list[int]) -> None:
    db = SessionLocal()
    try:
        if contact_ids:
            # event_notes cascade with their event.
            db.execute(
                sql_text("DELETE FROM events WHERE primary_contact_id = ANY(:ids)"),
                {"ids": contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": contact_ids},
            )
        if user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> int:
    admin_id, admin_email, _username = _make_admin()
    contact_ids: list[int] = []
    import modules.core.services.email_transport as email_transport

    original_transport = email_transport.get_email_transport
    try:
        resp = client.post(
            "/api/auth/login",
            json={"email": admin_email, "password": "smoke-pass-12345"},
        )
        _assert(resp.status_code == 200, "login", resp.text)
        auth = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        print("login ok")

        buyer_id = _make_contact("Maria Gonzalez")
        contact_ids.append(buyer_id)
        resp = client.post(
            "/api/events",
            headers=auth,
            json={
                "primary_contact_id": buyer_id,
                "event_type": "vehicle_sale",
                "event_name": "2019 Toyota Camry — Maria",
            },
        )
        _assert(resp.status_code == 201, "create deal", resp.text)
        deal_id = resp.json()["id"]
        print(f"create deal ok (id={deal_id})")

        # --- empty timeline ------------------------------------------------
        resp = client.get(f"/api/events/{deal_id}/notes", headers=auth)
        _assert(resp.status_code == 200, "list notes", resp.text)
        _assert(resp.json() == [], "new deal has no notes", resp.json())

        # --- create a plain note -------------------------------------------
        resp = client.post(
            f"/api/events/{deal_id}/notes",
            headers=auth,
            json={"body": "  Called, left a voicemail.  "},
        )
        _assert(resp.status_code == 201, "create note", resp.text)
        first = resp.json()
        _assert(first["body"] == "Called, left a voicemail.", "body trimmed", first)
        _assert(
            first["author_display_name"] == "Note Smoke Admin",
            "author byline snapshotted",
            first,
        )
        _assert(first["remind_at"] is None, "plain note has no reminder", first)
        _assert(first["edited_at"] is None, "new note not marked edited", first)
        print("create plain note ok")

        # --- blank body rejected -------------------------------------------
        resp = client.post(
            f"/api/events/{deal_id}/notes", headers=auth, json={"body": "   "}
        )
        _assert(resp.status_code == 422, "blank note rejected", resp.text)
        print("blank note rejected ok")

        # --- note with a reminder, recipient defaulted to the actor --------
        due = datetime.now(timezone.utc) + timedelta(days=2)
        resp = client.post(
            f"/api/events/{deal_id}/notes",
            headers=auth,
            json={
                "body": "Called again, asked to call back Thursday.",
                "remind_at": due.isoformat(),
            },
        )
        _assert(resp.status_code == 201, "create reminder note", resp.text)
        reminder = resp.json()
        reminder_id = reminder["id"]
        _assert(
            reminder["remind_user_id"] == admin_id,
            "reminder defaults to the acting rep",
            reminder,
        )
        _assert(reminder["remind_channel"] == "email", "channel", reminder)
        _assert(reminder["reminder_sent_at"] is None, "not yet sent", reminder)
        print("create note with reminder ok")

        # --- sms rejected until a transport exists -------------------------
        resp = client.post(
            f"/api/events/{deal_id}/notes",
            headers=auth,
            json={
                "body": "Text me about this one.",
                "remind_at": due.isoformat(),
                "remind_channel": "sms",
            },
        )
        _assert(resp.status_code == 422, "sms channel rejected", resp.text)
        _assert(
            resp.json()["detail"]["code"] == "note_channel_unsupported",
            "sms rejection code",
            resp.json(),
        )
        print("unsupported channel rejected ok")

        # --- newest first ---------------------------------------------------
        resp = client.get(f"/api/events/{deal_id}/notes", headers=auth)
        rows = resp.json()
        _assert(len(rows) == 2, "two notes on the timeline", rows)
        _assert(rows[0]["id"] == reminder_id, "newest note first", rows)
        print("timeline order ok")

        # --- edit stamps edited_at ------------------------------------------
        resp = client.patch(
            f"/api/events/{deal_id}/notes/{first['id']}",
            headers=auth,
            json={"body": "Called, left a voicemail. Trying again tomorrow."},
        )
        _assert(resp.status_code == 200, "edit note", resp.text)
        _assert(resp.json()["edited_at"] is not None, "edited_at stamped", resp.json())
        print("edit note ok")

        # --- reminder pass: nothing due yet ---------------------------------
        stub = _StubTransport()
        email_transport.get_email_transport = lambda: stub
        result = _run_pass()
        _assert(result.scanned == 0, "future reminder not due", result)
        _assert(stub.sent == [], "nothing sent while not due", stub.sent)
        print("pass skips future reminders ok")

        # --- reminder pass: due row delivers --------------------------------
        _set_remind_at(reminder_id, datetime.now(timezone.utc) - timedelta(minutes=1))
        result = _run_pass()
        _assert(result.scanned == 1, "one due reminder scanned", result)
        _assert(result.sent == 1, "one reminder sent", result)
        _assert(len(stub.sent) == 1, "transport got one message", stub.sent)
        payload = stub.sent[0]
        _assert(payload.to == admin_email, "sent to the rep", payload.to)
        _assert("Maria Gonzalez" in payload.subject, "subject names the customer", payload.subject)
        _assert(
            "call back Thursday" in payload.text,
            "the rep's own words are in the email",
            payload.text,
        )
        _assert(
            f"/deals/{deal_id}/notes" in payload.text,
            "email links the deal by its new URL",
            payload.text,
        )
        row = _note_row(reminder_id)
        _assert(row.reminder_sent_at is not None, "reminder_sent_at stamped", row)
        print("reminder delivery ok")

        # --- idempotent: a second pass re-sends nothing ---------------------
        result = _run_pass()
        _assert(result.scanned == 0, "sent reminder not rescanned", result)
        _assert(len(stub.sent) == 1, "no duplicate send", stub.sent)
        print("reminder idempotency ok")

        # --- resolve retires a due reminder ---------------------------------
        resp = client.post(
            f"/api/events/{deal_id}/notes",
            headers=auth,
            json={
                "body": "Wants a callback about financing.",
                "remind_at": due.isoformat(),
            },
        )
        resolved_id = resp.json()["id"]
        _set_remind_at(resolved_id, datetime.now(timezone.utc) - timedelta(minutes=1))
        resp = client.post(
            f"/api/events/{deal_id}/notes/{resolved_id}/resolve",
            headers=auth,
            json={"resolved": True},
        )
        _assert(resp.status_code == 200, "resolve note", resp.text)
        _assert(resp.json()["resolved_at"] is not None, "resolved_at set", resp.json())
        result = _run_pass()
        _assert(result.scanned == 0, "resolved reminder not delivered", result)
        _assert(len(stub.sent) == 1, "still no duplicate send", stub.sent)
        print("resolve retires reminder ok")

        # --- delivery failure retries on the next tick ----------------------
        resp = client.post(
            f"/api/events/{deal_id}/notes",
            headers=auth,
            json={"body": "Retry me.", "remind_at": due.isoformat()},
        )
        retry_id = resp.json()["id"]
        _set_remind_at(retry_id, datetime.now(timezone.utc) - timedelta(minutes=1))

        failing = _StubTransport(fail=True)
        email_transport.get_email_transport = lambda: failing
        result = _run_pass()
        _assert(result.failed == 1, "delivery failure counted", result)
        row = _note_row(retry_id)
        _assert(
            row.reminder_sent_at is None,
            "failed delivery leaves the row unstamped for retry",
            row,
        )

        recovering = _StubTransport()
        email_transport.get_email_transport = lambda: recovering
        result = _run_pass()
        _assert(result.sent == 1, "next tick retries the failed reminder", result)
        _assert(len(recovering.sent) == 1, "retry delivered", recovering.sent)
        print("failed delivery retried ok")

        # --- soft delete drops out of timeline and pass ---------------------
        resp = client.post(
            f"/api/events/{deal_id}/notes",
            headers=auth,
            json={"body": "Delete me.", "remind_at": due.isoformat()},
        )
        doomed_id = resp.json()["id"]
        _set_remind_at(doomed_id, datetime.now(timezone.utc) - timedelta(minutes=1))
        resp = client.delete(
            f"/api/events/{deal_id}/notes/{doomed_id}", headers=auth
        )
        _assert(resp.status_code == 204, "delete note", resp.text)
        listed = client.get(f"/api/events/{deal_id}/notes", headers=auth).json()
        _assert(
            all(n["id"] != doomed_id for n in listed),
            "deleted note off the timeline",
            listed,
        )
        result = _run_pass()
        _assert(result.scanned == 0, "deleted note's reminder never fires", result)
        print("soft delete ok")

        # --- 404s -----------------------------------------------------------
        resp = client.get("/api/events/99999999/notes", headers=auth)
        _assert(resp.status_code == 404, "unknown deal 404s", resp.text)
        resp = client.patch(
            f"/api/events/{deal_id}/notes/99999999",
            headers=auth,
            json={"body": "nope"},
        )
        _assert(resp.status_code == 404, "unknown note 404s", resp.text)
        print("not-found handling ok")

        print()
        print("event notes smoke ok")
        return 0
    finally:
        email_transport.get_email_transport = original_transport
        _cleanup([admin_id], contact_ids)
        print("cleanup done")


if __name__ == "__main__":
    sys.exit(main())
