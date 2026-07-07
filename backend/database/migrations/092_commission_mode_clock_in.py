"""Commission-mode clock-in (Phase 14.1).

Sales reps are 100% commission, so clock-in is an "active in app" signal,
not GPS/payroll attendance. This migration makes that a first-class mode:

  - ``business_profile.attendance_mode VARCHAR(20) DEFAULT 'payroll'`` — the
    switch. ``'payroll'`` preserves the fully-enforced geofence/selfie
    behavior for any future hourly mode (and for a fresh install). In
    ``'commission'`` the clock-in handler accepts an app-session punch with
    no GPS fix and never blocks on geofence proximity (see
    ``services/clock_in.py``). CHECK pins the closed set.

  - Extend ``chk_staff_punches_accepted_by`` with ``'app_session'`` — the
    ``accepted_by`` value stamped on a commission punch that was accepted as
    an active-app signal rather than by GPS/trusted-network location proof.
    Keeps the existing three values valid.

  - Flip the singleton ``business_profile`` row to ``'commission'``. This is
    the product activation for Kelley: reps clock in without GPS starting at
    deploy. Reversible — set the column back to ``'payroll'`` to restore
    strict geofencing. (Selfie is not mutated here; the handler downgrades a
    ``'required'`` policy to ``'optional'`` at runtime while in commission
    mode so it never blocks the punch, leaving the owner's stored setting
    intact for a future switch back to payroll.)
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE business_profile
                ADD COLUMN attendance_mode VARCHAR(20)
                    NOT NULL DEFAULT 'payroll'
            """
        )
    )
    connection.execute(
        text(
            """
            ALTER TABLE business_profile
                ADD CONSTRAINT chk_business_profile_attendance_mode
                CHECK (attendance_mode IN ('payroll', 'commission'))
            """
        )
    )

    # Widen the accepted_by closed set (added strict in migration 075,
    # extended for trusted_network in 076) to include the commission
    # active-app-session acceptance.
    connection.execute(
        text("ALTER TABLE staff_punches DROP CONSTRAINT chk_staff_punches_accepted_by")
    )
    connection.execute(
        text(
            """
            ALTER TABLE staff_punches
                ADD CONSTRAINT chk_staff_punches_accepted_by
                CHECK (accepted_by IN (
                    'gps',
                    'gps_with_accuracy_buffer',
                    'trusted_network',
                    'app_session'
                ))
            """
        )
    )

    # Product activation: Kelley reps are commission today. The column
    # defaults to 'payroll' for schema-preservation, so flip the existing
    # singleton row explicitly.
    connection.execute(
        text("UPDATE business_profile SET attendance_mode = 'commission'")
    )
