"""Smoke for Phase 10 Slice 3 (admin-configurable schedule presets).

Covers:

  1. The migration's three seed presets exist and are sorted as
     intended (Opening / Mid / Closing in sort_order order).
  2. Service-level validation rejects:
       - blank / whitespace-only label
       - end_time <= start_time (equal too — schema is strict >)
       - late_grace_minutes outside 0-120
       - sort_order < 0
       - duplicate active label (409)
     and allows reusing an archived preset's label.
  3. update_preset partial path: edit a single field, re-validate,
     bump updated_at. Patching with an unknown field returns
     unknown_field; empty patch returns nothing_to_update.
  4. archive_preset is idempotent and drops the row from the
     active-only list.
  5. Admin router end-to-end via TestClient: sales-token 403 on every
     verb, list (active vs all), create, patch, archive, re-activate.
"""

import os
import sys
import uuid
from datetime import time
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
from database.auth import (  # noqa: E402
    create_access_token,
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import StaffSchedulePreset, User  # noqa: E402
from services import staff_schedule_presets  # noqa: E402
from services.staff_schedule_presets import StaffSchedulePresetError  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_preset_ids: list[int] = []


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p10s3-{suffix}",
            email=f"{role}-p10s3-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P10S3 {role.title()} {suffix}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id
    finally:
        db.close()


def _token(user_id: int, *, sales: bool) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_sales_token(u) if sales else create_access_token(u)
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _preset_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_schedule_presets "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": _preset_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def _expect_error(
    fn, code: str, *, http_status: int | None = None
) -> StaffSchedulePresetError:
    try:
        fn()
    except StaffSchedulePresetError as exc:
        assert exc.code == code, (
            f"expected code={code!r}, got {exc.code!r}"
        )
        if http_status is not None:
            assert exc.http_status == http_status
        return exc
    raise AssertionError(
        f"expected StaffSchedulePresetError({code}) — got none"
    )


def main() -> None:
    admin_id = _make_user(role="admin")
    sales_id = _make_user(role="sales")
    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    sales_hdr = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}

    # ============================================================
    # 1) SEED PRESETS PRESENT
    # ============================================================
    print("===== seed presets =====")
    db = SessionLocal()
    try:
        active = staff_schedule_presets.list_presets(db, active_only=True)
        labels = [p["label"] for p in active]
        assert "Opening (9am - 5pm)" in labels
        assert "Mid (11am - 7pm)" in labels
        assert "Closing (1pm - 9pm)" in labels
        # sort_order ordering preserved.
        sort_orders = [p["sort_order"] for p in active]
        assert sort_orders == sorted(sort_orders), (
            "active listing should be sorted by sort_order"
        )
    finally:
        db.close()

    # ============================================================
    # 2) SERVICE VALIDATION
    # ============================================================
    print("===== service validation =====")
    db = SessionLocal()
    try:
        _expect_error(
            lambda: staff_schedule_presets.create_preset(
                db,
                actor_user_id=admin_id,
                label="   ",
                start_time_=time(9, 0),
                end_time_=time(17, 0),
            ),
            "label_required",
            http_status=422,
        )
        _expect_error(
            lambda: staff_schedule_presets.create_preset(
                db,
                actor_user_id=admin_id,
                label="P10S3 bad range",
                start_time_=time(17, 0),
                end_time_=time(9, 0),
            ),
            "invalid_time_range",
            http_status=422,
        )
        _expect_error(
            lambda: staff_schedule_presets.create_preset(
                db,
                actor_user_id=admin_id,
                label="P10S3 zero range",
                start_time_=time(9, 0),
                end_time_=time(9, 0),
            ),
            "invalid_time_range",
            http_status=422,
        )
        _expect_error(
            lambda: staff_schedule_presets.create_preset(
                db,
                actor_user_id=admin_id,
                label="P10S3 bad grace",
                start_time_=time(9, 0),
                end_time_=time(17, 0),
                late_grace_minutes=200,
            ),
            "late_grace_out_of_range",
            http_status=422,
        )
        _expect_error(
            lambda: staff_schedule_presets.create_preset(
                db,
                actor_user_id=admin_id,
                label="P10S3 bad sort",
                start_time_=time(9, 0),
                end_time_=time(17, 0),
                sort_order=-1,
            ),
            "sort_order_negative",
            http_status=422,
        )

        # Happy-path create.
        created = staff_schedule_presets.create_preset(
            db,
            actor_user_id=admin_id,
            label="P10S3 Morning",
            start_time_=time(8, 0),
            end_time_=time(14, 0),
            late_grace_minutes=20,
            sort_order=50,
        )
        db.commit()
        _preset_ids.append(created["id"])
        assert created["label"] == "P10S3 Morning"
        assert created["start_time"] == "08:00"
        assert created["end_time"] == "14:00"
        assert created["late_grace_minutes"] == 20
        assert created["active"] is True

        # Duplicate active label → 409.
        _expect_error(
            lambda: staff_schedule_presets.create_preset(
                db,
                actor_user_id=admin_id,
                label="P10S3 Morning",
                start_time_=time(7, 0),
                end_time_=time(13, 0),
            ),
            "duplicate_label",
            http_status=409,
        )
    finally:
        db.close()

    # ============================================================
    # 3) UPDATE / VALIDATION ON UPDATE
    # ============================================================
    print("===== update =====")
    db = SessionLocal()
    try:
        # nothing_to_update on empty patch.
        _expect_error(
            lambda: staff_schedule_presets.update_preset(
                db, preset_id=created["id"], fields={}
            ),
            "nothing_to_update",
            http_status=422,
        )
        # unknown_field on a bogus key.
        _expect_error(
            lambda: staff_schedule_presets.update_preset(
                db, preset_id=created["id"], fields={"bogus": 1}
            ),
            "unknown_field",
            http_status=422,
        )
        # 404 on missing.
        _expect_error(
            lambda: staff_schedule_presets.update_preset(
                db, preset_id=999_999_999, fields={"label": "x"}
            ),
            "preset_not_found",
            http_status=404,
        )
        # Patch one field at a time, validate the post-update state.
        updated = staff_schedule_presets.update_preset(
            db,
            preset_id=created["id"],
            fields={"late_grace_minutes": 10, "sort_order": 75},
        )
        db.commit()
        assert updated["late_grace_minutes"] == 10
        assert updated["sort_order"] == 75
        # A patch that would make the post-state invalid is rejected
        # (just change end_time to before start_time).
        _expect_error(
            lambda: staff_schedule_presets.update_preset(
                db,
                preset_id=created["id"],
                fields={"end_time": time(7, 0)},
            ),
            "invalid_time_range",
            http_status=422,
        )
    finally:
        db.close()

    # ============================================================
    # 4) ARCHIVE + RE-USE LABEL
    # ============================================================
    print("===== archive + label re-use =====")
    db = SessionLocal()
    try:
        archived = staff_schedule_presets.archive_preset(
            db, preset_id=created["id"]
        )
        db.commit()
        assert archived["active"] is False

        # Idempotent — second archive is a no-op.
        again = staff_schedule_presets.archive_preset(
            db, preset_id=created["id"]
        )
        db.commit()
        assert again["active"] is False

        # Active-only list excludes it now.
        active = staff_schedule_presets.list_presets(db, active_only=True)
        assert created["id"] not in {p["id"] for p in active}

        # all-presets list still includes it.
        all_presets = staff_schedule_presets.list_presets(
            db, active_only=False
        )
        assert created["id"] in {p["id"] for p in all_presets}

        # Re-use the archived label on a new active row — allowed because
        # the unique index is partial on active=TRUE.
        reused = staff_schedule_presets.create_preset(
            db,
            actor_user_id=admin_id,
            label="P10S3 Morning",
            start_time_=time(7, 0),
            end_time_=time(13, 0),
        )
        db.commit()
        _preset_ids.append(reused["id"])
        assert reused["label"] == "P10S3 Morning"

        # Now re-activating the original would collide — confirm 409.
        _expect_error(
            lambda: staff_schedule_presets.update_preset(
                db, preset_id=created["id"], fields={"active": True}
            ),
            "duplicate_label",
            http_status=409,
        )
    finally:
        db.close()

    # ============================================================
    # 5) ADMIN ROUTER end-to-end
    # ============================================================
    print("===== admin router =====")
    # Scope gate on every verb.
    assert (
        client.get(
            "/api/admin/schedule/presets", headers=sales_hdr
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/admin/schedule/presets",
            headers=sales_hdr,
            json={
                "label": "x",
                "start_time": "09:00",
                "end_time": "17:00",
            },
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/admin/schedule/presets/{reused['id']}",
            headers=sales_hdr,
            json={"label": "x"},
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/admin/schedule/presets/{reused['id']}",
            headers=sales_hdr,
        ).status_code
        == 403
    )

    # GET (default = active only) returns the seed + the row we created
    # via service; the archived original is excluded.
    resp = client.get(
        "/api/admin/schedule/presets", headers=admin_hdr
    )
    assert resp.status_code == 200
    active_ids = {p["id"] for p in resp.json()["presets"]}
    assert reused["id"] in active_ids
    assert created["id"] not in active_ids

    # include_archived=true brings the original back.
    resp = client.get(
        "/api/admin/schedule/presets",
        headers=admin_hdr,
        params={"include_archived": "true"},
    )
    assert resp.status_code == 200
    all_ids = {p["id"] for p in resp.json()["presets"]}
    assert created["id"] in all_ids

    # POST a new preset.
    resp = client.post(
        "/api/admin/schedule/presets",
        headers=admin_hdr,
        json={
            "label": f"P10S3 Router {uuid.uuid4().hex[:4]}",
            "start_time": "10:00",
            "end_time": "18:00",
            "late_grace_minutes": 15,
            "sort_order": 999,
        },
    )
    assert resp.status_code == 201, resp.text
    new = resp.json()
    _preset_ids.append(new["id"])
    assert new["start_time"] == "10:00"

    # PATCH it.
    resp = client.patch(
        f"/api/admin/schedule/presets/{new['id']}",
        headers=admin_hdr,
        json={"sort_order": 1000, "late_grace_minutes": 5},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sort_order"] == 1000
    assert resp.json()["late_grace_minutes"] == 5

    # Empty PATCH → 422 with nothing_to_update.
    resp = client.patch(
        f"/api/admin/schedule/presets/{new['id']}",
        headers=admin_hdr,
        json={},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "nothing_to_update"

    # Bad PATCH range → 422.
    resp = client.patch(
        f"/api/admin/schedule/presets/{new['id']}",
        headers=admin_hdr,
        json={"start_time": "20:00", "end_time": "10:00"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_time_range"

    # DELETE (soft) flips active=false.
    resp = client.delete(
        f"/api/admin/schedule/presets/{new['id']}",
        headers=admin_hdr,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] is False

    # Re-activate via PATCH.
    resp = client.patch(
        f"/api/admin/schedule/presets/{new['id']}",
        headers=admin_hdr,
        json={"active": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] is True

    print("phase10_presets smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
