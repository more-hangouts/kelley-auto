"""Event workflow definitions — the kanban columns and their semantics.

Status codes here are mirrored in the chk_events_status CHECK constraint in
database/migrations/015_create_events.py. When adding or removing a status,
update both — Postgres won't accept a status the constraint doesn't list.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EventStatus:
    code: str
    label: str
    sort_order: int
    is_terminal: bool = False
    description: str = ""


QUINCEANERA_STATUSES: tuple[EventStatus, ...] = (
    EventStatus(
        code="lead",
        label="Lead",
        sort_order=1,
        description="Appointment booked, customer hasn't attended yet.",
    ),
    EventStatus(
        code="consulted",
        label="Consulted",
        sort_order=2,
        description="Came in, browsed, no purchase yet — warm follow-up bucket.",
    ),
    EventStatus(
        code="sold",
        label="Sold",
        sort_order=3,
        description="Deposit paid, dress selected.",
    ),
    EventStatus(
        code="on_order",
        label="On Order",
        sort_order=4,
        description="Special order placed with the designer / vendor.",
    ),
    EventStatus(
        code="arrived",
        label="Arrived",
        sort_order=5,
        description="Dress is in store, awaiting first fitting.",
    ),
    EventStatus(
        code="in_alterations",
        label="In Alterations",
        sort_order=6,
        description="Being altered.",
    ),
    EventStatus(
        code="ready_for_pickup",
        label="Ready for Pickup",
        sort_order=7,
        description="Alterations complete, awaiting customer.",
    ),
    EventStatus(
        code="picked_up",
        label="Picked Up",
        sort_order=8,
        is_terminal=True,
        description="Customer has the dress — completed.",
    ),
    EventStatus(
        code="cancelled",
        label="Cancelled",
        sort_order=9,
        is_terminal=True,
        description="Lost lead or refunded order.",
    ),
)


# Kelley Autoplex car-deal pipeline (Day 3). Mirrored in the chk_events_status
# CHECK widened by database/migrations/086_vehicle_sale_workflow.py — keep the
# two in sync. `sold` is intentionally NON-terminal: the deal stays open after
# the sale so the team can finish paperwork and delivery. `delivered` and
# `lost` are the only terminal columns. Note `sold` is shared with the
# quinceañera workflow; the union CHECK lists it once.
VEHICLE_SALE_STATUSES: tuple[EventStatus, ...] = (
    EventStatus(
        code="new_lead",
        label="New Lead",
        sort_order=1,
        description="Inbound inquiry, not yet worked.",
    ),
    EventStatus(
        code="contacted",
        label="Contacted",
        sort_order=2,
        description="Salesperson has reached out.",
    ),
    EventStatus(
        code="appointment",
        label="Appointment",
        sort_order=3,
        description="Showroom visit scheduled.",
    ),
    EventStatus(
        code="test_drive",
        label="Test Drive",
        sort_order=4,
        description="Customer has driven the vehicle.",
    ),
    EventStatus(
        code="negotiation",
        label="Negotiation",
        sort_order=5,
        description="Working numbers — price, trade-in, terms.",
    ),
    EventStatus(
        code="financing",
        label="Financing",
        sort_order=6,
        description="Credit application / lender approval in progress.",
    ),
    EventStatus(
        code="sold",
        label="Sold",
        sort_order=7,
        description="Deal closed — paperwork and delivery still to finish.",
    ),
    EventStatus(
        code="delivered",
        label="Delivered",
        sort_order=8,
        is_terminal=True,
        description="Keys handed over — deal complete.",
    ),
    EventStatus(
        code="lost",
        label="Lost",
        sort_order=9,
        is_terminal=True,
        description="Customer walked or bought elsewhere.",
    ),
)


EVENT_WORKFLOWS: dict[str, tuple[EventStatus, ...]] = {
    "quinceanera": QUINCEANERA_STATUSES,
    "vehicle_sale": VEHICLE_SALE_STATUSES,
}


def all_statuses(event_type: str) -> tuple[EventStatus, ...]:
    if event_type not in EVENT_WORKFLOWS:
        raise ValueError(f"unknown event_type: {event_type!r}")
    return EVENT_WORKFLOWS[event_type]


def initial_status(event_type: str) -> str:
    """The status a freshly created event of this type starts in — the
    column with the lowest sort_order ('lead' for quinceañera, 'new_lead'
    for vehicle sales). Replaces the previously hardcoded 'lead' so each
    workflow seeds its own first column.
    """
    return min(all_statuses(event_type), key=lambda s: s.sort_order).code


def status_codes(event_type: str) -> set[str]:
    return {s.code for s in all_statuses(event_type)}


def get_status(event_type: str, code: str) -> EventStatus:
    for s in all_statuses(event_type):
        if s.code == code:
            return s
    raise ValueError(
        f"unknown status {code!r} for event_type {event_type!r}"
    )
