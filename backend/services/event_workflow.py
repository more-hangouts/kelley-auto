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


EVENT_WORKFLOWS: dict[str, tuple[EventStatus, ...]] = {
    "quinceanera": QUINCEANERA_STATUSES,
}


def all_statuses(event_type: str) -> tuple[EventStatus, ...]:
    if event_type not in EVENT_WORKFLOWS:
        raise ValueError(f"unknown event_type: {event_type!r}")
    return EVENT_WORKFLOWS[event_type]


def status_codes(event_type: str) -> set[str]:
    return {s.code for s in all_statuses(event_type)}


def get_status(event_type: str, code: str) -> EventStatus:
    for s in all_statuses(event_type):
        if s.code == code:
            return s
    raise ValueError(
        f"unknown status {code!r} for event_type {event_type!r}"
    )
