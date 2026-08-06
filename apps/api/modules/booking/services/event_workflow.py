"""Event workflow definitions — the kanban columns and their semantics.

Status codes here are mirrored in the chk_events_status CHECK constraint,
created in database/migrations/015_create_events.py and last rewritten by
099_vehicle_sale_board_simplification.py (which added `follow_up` and
dropped the unused middle of the vehicle-sale funnel). When adding or
removing a status, write a new migration too — Postgres won't accept a
status the constraint doesn't list, and the constraint is the union of
every workflow's codes.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EventStatus:
    code: str
    label: str
    sort_order: int
    is_terminal: bool = False
    description: str = ""


# legacy Bella's-era rows: the quinceanera workflow is retired for new
# events (everything defaults to vehicle_sale), but existing rows still
# reference these statuses, so the definition must stay for reads,
# serialization, and status validation of historical events.
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


# Kelley Autoplex car-deal pipeline. Mirrored in the chk_events_status CHECK
# (widened by migration 086, narrowed to this set by 099) — keep the two in
# sync. Note `sold` is shared with the quinceañera workflow; the union CHECK
# lists it once, and each workflow carries its own is_terminal flag for it
# (terminal here, non-terminal there, where an order still has stages left).
#
# Deliberately short: this is a sales-facing board, and financing runs on its
# own system, so the columns only answer "did we talk to them, do we owe them
# a follow-up, did we win or lose it?". The old middle of the funnel
# (`appointment`, `test_drive`, `negotiation`, `financing`) and the separate
# `delivered` close were dropped in 099 — no deal had ever reached them.
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
        description="Salesperson has reached out — waiting on the customer.",
    ),
    EventStatus(
        code="follow_up",
        label="Follow Up",
        sort_order=3,
        description="Owed a callback — the working list to chase today.",
    ),
    EventStatus(
        code="sold",
        label="Sold",
        sort_order=4,
        is_terminal=True,
        description="Deal closed — car sold.",
    ),
    EventStatus(
        code="lost",
        label="Lost",
        sort_order=5,
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


def cancellation_status(event_type: str) -> str:
    """Status an event moves to when its booking/appointment is cancelled.

    The quinceañera workflow has an explicit ``cancelled`` column; the
    vehicle-sale workflow has none, so a scrapped deal lands in its terminal
    ``lost`` column instead. Keeps the cancel-mirror valid for every workflow.
    """
    codes = {s.code for s in all_statuses(event_type)}
    if "cancelled" in codes:
        return "cancelled"
    return "lost"


def status_codes(event_type: str) -> set[str]:
    return {s.code for s in all_statuses(event_type)}


def get_status(event_type: str, code: str) -> EventStatus:
    for s in all_statuses(event_type):
        if s.code == code:
            return s
    raise ValueError(
        f"unknown status {code!r} for event_type {event_type!r}"
    )
