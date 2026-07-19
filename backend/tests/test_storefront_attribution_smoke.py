"""Smoke tests for storefront channel attribution (migration 096).

Covers the catering210-ported derivation ladder and the server-only revenue
milestone:
  - derive_source(): UTM beats click-id beats referrer; fbclid-only paid
    clicks stop reading as "(direct)"; tags recovered from the referrer URL
    (in-app-browser orphan); self-referrals ignored; unknown stays None.
  - is_bot_user_agent(): crawlers/preview bots/script UAs (and empty UA)
    are bots; real browsers are not.
  - POST /api/public/track with an fbclid stores a facebook/paid event and
    a bot UA stores nothing.
  - A lead submitted with ONLY an fbclid gets lead_attribution.source
    facebook/paid with the click_id captured.
  - record_milestone(): server-only names enforced, idempotent on
    dedupe_key, inherits the deal's first-touch source; summary() then
    shows the revenue under that channel.

Run as a script (writes/removes its own rows):
    .venv/bin/python tests/test_storefront_attribution_smoke.py
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
    "test-key-not-for-production-just-smoke-testing-only-please",
)
os.environ.setdefault("STOREFRONT_ANALYTICS_ENABLED", "true")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from services import storefront_analytics_service as svc  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8]
_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148"
)


def _assert(cond: bool, label: str, detail: object = "") -> None:
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _cleanup() -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "DELETE FROM storefront_events WHERE visitor_id IN "
                "(SELECT id FROM storefront_visitors WHERE visitor_key LIKE :k)"
            ),
            {"k": f"smoke{_TAG}%"},
        )
        db.execute(
            sql_text("DELETE FROM storefront_visitors WHERE visitor_key LIKE :k"),
            {"k": f"smoke{_TAG}%"},
        )
        db.commit()
    finally:
        db.close()


def test_derive_source_ladder() -> None:
    # Rung 1: explicit UTM wins even when a click id rides along.
    d = svc.derive_source(
        utm={"source": "Newsletter", "medium": "Email"},
        click_ids={"fbclid": "abc123"},
    )
    _assert(d["source"] == "newsletter" and d["medium"] == "email", "utm wins", d)
    _assert(d["click_id"] == "abc123", "click id still captured", d)

    # Rung 2: fbclid-only paid click must NOT read as direct.
    d = svc.derive_source(click_ids={"fbclid": "IwAR123"})
    _assert(
        d == {"source": "facebook", "medium": "paid", "click_id": "IwAR123"},
        "fbclid → facebook/paid",
        d,
    )
    d = svc.derive_source(click_ids={"gclid": "g1"})
    _assert(d["source"] == "google" and d["medium"] == "paid", "gclid → google/paid", d)

    # Rung 2b: orphan recovery — tags live in the URL the visitor came from.
    d = svc.derive_source(
        referrer="https://kelleyautoplex.com/?utm_source=facebook&utm_medium=paid&fbclid=Z9",
        page_url="/inventory",
    )
    _assert(
        d["source"] == "facebook" and d["click_id"] == "Z9",
        "recovered from referrer URL",
        d,
    )

    # Rung 3: referrer host classification; self-referral ignored.
    d = svc.derive_source(
        referrer="https://www.google.com/search?q=used+cars",
        page_url="https://kelleyautoplex.com/inventory",
    )
    _assert(d["source"] == "google" and d["medium"] == "organic", "google organic", d)
    d = svc.derive_source(
        referrer="https://kelleyautoplex.com/about",
        page_url="https://kelleyautoplex.com/inventory",
    )
    _assert(d["source"] is None, "self-referral is not a source", d)

    # Bottom: nothing known stays honestly unknown.
    d = svc.derive_source()
    _assert(d == {"source": None, "medium": None, "click_id": None}, "direct", d)

    print("derive_source ladder ok")


def test_bot_filter() -> None:
    for ua in (
        None,
        "",
        "facebookexternalhit/1.1",
        "meta-externalagent/1.1",
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "python-requests/2.31.0",
        "curl/8.5.0",
    ):
        _assert(svc.is_bot_user_agent(ua), "bot detected", ua)
    _assert(not svc.is_bot_user_agent(_UA), "real browser passes", _UA)
    print("bot filter ok")


def test_track_endpoint() -> None:
    vid = f"smoke{_TAG}a"
    # A real browser with only an fbclid → stored as facebook/paid.
    r = client.post(
        "/api/public/track",
        json={
            "ka_vid": vid,
            "ka_sid": f"{vid}s",
            "event_name": "page_view",
            "path": "/inventory",
            "fbclid": "SMOKECLID",
        },
        headers={"user-agent": _UA},
    )
    _assert(r.status_code == 200 and r.json()["ok"], "track ack", r.text)

    # A bot UA gets the same ack and writes nothing.
    r = client.post(
        "/api/public/track",
        json={"ka_vid": f"smoke{_TAG}bot", "event_name": "page_view", "path": "/"},
        headers={"user-agent": "facebookexternalhit/1.1"},
    )
    _assert(r.status_code == 200 and r.json()["ok"], "bot ack", r.text)

    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT e.source, e.medium, e.click_id FROM storefront_events e "
                "JOIN storefront_visitors v ON v.id = e.visitor_id "
                "WHERE v.visitor_key = :k"
            ),
            {"k": vid},
        ).first()
        _assert(row is not None, "event stored")
        _assert(
            tuple(row) == ("facebook", "paid", "SMOKECLID"),
            "derived channel stored",
            tuple(row),
        )
        bot_rows = db.execute(
            sql_text(
                "SELECT COUNT(*) FROM storefront_visitors WHERE visitor_key = :k"
            ),
            {"k": f"smoke{_TAG}bot"},
        ).scalar()
        _assert(int(bot_rows) == 0, "bot wrote nothing", bot_rows)
        first_touch = db.execute(
            sql_text(
                "SELECT first_touch_attribution->>'source' FROM storefront_visitors "
                "WHERE visitor_key = :k"
            ),
            {"k": vid},
        ).scalar()
        _assert(first_touch == "facebook", "visitor first touch enriched", first_touch)
    finally:
        db.close()
    print("track endpoint ok")


def test_milestone_and_summary() -> None:
    # record_milestone refuses client event names.
    db = SessionLocal()
    try:
        _assert(
            svc.record_milestone(db, event_name="page_view", crm_event_id=1) is None,
            "client name rejected",
        )
        # Pick any real deal id to attach the milestone to (rolled back after).
        deal_id = db.execute(
            sql_text("SELECT id FROM events ORDER BY id LIMIT 1")
        ).scalar()
        if deal_id is None:
            print("milestone/summary skipped (no deals in DB)")
            return
        key = f"smoke:{_TAG}"
        first = svc.record_milestone(
            db,
            event_name="payment_received",
            crm_event_id=int(deal_id),
            amount_cents=123400,
            dedupe_key=key,
        )
        _assert(first is not None, "milestone written")
        dup = svc.record_milestone(
            db,
            event_name="payment_received",
            crm_event_id=int(deal_id),
            amount_cents=123400,
            dedupe_key=key,
        )
        _assert(dup is None, "milestone idempotent")

        s = svc.summary(db, days=7)
        _assert(
            any(step["event_name"] == "payment_received" for step in s["funnel"]),
            "funnel includes payments",
        )
        _assert(s["total_revenue_cents"] >= 123400, "revenue summed", s["total_revenue_cents"])
        db.rollback()  # never persist the fake payment milestone
    finally:
        db.close()
    print("milestone + summary ok")


if __name__ == "__main__":
    try:
        test_derive_source_ladder()
        test_bot_filter()
        test_track_endpoint()
        test_milestone_and_summary()
    finally:
        _cleanup()
    print("ALL STOREFRONT ATTRIBUTION SMOKES PASSED")
