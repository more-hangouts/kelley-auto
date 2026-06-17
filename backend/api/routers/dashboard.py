"""Dashboard router.

Phase 10. Hosts the cross-domain rollups the staff Dashboard renders:
AR snapshot, recent payments list, awaiting-signature quote list.
Each route is a thin Pydantic-shape over the matching ``services.
dashboard`` function.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import dashboard


router = APIRouter()


# ---------------------------------------------------------------------------
# AR summary
# ---------------------------------------------------------------------------


class ARSummaryResponse(BaseModel):
    outstanding_balance_cents: int
    outstanding_invoice_count: int
    overdue_balance_cents: int
    overdue_invoice_count: int
    deposits_collected_this_month_cents: int


@router.get("/ar-summary", response_model=ARSummaryResponse)
def get_ar_summary(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> ARSummaryResponse:
    summary = dashboard.ar_summary(db)
    return ARSummaryResponse(
        outstanding_balance_cents=summary.outstanding_balance_cents,
        outstanding_invoice_count=summary.outstanding_invoice_count,
        overdue_balance_cents=summary.overdue_balance_cents,
        overdue_invoice_count=summary.overdue_invoice_count,
        deposits_collected_this_month_cents=(
            summary.deposits_collected_this_month_cents
        ),
    )


# ---------------------------------------------------------------------------
# Recent payments
# ---------------------------------------------------------------------------


class RecentPaymentResponse(BaseModel):
    id: int
    payment_number: str | None
    contact_id: int
    contact_name: str
    amount_cents: int
    method: str
    status: str
    payment_date: date
    created_at: datetime
    event_id: int | None


class RecentPaymentsResponse(BaseModel):
    payments: list[RecentPaymentResponse]


@router.get("/recent-payments", response_model=RecentPaymentsResponse)
def get_recent_payments(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
    limit: int = Query(default=10, ge=1, le=50),
) -> RecentPaymentsResponse:
    rows = dashboard.recent_payments(db, limit=limit)
    return RecentPaymentsResponse(
        payments=[
            RecentPaymentResponse(
                id=r.id,
                payment_number=r.payment_number,
                contact_id=r.contact_id,
                contact_name=r.contact_name,
                amount_cents=r.amount_cents,
                method=r.method,
                status=r.status,
                payment_date=r.payment_date,
                created_at=r.created_at,
                event_id=r.event_id,
            )
            for r in rows
        ]
    )


# ---------------------------------------------------------------------------
# Quotes awaiting signature
# ---------------------------------------------------------------------------


class AwaitingQuoteResponse(BaseModel):
    id: int
    quote_number: str | None
    event_id: int
    contact_id: int
    contact_name: str
    total_cents: int
    sent_at: datetime
    days_since_sent: int


class AwaitingQuotesResponse(BaseModel):
    quotes: list[AwaitingQuoteResponse]


@router.get("/awaiting-signature", response_model=AwaitingQuotesResponse)
def get_awaiting_signature_quotes(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
    min_age_days: int = Query(default=3, ge=0, le=365),
    limit: int = Query(default=25, ge=1, le=100),
) -> AwaitingQuotesResponse:
    rows = dashboard.quotes_awaiting_signature(
        db, min_age_days=min_age_days, limit=limit
    )
    return AwaitingQuotesResponse(
        quotes=[
            AwaitingQuoteResponse(
                id=r.id,
                quote_number=r.quote_number,
                event_id=r.event_id,
                contact_id=r.contact_id,
                contact_name=r.contact_name,
                total_cents=r.total_cents,
                sent_at=r.sent_at,
                days_since_sent=r.days_since_sent,
            )
            for r in rows
        ]
    )


# ---------------------------------------------------------------------------
# Today's agenda
# ---------------------------------------------------------------------------


class AgendaItemResponse(BaseModel):
    id: int
    slot_start_at: datetime
    slot_end_at: datetime
    party_size_bucket: str
    status: str
    crm_event_id: int | None
    contact_id: int | None
    display_name: str


class AgendaTodayResponse(BaseModel):
    date: str
    timezone: str
    appointments: list[AgendaItemResponse]


@router.get("/agenda-today", response_model=AgendaTodayResponse)
def get_agenda_today(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> AgendaTodayResponse:
    payload = dashboard.todays_agenda(db)
    return AgendaTodayResponse(
        date=payload["date"],
        timezone=payload["timezone"],
        appointments=[
            AgendaItemResponse(
                id=a.id,
                slot_start_at=a.slot_start_at,
                slot_end_at=a.slot_end_at,
                party_size_bucket=a.party_size_bucket,
                status=a.status,
                crm_event_id=a.crm_event_id,
                contact_id=a.contact_id,
                display_name=a.display_name,
            )
            for a in payload["appointments"]
        ],
    )


# ---------------------------------------------------------------------------
# SPLH leaderboard
# ---------------------------------------------------------------------------


class SPLHLeaderboardRowResponse(BaseModel):
    user_id: int
    username: str | None
    full_name: str | None
    revenue_cents: int
    invoice_count: int
    actual_hours: float
    splh_cents_per_hour: int | None


class SPLHLeaderboardResponse(BaseModel):
    from_date: date
    to_date: date
    revenue_basis: str
    rows: list[SPLHLeaderboardRowResponse]


@router.get("/splh-leaderboard", response_model=SPLHLeaderboardResponse)
def get_splh_leaderboard(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=25),
) -> SPLHLeaderboardResponse:
    payload = dashboard.splh_leaderboard(
        db,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
    )
    return SPLHLeaderboardResponse(
        from_date=payload.from_date,
        to_date=payload.to_date,
        revenue_basis=payload.revenue_basis,
        rows=[
            SPLHLeaderboardRowResponse(
                user_id=r.user_id,
                username=r.username,
                full_name=r.full_name,
                revenue_cents=r.revenue_cents,
                invoice_count=r.invoice_count,
                actual_hours=r.actual_hours,
                splh_cents_per_hour=r.splh_cents_per_hour,
            )
            for r in payload.rows
        ],
    )


# ---------------------------------------------------------------------------
# Pipeline counts
# ---------------------------------------------------------------------------


class PipelineLaneResponse(BaseModel):
    code: str
    label: str
    sort_order: int
    is_terminal: bool
    count: int


class PipelineCountsResponse(BaseModel):
    event_type: str
    lanes: list[PipelineLaneResponse]


@router.get("/pipeline-counts", response_model=PipelineCountsResponse)
def get_pipeline_counts(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
    event_type: str = Query(default="quinceanera"),
) -> PipelineCountsResponse:
    lanes = dashboard.pipeline_counts(db, event_type=event_type)
    return PipelineCountsResponse(
        event_type=event_type,
        lanes=[
            PipelineLaneResponse(
                code=lane.code,
                label=lane.label,
                sort_order=lane.sort_order,
                is_terminal=lane.is_terminal,
                count=lane.count,
            )
            for lane in lanes
        ],
    )
