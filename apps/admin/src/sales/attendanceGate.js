/**
 * Shared helpers for the Phase 7 Slice 2 attendance gate on the
 * frontend. The server-side dep is the actual enforcement; these
 * helpers turn the 403 into a legible message + a one-tap link to
 * the /clock screen.
 */

export function isAttendanceGateError(err) {
  return err?.response?.status === 403 &&
    err?.response?.data?.detail?.code === 'attendance_gate'
}

export function attendanceGateMessage() {
  return 'Clock in to start working the floor. The attendance gate blocks appointment changes until you punch in.'
}
