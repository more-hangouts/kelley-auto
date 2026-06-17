"""Guardrail smoke for the notification kind naming boundary.

Three name spaces coexist in this codebase (see
[docs/STAFF_EMAIL_BUILD_TRACKER.md#notification-kind-naming-boundary](
../docs/STAFF_EMAIL_BUILD_TRACKER.md#notification-kind-naming-boundary)
for the full doctrine):

  1. Event-bus dotted kinds used with ``record_event`` — registered in
     ``TIMING_MODE`` / ``INTRINSIC_TARGETING`` / ``STAFF_EMAIL_RENDERERS``.
  2. Legacy snake_case enqueue keys used by ``enqueue_for_*`` helpers
     (``booking_confirmation``, ``reminder``, etc.) — template
     identifiers, not event-bus kinds. They write directly to
     ``notification_jobs.kind`` and own delivery end-to-end.
  3. Customer-facing portal kinds dispatched via ``services/portal_email``.

This smoke is the trip-wire for accidentally collapsing those spaces:

  - Adding a renderer for a dotted kind without also adding it to
    ``TIMING_MODE`` would leave ``record_event`` to fall through to the
    default real-time mode and miss the registry; the test catches that.
  - Adding ``"real_time"`` timing for a dotted kind that has an
    intrinsic recipient OR a role-default subscriber but no renderer
    would result in ``record_event`` computing recipients, logging a
    warning, and silently dropping the email; the test catches that
    too (with a documented allowlist for the one known
    forward-looking exception).
  - Reusing one of the snake_case legacy keys as an event-bus kind
    would silently route legacy direct-enqueue jobs through the staff
    event bus dispatcher — wrong renderer, wrong recipients.

Pure-Python assertions on registry contents. No DB connection, no
rate limiter, no transport. Safe to run anywhere.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# The notification modules import config which reads env at import time.
# Set minimum-viable defaults so the import succeeds without a real .env.
os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from services.notification_routing import (  # noqa: E402
    INTRINSIC_TARGETING,
    ROLE_DEFAULTS,
    TIMING_MODE,
)
from services.notification_service import STAFF_EMAIL_RENDERERS  # noqa: E402


# Legacy enqueue keys used by services.notification_service.enqueue_for_*
# helpers. Hard-coded here rather than imported because the helpers don't
# export them as a named constant — that's part of the boundary: these
# strings are an implementation detail of the legacy senders, not a
# shared vocabulary.
LEGACY_ENQUEUE_KEYS: frozenset[str] = frozenset(
    {
        "booking_confirmation",
        "internal_new_booking",
        "enrichment_invitation",
        "reminder",
        "reschedule_confirmation",
        "cancellation_confirmation",
    }
)


# Kinds that intentionally have role-default subscribers but no renderer
# yet because the legacy direct-enqueue path is the canonical sender.
# Each entry needs a written reason in the comment below — the allowlist
# documents technical debt, not "future drift is fine."
KNOWN_FORWARD_LOOKING: frozenset[str] = frozenset(
    {
        # admin.new_booking: TIMING_MODE='real_time' and admin is a
        # role-default subscriber, but no renderer is registered under
        # this name. The legacy services.notification_service.enqueue_for_new_booking
        # path uses kind="internal_new_booking" instead, and the renderer
        # for the admin "new booking" email is rendered indirectly via
        # that legacy enqueue. Drop this entry when the path migrates to
        # record_event.
        "admin.new_booking",
    }
)


REAL_TIME_MODES: frozenset[str] = frozenset({"real_time", "real_time_and_digest"})


def _routed_kinds() -> set[str]:
    """Kinds that, when emitted, would compute at least one recipient
    via intrinsic targeting or role defaults. These are the kinds the
    dispatcher will try to fan out — and therefore the ones that need a
    renderer to actually deliver email."""
    routed = set(INTRINSIC_TARGETING)
    for role_subs in ROLE_DEFAULTS.values():
        for kind, enabled in role_subs.items():
            if enabled:
                routed.add(kind)
    return routed


def check_intrinsic_targeting_subset_of_timing_mode() -> None:
    extra = set(INTRINSIC_TARGETING) - set(TIMING_MODE)
    assert not extra, (
        "INTRINSIC_TARGETING contains kinds not in TIMING_MODE: "
        f"{sorted(extra)}. Add to TIMING_MODE with the correct timing "
        "or remove from INTRINSIC_TARGETING."
    )


def check_renderers_subset_of_timing_mode() -> None:
    extra = set(STAFF_EMAIL_RENDERERS) - set(TIMING_MODE)
    assert not extra, (
        "STAFF_EMAIL_RENDERERS contains kinds not in TIMING_MODE: "
        f"{sorted(extra)}. Add to TIMING_MODE so the dispatcher knows "
        "how to handle the kind, or remove the orphaned renderer."
    )


def check_routed_real_time_kinds_have_renderers() -> None:
    """A kind that the dispatcher will try to fan out (because it has an
    intrinsic recipient or a role-default subscriber) and that is
    real-time MUST have a renderer registered. Otherwise record_event
    logs a warning and silently drops the email."""
    routed = _routed_kinds()
    missing: list[str] = []
    for kind in sorted(routed):
        timing = TIMING_MODE.get(kind, "real_time")
        if timing in REAL_TIME_MODES and kind not in STAFF_EMAIL_RENDERERS:
            missing.append(kind)
    unexpected = set(missing) - KNOWN_FORWARD_LOOKING
    assert not unexpected, (
        "real-time kinds with computed recipients but no renderer: "
        f"{sorted(unexpected)}. Add a renderer in "
        "services.notification_service.STAFF_EMAIL_RENDERERS, or change "
        "the timing in TIMING_MODE, or — if this is a documented "
        "legacy-bridge case — add the kind to KNOWN_FORWARD_LOOKING in "
        "this test WITH a written reason."
    )


def check_legacy_enqueue_keys_not_in_timing_mode() -> None:
    """Snake_case legacy keys must not collide with the event-bus
    namespace. If they did, the dispatcher would treat a legacy direct
    enqueue as an event-bus kind and route it through the wrong path."""
    collisions = LEGACY_ENQUEUE_KEYS & set(TIMING_MODE)
    assert not collisions, (
        "legacy enqueue keys leaked into TIMING_MODE: "
        f"{sorted(collisions)}. The legacy keys are template identifiers "
        "owned by services.notification_service.enqueue_for_* and must "
        "stay out of the event-bus registry. If you're migrating one of "
        "these to record_event, rename to the dotted form first."
    )


def main() -> None:
    check_intrinsic_targeting_subset_of_timing_mode()
    check_renderers_subset_of_timing_mode()
    check_routed_real_time_kinds_have_renderers()
    check_legacy_enqueue_keys_not_in_timing_mode()
    print("notification_kind_naming_boundary smoke ok")


if __name__ == "__main__":
    main()
