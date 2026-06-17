"""Smoke tests for admin booking settings endpoints."""

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
os.environ.setdefault(
    "SECRET_KEY", "test-key-not-for-production-just-smoke-testing-only-please"
)
os.environ["SMTP_HOST"] = ""

from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text

from api.server import app
from database.auth import hash_password
from database.connection import SessionLocal
from database.models import (
    AppointmentAvailabilityRule,
    AppointmentBlackout,
    BookingWidgetThemeSettings,
    User,
)


client = TestClient(app)


def _make_admin():
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"settings-smoke-{suffix}",
            email=f"settings-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Settings Smoke",
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


def _delete_user(user_id):
    db = SessionLocal()
    try:
        db.execute(sql_text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Auth required on all
# ---------------------------------------------------------------------------

for path in (
    "/api/admin/booking/availability/rules",
    "/api/admin/booking/blackouts",
    "/api/admin/booking/settings",
):
    resp = client.get(path)
    assert resp.status_code == 401, f"{path} should require auth, got {resp.status_code}"
print("auth required ok")


user_id, user_email = _make_admin()
created_rule_id = None
created_blackout_id = None
original_settings = None
try:
    resp = client.post(
        "/api/auth/login",
        json={"email": user_email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    auth = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    print("admin login ok")

    # ---------------------------------------------------------------------
    # Availability rules CRUD
    # ---------------------------------------------------------------------

    resp = client.get("/api/admin/booking/availability/rules", headers=auth)
    assert resp.status_code == 200
    seeded = resp.json()
    assert len(seeded) >= 5, f"expected >=5 seeded rules, got {len(seeded)}"
    print(f"list rules ok ({len(seeded)} present)")

    new_rule = {
        "weekday": 5,  # Saturday
        "start_time": "10:00:00",
        "end_time": "11:00:00",
        "slot_duration_minutes": 30,
        "capacity": 2,
        "active": True,
        "label": "Smoke test extra hour",
    }
    resp = client.post("/api/admin/booking/availability/rules", headers=auth, json=new_rule)
    assert resp.status_code == 201, resp.text
    created_rule_id = resp.json()["id"]
    print(f"create rule ok (id={created_rule_id})")

    # Update — change capacity
    updated = {**new_rule, "capacity": 3, "label": "Smoke test edited"}
    resp = client.patch(
        f"/api/admin/booking/availability/rules/{created_rule_id}",
        headers=auth,
        json=updated,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["capacity"] == 3
    assert resp.json()["label"] == "Smoke test edited"
    print("update rule ok")

    # Bad time range rejected
    bad = {**new_rule, "start_time": "12:00:00", "end_time": "11:00:00"}
    resp = client.post("/api/admin/booking/availability/rules", headers=auth, json=bad)
    assert resp.status_code == 422, resp.text
    print("bad time range rejected ok")

    # Delete
    resp = client.delete(
        f"/api/admin/booking/availability/rules/{created_rule_id}", headers=auth
    )
    assert resp.status_code == 204, resp.text
    created_rule_id = None
    print("delete rule ok")

    # 404 for missing
    resp = client.delete(
        "/api/admin/booking/availability/rules/9999999", headers=auth
    )
    assert resp.status_code == 404
    print("missing rule 404 ok")

    # ---------------------------------------------------------------------
    # Blackouts CRUD
    # ---------------------------------------------------------------------

    start = (datetime.now(timezone.utc) + timedelta(days=400)).replace(microsecond=0)
    end = start + timedelta(hours=8)
    resp = client.post(
        "/api/admin/booking/blackouts",
        headers=auth,
        json={
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "reason": "Smoke test holiday",
        },
    )
    assert resp.status_code == 201, resp.text
    created_blackout_id = resp.json()["id"]
    assert resp.json()["created_by"] == user_id
    print(f"create blackout ok (id={created_blackout_id})")

    resp = client.get("/api/admin/booking/blackouts", headers=auth)
    assert resp.status_code == 200
    assert any(b["id"] == created_blackout_id for b in resp.json())
    print("list blackouts ok")

    resp = client.delete(
        f"/api/admin/booking/blackouts/{created_blackout_id}", headers=auth
    )
    assert resp.status_code == 204
    created_blackout_id = None
    print("delete blackout ok")

    # ---------------------------------------------------------------------
    # Theme settings GET/PUT (partial update semantics)
    # ---------------------------------------------------------------------

    db = SessionLocal()
    try:
        s = db.query(BookingWidgetThemeSettings).first()
        original_settings = (s.theme, s.copy, s.flow)
    finally:
        db.close()

    resp = client.get("/api/admin/booking/settings", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert "theme" in body and "copy_text" in body and "flow" in body
    print("get settings ok")

    # Patch only `theme.color_accent` — copy + flow untouched.
    new_theme = {**body["theme"], "color_accent": "#123456"}
    resp = client.put(
        "/api/admin/booking/settings", headers=auth, json={"theme": new_theme}
    )
    assert resp.status_code == 200, resp.text
    after = resp.json()
    assert after["theme"]["color_accent"] == "#123456"
    # Copy unchanged after a theme-only PUT
    assert after["copy_text"]["header_title"] == body["copy_text"]["header_title"]
    print("partial put preserves siblings ok")

    # Public theme endpoint reflects the change (anonymous access ok)
    resp = client.get("/api/booking/theme")
    assert resp.json()["theme"]["color_accent"] == "#123456"
    print("public theme reflects edit ok")

finally:
    # Restore settings exactly
    if original_settings is not None:
        db = SessionLocal()
        try:
            s = db.query(BookingWidgetThemeSettings).first()
            s.theme, s.copy, s.flow = original_settings
            db.commit()
        finally:
            db.close()
    if created_rule_id:
        db = SessionLocal()
        try:
            db.execute(
                sql_text("DELETE FROM appointment_availability_rules WHERE id = :id"),
                {"id": created_rule_id},
            )
            db.commit()
        finally:
            db.close()
    if created_blackout_id:
        db = SessionLocal()
        try:
            db.execute(
                sql_text("DELETE FROM appointment_blackouts WHERE id = :id"),
                {"id": created_blackout_id},
            )
            db.commit()
        finally:
            db.close()
    _delete_user(user_id)
    print("cleanup done")

print("\nadmin booking settings smoke ok")
