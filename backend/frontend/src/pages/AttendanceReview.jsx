import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'

import DownloadIcon from '@mui/icons-material/Download'

import {
  adjustAttendancePunch,
  adminClockOutPunch,
  clockEveryoneOut,
  confirmAttendancePunch,
  decideAttendanceCorrectionRequest,
  downloadAttendanceTotalsCsv,
  excuseScheduleEntry,
  getCronHealth,
  getHoursVariance,
  listAttendanceCorrectionRequests,
  listAttendancePunches,
  listAttendanceTotals,
  listFlaggedExceptions,
  listOpenSessions,
  listSalesStaff,
  resolveMissingOutPunch,
  setScheduleEntryNotes,
  voidAttendancePunch,
} from '../services/api'

// Owner attendance review (Phase 7 Slice 2B-2). Two surfaces share
// this component:
//   - The standalone `/reports/attendance` page mounts it with the
//     full filter row (range key + staff filter + review-queue toggle
//     + totals panel + correction-request queue).
//   - The "Today's punches" section embedded under
//     `/settings/sales-staff` mounts it with `mode='today_panel'` so
//     the owner sees today's activity right next to the PIN/lockout
//     management. The compact panel hides the totals chart and the
//     correction-request queue (those live on /reports/attendance).
//
// Design notes baked in from the user's Slice 2B-2 directive:
//   - Bounded reads from day one. The default range is "today"; the
//     widest preset is the current week. There is no "all time" UI.
//   - Both UTC and business-local timestamps are exposed in the API
//     response. We render the local string and tooltip the UTC ISO so
//     a payroll-style "what was this in UTC?" lookup is one hover away.
//   - Owner adjustments are append-only/audited. Adjust + void use
//     dialogs that require a reason; there is no inline delete.
//   - Correction approve/deny is its own action separate from manual
//     adjust. The correction queue surfaces both buttons with their
//     own confirmation dialogs.

const RANGE_OPTIONS = [
  { value: 'today', label: 'Today' },
  { value: 'current_week', label: 'This week' },
  { value: 'pay_period', label: 'Pay period' },
  { value: 'current_month', label: 'This month' },
  { value: 'last_month', label: 'Last month' },
  { value: 'current_quarter', label: 'This quarter' },
  { value: 'last_quarter', label: 'Last quarter' },
]

const BUCKET_OPTIONS = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'biweek', label: 'Biweek' },
  { value: 'month', label: 'Month' },
]

const REVIEW_STATUS_OPTIONS = [
  'late',
  'early_out',
  'unscheduled',
  'manual_adjusted',
  'void',
]

function formatLocalTimestamp(localIso) {
  if (!localIso) return ''
  try {
    const d = new Date(localIso)
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return localIso
  }
}

function reasonOrEmpty(text) {
  return (text || '').trim()
}

function formatOpenDuration(hoursFloat) {
  const totalMin = Math.max(0, Math.round((hoursFloat || 0) * 60))
  const h = Math.floor(totalMin / 60)
  const m = totalMin % 60
  if (h === 0) return `${m}m`
  return `${h}h ${m}m`
}

// "clock-in" / "clock-out" — the words an owner actually thinks in,
// instead of direction='in'/'out'.
function directionWord(direction) {
  return direction === 'in' ? 'clock-in' : 'clock-out'
}

// The handful of reasons an owner reaches for most when adjusting a
// punch, tailored to which punch they're editing. Tapping one fills
// the reason box; they can still edit it or type their own.
function quickReasons(direction) {
  if (direction === 'in') {
    return [
      'Forgot to clock in',
      'Arrived earlier than the system shows',
      'Clocked in at the wrong time',
    ]
  }
  return [
    'Forgot to clock out',
    'Left earlier than the system shows',
    'System closed it at the wrong time',
  ]
}

// Friendly long form for the dialogs: "Sat, Jun 13, 2026, 4:53 PM".
function formatLocalLong(localIso) {
  if (!localIso) return ''
  try {
    return new Date(localIso).toLocaleString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return localIso
  }
}

function punchStylistLabel(staff, userId) {
  const s = staff.find((x) => x.id === userId)
  return s ? s.full_name || s.username : `User #${userId}`
}

function statusChip(punch) {
  const s = punch.status
  let color = 'default'
  if (s === 'late' || s === 'unscheduled') color = 'warning'
  if (s === 'early_out') color = 'warning'
  if (s === 'manual_adjusted') color = 'info'
  if (s === 'void') color = 'error'
  if (s === 'recorded') color = 'success'
  return <Chip label={s} size="small" color={color} variant="outlined" />
}

function reviewBadge(punch) {
  const out = []
  if (punch.auto_closed) {
    out.push(
      <Chip
        key="auto"
        label={`Auto-closed (${punch.auto_close_reason || 'system'})`}
        size="small"
        color="warning"
      />,
    )
  }
  if (punch.hours_confirmation_status === 'needs_review') {
    out.push(
      <Chip
        key="needs"
        label="Needs review"
        size="small"
        color="warning"
        variant="outlined"
      />,
    )
  }
  if (punch.hours_confirmation_status === 'adjusted') {
    out.push(
      <Chip
        key="adj"
        label="Hours adjusted"
        size="small"
        color="info"
        variant="outlined"
      />,
    )
  }
  if (punch.hours_confirmation_status === 'confirmed') {
    out.push(
      <Chip
        key="conf"
        label="Confirmed"
        size="small"
        color="success"
        variant="outlined"
      />,
    )
  }
  return out
}

export default function AttendanceReview({ mode = 'full' } = {}) {
  const showTotals = mode === 'full'
  const showCorrections = mode === 'full'
  const showStaffFilter = mode === 'full'
  const showRangePicker = mode === 'full'

  const [rangeKey, setRangeKey] = useState('today')
  const [customFrom, setCustomFrom] = useState('')
  const [customTo, setCustomTo] = useState('')
  const [bucket, setBucket] = useState('day')
  const [staffUserId, setStaffUserId] = useState('all')
  const [reviewOnly, setReviewOnly] = useState(false)

  const [staff, setStaff] = useState([])
  const [punches, setPunches] = useState(null)
  const [totals, setTotals] = useState(null)
  const [totalsError, setTotalsError] = useState(null)
  const [corrections, setCorrections] = useState(null)
  const [cronHealth, setCronHealth] = useState(null)
  const [loadError, setLoadError] = useState(null)
  // Phase 10 Slice 2 — extra cards under the existing totals/cron blocks.
  const [flagged, setFlagged] = useState(null)
  const [variance, setVariance] = useState(null)
  const [scheduleNotesDraft, setScheduleNotesDraft] = useState({})
  // Phase 10 Slice 4 — resolve-missing-out dialog state.
  const [resolveDialog, setResolveDialog] = useState(null)

  const [adjustDialog, setAdjustDialog] = useState(null)
  const [voidDialog, setVoidDialog] = useState(null)
  const [decideDialog, setDecideDialog] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [csvBusy, setCsvBusy] = useState(false)
  // Currently-clocked-in sessions (not date-bounded) + bulk clock-out.
  const [openSessions, setOpenSessions] = useState(null)
  const [clockAllBusy, setClockAllBusy] = useState(false)
  const [clockAllDialog, setClockAllDialog] = useState(false)

  // Custom date range overrides the preset. Switching to a preset
  // clears the custom dates; typing a custom date clears the preset.
  const usingCustomRange = Boolean(customFrom && customTo)

  const params = useMemo(() => {
    const out = {}
    if (usingCustomRange) {
      out.from_date = customFrom
      out.to_date = customTo
    } else {
      out.range_key = rangeKey
    }
    if (staffUserId !== 'all') out.staff_user_id = staffUserId
    if (reviewOnly) out.review_queue_only = true
    return out
  }, [rangeKey, customFrom, customTo, usingCustomRange, staffUserId, reviewOnly])

  const totalsParams = useMemo(() => {
    const out = { bucket }
    if (usingCustomRange) {
      out.from_date = customFrom
      out.to_date = customTo
    } else {
      out.range_key = rangeKey
    }
    return out
  }, [rangeKey, customFrom, customTo, usingCustomRange, bucket])

  function handleSelectPreset(value) {
    if (!value) return
    setRangeKey(value)
    setCustomFrom('')
    setCustomTo('')
  }

  async function refresh() {
    setLoadError(null)
    setTotalsError(null)
    try {
      const totalsPromise = showTotals
        ? listAttendanceTotals(totalsParams).catch((err) => {
            const code = err?.response?.data?.detail?.code
            if (code === 'pay_period_anchor_missing') {
              setTotalsError(
                'Biweek bucketing needs a pay-period anchor. Set "Biweekly anchor" on the business profile, then try again.',
              )
            } else if (code === 'invalid_bucket') {
              setTotalsError('Unknown bucket selection.')
            } else if (code === 'invalid_range_key') {
              setTotalsError('Unknown range selection.')
            } else {
              setTotalsError("Couldn't load totals.")
            }
            return null
          })
        : Promise.resolve(null)
      const [
        punchesData,
        totalsData,
        correctionsData,
        staffRows,
        cronData,
        openData,
      ] = await Promise.all([
        listAttendancePunches(params),
        totalsPromise,
        showCorrections
          ? listAttendanceCorrectionRequests({ status: 'pending' })
          : Promise.resolve(null),
        // Fetched on first refresh regardless of mode — rows render
        // the stylist's name via this map even when the staff filter
        // dropdown is hidden in `today_panel` mode.
        staff.length === 0 ? listSalesStaff() : Promise.resolve(staff),
        showTotals ? getCronHealth() : Promise.resolve(null),
        // Who's on the clock right now — global, not windowed, so a
        // session opened days ago still surfaces. Non-fatal on failure.
        listOpenSessions().catch(() => null),
      ])
      setPunches(punchesData)
      setTotals(totalsData)
      setCorrections(correctionsData)
      setCronHealth(cronData)
      setOpenSessions(openData)
      if (Array.isArray(staffRows)) setStaff(staffRows)
      // Phase 10 Slice 2 cards (full mode only). The /punches response
      // reports the resolved from_date/to_date window the server picked
      // for the active range, so we feed the new endpoints the same
      // dates without re-deriving the range_key → date math.
      if (
        showTotals &&
        punchesData?.from_date &&
        punchesData?.to_date
      ) {
        const range = {
          from_date: punchesData.from_date,
          to_date: punchesData.to_date,
          user_id:
            staffUserId !== 'all' ? staffUserId : undefined,
        }
        try {
          const [flaggedResp, varianceResp] = await Promise.all([
            listFlaggedExceptions(range),
            getHoursVariance(range),
          ])
          setFlagged(flaggedResp)
          setVariance(varianceResp)
          const draftSeed = {}
          for (const row of flaggedResp.exceptions || []) {
            draftSeed[row.id] = row.manager_notes || ''
          }
          setScheduleNotesDraft(draftSeed)
        } catch {
          // Schedule-side cards are advisory — if they 500, the rest
          // of the page still loads.
          setFlagged({ exceptions: [] })
          setVariance({ rows: [] })
        }
      }
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      setLoadError(
        code === 'invalid_date_range'
          ? 'That date range is not valid.'
          : 'Could not load attendance data.',
      )
      setPunches({ punches: [], review_queue_count: 0 })
    }
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeKey, customFrom, customTo, bucket, staffUserId, reviewOnly])

  async function handleConfirm(punch) {
    setActionError(null)
    setBusyId(punch.id)
    try {
      await confirmAttendancePunch(punch.id)
      await refresh()
    } catch (err) {
      setActionError(
        err?.response?.data?.detail?.code === 'punch_not_in_review'
          ? 'This punch is not in review state.'
          : 'Could not confirm hours.',
      )
    } finally {
      setBusyId(null)
    }
  }

  async function handleClockOut(session) {
    setActionError(null)
    setBusyId(session.in_punch_id)
    try {
      await adminClockOutPunch(session.in_punch_id)
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      const who = session.full_name || session.username || 'That person'
      setActionError(
        code === 'not_currently_open'
          ? `${who} is already clocked out.`
          : 'Could not clock that person out.',
      )
    } finally {
      setBusyId(null)
    }
  }

  async function handleClockEveryoneOut() {
    setActionError(null)
    setClockAllBusy(true)
    try {
      await clockEveryoneOut()
      setClockAllDialog(false)
      await refresh()
    } catch {
      setActionError('Could not clock everyone out.')
    } finally {
      setClockAllBusy(false)
    }
  }

  async function submitAdjust() {
    if (!adjustDialog) return
    const reason = reasonOrEmpty(adjustDialog.reason)
    if (!reason) {
      setActionError('A reason is required for any adjustment.')
      return
    }
    setActionError(null)
    setBusyId(adjustDialog.punch.id)
    try {
      // datetime-local inputs come back as 'YYYY-MM-DDTHH:mm' in the
      // boutique's local clock (no timezone). Convert to a real Date
      // so the JSON body carries a proper ISO with offset.
      const dt = new Date(adjustDialog.value)
      if (Number.isNaN(dt.getTime())) {
        setActionError('Pick a valid date + time.')
        setBusyId(null)
        return
      }
      await adjustAttendancePunch(adjustDialog.punch.id, {
        new_punched_at: dt.toISOString(),
        reason,
      })
      setAdjustDialog(null)
      await refresh()
    } catch (err) {
      setActionError(
        err?.response?.data?.detail?.code === 'punch_void'
          ? 'This punch is voided. Restore it before adjusting.'
          : 'Could not save the adjustment.',
      )
    } finally {
      setBusyId(null)
    }
  }

  async function submitVoid() {
    if (!voidDialog) return
    const reason = reasonOrEmpty(voidDialog.reason)
    if (!reason) {
      setActionError('A reason is required to void a punch.')
      return
    }
    setActionError(null)
    setBusyId(voidDialog.punch.id)
    try {
      await voidAttendancePunch(voidDialog.punch.id, reason)
      setVoidDialog(null)
      await refresh()
    } catch {
      setActionError('Could not void the punch.')
    } finally {
      setBusyId(null)
    }
  }

  // ---- Phase 10 Slice 2 — flagged-exception inline actions. ----

  async function saveFlaggedNotes(entry) {
    setActionError(null)
    setBusyId(entry.id)
    try {
      await setScheduleEntryNotes(
        entry.id,
        scheduleNotesDraft[entry.id] || '',
      )
      await refresh()
    } catch {
      setActionError("Couldn't save the note.")
    } finally {
      setBusyId(null)
    }
  }

  async function excuseFlagged(entry) {
    setActionError(null)
    setBusyId(entry.id)
    try {
      await excuseScheduleEntry(
        entry.id,
        scheduleNotesDraft[entry.id] || '',
      )
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'entry_not_no_show') {
        setActionError('Only no-show shifts can be marked excused.')
      } else {
        setActionError("Couldn't excuse that shift.")
      }
    } finally {
      setBusyId(null)
    }
  }

  // Phase 10 Slice 4 — open the resolve-missing-out dialog with the
  // entry's scheduled end time pre-filled. Manager can adjust before
  // submitting.
  function openResolveDialog(entry) {
    const endDt = entry.ends_at_local
      ? new Date(entry.ends_at_local)
      : null
    let prefill = ''
    if (endDt && !Number.isNaN(endDt.getTime())) {
      const y = endDt.getFullYear()
      const m = String(endDt.getMonth() + 1).padStart(2, '0')
      const day = String(endDt.getDate()).padStart(2, '0')
      const hh = String(endDt.getHours()).padStart(2, '0')
      const mm = String(endDt.getMinutes()).padStart(2, '0')
      prefill = `${y}-${m}-${day}T${hh}:${mm}`
    }
    setResolveDialog({
      entry,
      outAtLocal: prefill,
      notes: scheduleNotesDraft[entry.id] || '',
    })
  }

  async function submitResolve() {
    if (!resolveDialog) return
    const local = resolveDialog.outAtLocal
    if (!local) {
      setActionError('Pick an out time to record.')
      return
    }
    const dt = new Date(local)
    if (Number.isNaN(dt.getTime())) {
      setActionError('Out time is not valid.')
      return
    }
    setActionError(null)
    setBusyId(resolveDialog.entry.id)
    try {
      await resolveMissingOutPunch(resolveDialog.entry.id, {
        out_at_local: dt.toISOString(),
        notes: resolveDialog.notes?.trim() || null,
      })
      setResolveDialog(null)
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'invalid_date_range') {
        setActionError('Out time must be after the original in punch.')
      } else if (code === 'entry_not_missing_out_punch') {
        setActionError(
          'That entry is no longer flagged as missing an out punch.',
        )
        await refresh()
      } else {
        setActionError("Couldn't record the out time.")
      }
    } finally {
      setBusyId(null)
    }
  }

  async function submitDecision() {
    if (!decideDialog) return
    setActionError(null)
    setBusyId(decideDialog.request.id)
    try {
      await decideAttendanceCorrectionRequest(decideDialog.request.id, {
        status: decideDialog.decision,
        decision_notes: reasonOrEmpty(decideDialog.notes) || null,
      })
      setDecideDialog(null)
      await refresh()
    } catch (err) {
      setActionError(
        err?.response?.data?.detail?.code === 'correction_request_not_pending'
          ? 'This request was already decided.'
          : 'Could not record the decision.',
      )
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Stack spacing={3}>
      {mode === 'full' && (
        <Box>
          <Typography variant="h4">Attendance review</Typography>
          <Typography variant="body2" color="text.secondary">
            Bounded by business-local date. All edits are append-only —
            adjustments and voids land in the audit log, never overwrite
            the original.
          </Typography>
        </Box>
      )}

      {loadError && <Alert severity="error">{loadError}</Alert>}
      {actionError && (
        <Alert severity="error" onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}

      {(showRangePicker || showStaffFilter) && (
        <Stack spacing={2}>
          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={2}
            alignItems={{ xs: 'stretch', sm: 'center' }}
            useFlexGap
            flexWrap="wrap"
          >
            {showRangePicker && (
              <ToggleButtonGroup
                value={usingCustomRange ? null : rangeKey}
                exclusive
                onChange={(_, val) => handleSelectPreset(val)}
                size="small"
              >
                {RANGE_OPTIONS.map((opt) => (
                  <ToggleButton key={opt.value} value={opt.value}>
                    {opt.label}
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>
            )}
            {showStaffFilter && (
              <FormControl size="small" sx={{ minWidth: 200 }}>
                <InputLabel id="staff-filter-label">Stylist</InputLabel>
                <Select
                  labelId="staff-filter-label"
                  label="Stylist"
                  value={staffUserId}
                  onChange={(e) => setStaffUserId(e.target.value)}
                >
                  <MenuItem value="all">All stylists</MenuItem>
                  {staff.map((s) => (
                    <MenuItem key={s.id} value={s.id}>
                      {s.full_name || s.username}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
            <ToggleButton
              value="review"
              selected={reviewOnly}
              onChange={() => setReviewOnly((v) => !v)}
              size="small"
            >
              Needs review only
              {punches?.review_queue_count != null &&
                ` (${punches.review_queue_count})`}
            </ToggleButton>
          </Stack>
          {showRangePicker && (
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={2}
              alignItems={{ xs: 'stretch', sm: 'center' }}
              useFlexGap
              flexWrap="wrap"
            >
              <TextField
                label="From"
                type="date"
                size="small"
                value={customFrom}
                onChange={(e) => setCustomFrom(e.target.value)}
                InputLabelProps={{ shrink: true }}
              />
              <TextField
                label="To"
                type="date"
                size="small"
                value={customTo}
                onChange={(e) => setCustomTo(e.target.value)}
                InputLabelProps={{ shrink: true }}
              />
              {usingCustomRange && (
                <Button
                  size="small"
                  onClick={() => {
                    setCustomFrom('')
                    setCustomTo('')
                  }}
                >
                  Clear dates
                </Button>
              )}
            </Stack>
          )}
        </Stack>
      )}

      {openSessions && openSessions.open_sessions.length > 0 && (
        <Card variant={mode === 'today_panel' ? 'outlined' : 'elevation'}>
          <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              spacing={1}
              flexWrap="wrap"
              useFlexGap
              sx={{ mb: 2 }}
            >
              <Box>
                <Typography variant="h6">On the clock now</Typography>
                <Typography variant="body2" color="text.secondary">
                  {openSessions.open_sessions.length} currently clocked in.
                  Clocking out records an out punch at the current time and
                  flags the hours for review so you can adjust if needed.
                </Typography>
              </Box>
              <Button
                variant="outlined"
                color="error"
                disabled={clockAllBusy}
                onClick={() => setClockAllDialog(true)}
              >
                {clockAllBusy ? (
                  <CircularProgress size={20} />
                ) : (
                  'Clock everyone out'
                )}
              </Button>
            </Stack>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Stylist</TableCell>
                    <TableCell>Clocked in</TableCell>
                    <TableCell>Open for</TableCell>
                    <TableCell align="right">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {openSessions.open_sessions.map((s) => (
                    <TableRow key={s.in_punch_id} hover>
                      <TableCell>
                        {s.full_name || s.username || `User #${s.user_id}`}
                      </TableCell>
                      <Tooltip title={`UTC: ${s.punched_at}`} placement="top">
                        <TableCell>
                          {formatLocalTimestamp(s.punched_at_local)}
                        </TableCell>
                      </Tooltip>
                      <TableCell>{formatOpenDuration(s.hours_open)}</TableCell>
                      <TableCell align="right">
                        <Button
                          size="small"
                          color="error"
                          disabled={busyId === s.in_punch_id}
                          onClick={() => handleClockOut(s)}
                        >
                          {busyId === s.in_punch_id ? (
                            <CircularProgress size={16} />
                          ) : (
                            'Clock out'
                          )}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </CardContent>
        </Card>
      )}

      <Dialog
        open={clockAllDialog}
        onClose={() => !clockAllBusy && setClockAllDialog(false)}
      >
        <DialogTitle>Clock everyone out?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This records an out punch at the current time for the{' '}
            {openSessions?.open_sessions?.length || 0} people on the clock
            right now. The hours land in the review queue so you can adjust
            any that look off. Nothing is deleted and each punch can be
            corrected individually.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => setClockAllDialog(false)}
            disabled={clockAllBusy}
          >
            Cancel
          </Button>
          <Button
            color="error"
            variant="contained"
            onClick={handleClockEveryoneOut}
            disabled={clockAllBusy}
          >
            {clockAllBusy ? (
              <CircularProgress size={20} />
            ) : (
              'Clock everyone out'
            )}
          </Button>
        </DialogActions>
      </Dialog>

      <Card variant={mode === 'today_panel' ? 'outlined' : 'elevation'}>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            sx={{ mb: 2 }}
          >
            <Typography variant="h6">
              {mode === 'today_panel' ? "Today's punches" : 'Punches'}
            </Typography>
            {punches?.from_date && punches?.to_date && (
              <Typography variant="caption" color="text.secondary">
                {punches.from_date === punches.to_date
                  ? punches.from_date
                  : `${punches.from_date} → ${punches.to_date}`}
                {punches.timezone ? ` (${punches.timezone})` : ''}
              </Typography>
            )}
          </Stack>

          {punches === null ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={28} />
            </Box>
          ) : punches.punches.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No punches in this window.
            </Typography>
          ) : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Stylist</TableCell>
                    <TableCell>Direction</TableCell>
                    <TableCell>Local time</TableCell>
                    <TableCell>Date</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell>Flags</TableCell>
                    <TableCell align="right">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {punches.punches.map((p) => {
                    const stylistRow = staff.find(
                      (s) => s.id === p.user_id,
                    )
                    const stylistLabel = stylistRow
                      ? stylistRow.full_name || stylistRow.username
                      : `User #${p.user_id}`
                    const reviewable =
                      p.auto_closed ||
                      p.hours_confirmation_status === 'needs_review' ||
                      p.hours_confirmation_status === 'adjusted'
                    return (
                      <TableRow key={p.id} hover>
                        <TableCell>{stylistLabel}</TableCell>
                        <TableCell>
                          <Chip
                            label={p.direction === 'in' ? 'In' : 'Out'}
                            size="small"
                            color={p.direction === 'in' ? 'primary' : 'default'}
                            variant={
                              p.direction === 'in' ? 'filled' : 'outlined'
                            }
                          />
                        </TableCell>
                        <Tooltip title={`UTC: ${p.punched_at}`} placement="top">
                          <TableCell>
                            {formatLocalTimestamp(p.punched_at_local)}
                          </TableCell>
                        </Tooltip>
                        <TableCell>{p.business_date}</TableCell>
                        <TableCell>{statusChip(p)}</TableCell>
                        <TableCell>
                          <Stack direction="row" spacing={0.5} flexWrap="wrap">
                            {reviewBadge(p)}
                          </Stack>
                        </TableCell>
                        <TableCell align="right">
                          <Stack
                            direction="row"
                            spacing={0.5}
                            justifyContent="flex-end"
                          >
                            {reviewable && (
                              <Button
                                size="small"
                                disabled={busyId === p.id}
                                onClick={() => handleConfirm(p)}
                              >
                                Confirm
                              </Button>
                            )}
                            <Button
                              size="small"
                              disabled={busyId === p.id || p.status === 'void'}
                              onClick={() =>
                                setAdjustDialog({
                                  punch: p,
                                  value: p.punched_at_local.slice(0, 16),
                                  reason: '',
                                })
                              }
                            >
                              Adjust
                            </Button>
                            {p.status !== 'void' && (
                              <Button
                                size="small"
                                color="error"
                                disabled={busyId === p.id}
                                onClick={() =>
                                  setVoidDialog({ punch: p, reason: '' })
                                }
                              >
                                Void
                              </Button>
                            )}
                          </Stack>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </CardContent>
      </Card>

      {showTotals && (
        <Card>
          <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              justifyContent="space-between"
              alignItems={{ xs: 'flex-start', sm: 'center' }}
              spacing={1}
              sx={{ mb: 2 }}
            >
              <Typography variant="h6">Hours by stylist</Typography>
              <Stack
                direction="row"
                spacing={1}
                alignItems="center"
                useFlexGap
                flexWrap="wrap"
              >
                <ToggleButtonGroup
                  value={bucket}
                  exclusive
                  onChange={(_, val) => val && setBucket(val)}
                  size="small"
                >
                  {BUCKET_OPTIONS.map((opt) => (
                    <ToggleButton key={opt.value} value={opt.value}>
                      {opt.label}
                    </ToggleButton>
                  ))}
                </ToggleButtonGroup>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<DownloadIcon />}
                  disabled={csvBusy || totals === null}
                  onClick={async () => {
                    setCsvBusy(true)
                    try {
                      await downloadAttendanceTotalsCsv(totalsParams)
                    } catch {
                      setActionError("Couldn't download the CSV.")
                    } finally {
                      setCsvBusy(false)
                    }
                  }}
                >
                  {csvBusy ? 'Preparing…' : 'Export CSV'}
                </Button>
              </Stack>
            </Stack>
            {totalsError && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                {totalsError}
              </Alert>
            )}
            {totals === null && !totalsError ? (
              <CircularProgress size={20} />
            ) : totals && totals.totals.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No paired sessions in this window.
              </Typography>
            ) : totals ? (
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Stylist</TableCell>
                    <TableCell align="right">Total hours</TableCell>
                    <TableCell>
                      {bucket === 'day'
                        ? 'By day'
                        : bucket === 'week'
                          ? 'By ISO week'
                          : bucket === 'biweek'
                            ? 'By pay period'
                            : 'By month'}
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {totals.totals.map((row) => (
                    <TableRow key={row.user_id}>
                      <TableCell>
                        {row.full_name || row.username || `User #${row.user_id}`}
                      </TableCell>
                      <TableCell align="right">{row.total_hours.toFixed(2)}</TableCell>
                      <TableCell>
                        <Stack direction="row" spacing={1} flexWrap="wrap">
                          {row.by_bucket.map((entry) => (
                            <Chip
                              key={entry.bucket_key}
                              label={`${entry.bucket_key}: ${entry.hours.toFixed(2)}h`}
                              size="small"
                              variant="outlined"
                            />
                          ))}
                        </Stack>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : null}
          </CardContent>
        </Card>
      )}

      {showTotals && cronHealth && (
        <Card>
          <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
            <Typography variant="h6" sx={{ mb: 1 }}>
              Cron health
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: 'block', mb: 2 }}
            >
              Auto-close, pre-close reminder, and selfie retention each
              run once a day. A "stale" flag means the cron hasn't
              completed in over{' '}
              {Math.round(cronHealth.stale_after_seconds / 86400)} days.
            </Typography>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Cron</TableCell>
                  <TableCell>Last finished</TableCell>
                  <TableCell align="right">Scanned</TableCell>
                  <TableCell align="right">Changed</TableCell>
                  <TableCell>Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {cronHealth.crons.map((c) => (
                  <TableRow key={c.name}>
                    <TableCell>{c.name}</TableCell>
                    <TableCell>
                      {c.last_finished_at
                        ? formatLocalTimestamp(c.last_finished_at)
                        : 'never'}
                    </TableCell>
                    <TableCell align="right">{c.last_scanned_count}</TableCell>
                    <TableCell align="right">{c.last_changed_count}</TableCell>
                    <TableCell>
                      {c.ok ? (
                        <Chip label="OK" size="small" color="success" />
                      ) : c.last_error ? (
                        <Tooltip title={c.last_error}>
                          <Chip
                            label={`Error (×${c.consecutive_failures})`}
                            size="small"
                            color="error"
                          />
                        </Tooltip>
                      ) : c.is_stale ? (
                        <Chip label="Stale" size="small" color="warning" />
                      ) : (
                        <Chip label="Pending" size="small" />
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {showTotals && flagged && (
        <Card>
          <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              sx={{ mb: 2 }}
            >
              <Typography variant="h6">Flagged exceptions</Typography>
              <Typography variant="caption" color="text.secondary">
                Published shifts the schedule crons flagged — either no
                clock-in inside the grace window (no_show) or a
                clocked-in session that never clocked out
                (missing_out_punch).
              </Typography>
            </Stack>
            {flagged.exceptions.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No missed shifts in this window.
              </Typography>
            ) : (
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Stylist</TableCell>
                    <TableCell>Date</TableCell>
                    <TableCell>Shift</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell>Manager notes</TableCell>
                    <TableCell align="right">Action</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {flagged.exceptions.map((row) => {
                    const isMissingOut =
                      row.attendance_status === 'missing_out_punch'
                    return (
                      <TableRow key={row.id}>
                        <TableCell>
                          {row.user_full_name ||
                            row.user_username ||
                            `User #${row.user_id}`}
                        </TableCell>
                        <TableCell>{row.business_date}</TableCell>
                        <TableCell>
                          {formatLocalTimestamp(row.starts_at_local)}
                          {' to '}
                          {formatLocalTimestamp(row.ends_at_local)}
                        </TableCell>
                        <TableCell>
                          <Chip
                            label={
                              isMissingOut ? 'Missing out' : 'No show'
                            }
                            size="small"
                            color={isMissingOut ? 'warning' : 'error'}
                            variant="outlined"
                          />
                        </TableCell>
                        <TableCell sx={{ minWidth: 220 }}>
                          <TextField
                            size="small"
                            fullWidth
                            placeholder={
                              isMissingOut
                                ? 'e.g. left at 5pm, forgot to clock out'
                                : 'e.g. called out sick'
                            }
                            value={scheduleNotesDraft[row.id] ?? ''}
                            onChange={(e) =>
                              setScheduleNotesDraft((draft) => ({
                                ...draft,
                                [row.id]: e.target.value,
                              }))
                            }
                          />
                        </TableCell>
                        <TableCell align="right">
                          <Stack
                            direction="row"
                            spacing={0.5}
                            justifyContent="flex-end"
                          >
                            <Button
                              size="small"
                              disabled={busyId === row.id}
                              onClick={() => saveFlaggedNotes(row)}
                            >
                              Save note
                            </Button>
                            {isMissingOut ? (
                              <Button
                                size="small"
                                variant="contained"
                                color="warning"
                                disabled={busyId === row.id}
                                onClick={() => openResolveDialog(row)}
                              >
                                Enter out time
                              </Button>
                            ) : (
                              <Button
                                size="small"
                                variant="outlined"
                                color="warning"
                                disabled={busyId === row.id}
                                onClick={() => excuseFlagged(row)}
                              >
                                Mark excused
                              </Button>
                            )}
                          </Stack>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {/* Resolve-missing-out dialog */}
      <Dialog
        open={resolveDialog !== null}
        onClose={() => setResolveDialog(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Enter missing out time</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            The stylist clocked in but never clocked out. Enter the
            time they actually left so the schedule reflects worked
            hours. A paired out-punch is inserted and the entry's
            attendance status updates to present (or late if the
            original clock-in was past grace).
          </DialogContentText>
          <Stack spacing={2}>
            <TextField
              label="Out time (boutique-local)"
              type="datetime-local"
              value={resolveDialog?.outAtLocal || ''}
              onChange={(e) =>
                setResolveDialog((d) => ({
                  ...d,
                  outAtLocal: e.target.value,
                }))
              }
              InputLabelProps={{ shrink: true }}
              fullWidth
            />
            <TextField
              label="Notes (optional)"
              value={resolveDialog?.notes || ''}
              onChange={(e) =>
                setResolveDialog((d) => ({
                  ...d,
                  notes: e.target.value,
                }))
              }
              multiline
              minRows={2}
              fullWidth
              helperText="Appended to the entry's manager notes for the audit trail."
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setResolveDialog(null)}>Cancel</Button>
          <Button
            variant="contained"
            color="warning"
            onClick={submitResolve}
            disabled={busyId === resolveDialog?.entry?.id}
          >
            Record out time
          </Button>
        </DialogActions>
      </Dialog>

      {showTotals && variance && (
        <Card>
          <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              sx={{ mb: 2 }}
            >
              <Typography variant="h6">
                Scheduled vs actual hours
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Published shifts only. Variance is actual − scheduled.
              </Typography>
            </Stack>
            {variance.rows.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No published shifts in this window.
              </Typography>
            ) : (
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Stylist</TableCell>
                    <TableCell align="right">Scheduled</TableCell>
                    <TableCell align="right">Actual</TableCell>
                    <TableCell align="right">Variance</TableCell>
                    <TableCell align="right">Shifts</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {variance.rows.map((row) => (
                    <TableRow key={row.user_id}>
                      <TableCell>
                        {row.full_name ||
                          row.username ||
                          `User #${row.user_id}`}
                      </TableCell>
                      <TableCell align="right">
                        {row.scheduled_hours.toFixed(2)}h
                      </TableCell>
                      <TableCell align="right">
                        {row.actual_hours.toFixed(2)}h
                      </TableCell>
                      <TableCell align="right">
                        <Chip
                          label={`${row.variance_hours >= 0 ? '+' : ''}${row.variance_hours.toFixed(2)}h`}
                          size="small"
                          color={
                            Math.abs(row.variance_hours) < 0.25
                              ? 'success'
                              : row.variance_hours < 0
                                ? 'error'
                                : 'warning'
                          }
                          variant="outlined"
                        />
                      </TableCell>
                      <TableCell align="right">
                        {row.stamped_pairs}/{row.entry_count}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {showCorrections && (
        <Card>
          <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              sx={{ mb: 2 }}
            >
              <Typography variant="h6">Correction requests</Typography>
              <Typography variant="caption" color="text.secondary">
                Pending only. Approving applies the proposed time and
                writes an audit row.
              </Typography>
            </Stack>
            {corrections === null ? (
              <CircularProgress size={20} />
            ) : corrections.correction_requests.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No pending requests.
              </Typography>
            ) : (
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Stylist</TableCell>
                    <TableCell>Submitted</TableCell>
                    <TableCell>Proposed in</TableCell>
                    <TableCell>Proposed out</TableCell>
                    <TableCell>Reason</TableCell>
                    <TableCell align="right">Decision</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {corrections.correction_requests.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell>
                        {r.user_full_name || r.user_username || `User #${r.user_id}`}
                      </TableCell>
                      <TableCell>{formatLocalTimestamp(r.created_at)}</TableCell>
                      <TableCell>
                        {r.requested_check_in_at_local
                          ? formatLocalTimestamp(r.requested_check_in_at_local)
                          : '—'}
                      </TableCell>
                      <TableCell>
                        {r.requested_check_out_at_local
                          ? formatLocalTimestamp(r.requested_check_out_at_local)
                          : '—'}
                      </TableCell>
                      <TableCell>{r.reason}</TableCell>
                      <TableCell align="right">
                        <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                          <Button
                            size="small"
                            variant="contained"
                            color="primary"
                            disabled={busyId === r.id}
                            onClick={() =>
                              setDecideDialog({
                                request: r,
                                decision: 'approved',
                                notes: '',
                              })
                            }
                          >
                            Approve
                          </Button>
                          <Button
                            size="small"
                            color="error"
                            disabled={busyId === r.id}
                            onClick={() =>
                              setDecideDialog({
                                request: r,
                                decision: 'denied',
                                notes: '',
                              })
                            }
                          >
                            Deny
                          </Button>
                        </Stack>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {/* Adjust dialog */}
      <Dialog
        open={adjustDialog !== null}
        onClose={() => setAdjustDialog(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {adjustDialog?.punch
            ? `Adjust ${punchStylistLabel(
                staff,
                adjustDialog.punch.user_id,
              )}'s ${directionWord(adjustDialog.punch.direction)} time`
            : 'Adjust punch time'}
        </DialogTitle>
        <DialogContent>
          {adjustDialog?.punch && (
            <DialogContentText sx={{ mb: 2 }}>
              Change the time {punchStylistLabel(
                staff,
                adjustDialog.punch.user_id,
              )}{' '}
              clocked {adjustDialog.punch.direction === 'in' ? 'in' : 'out'}.
              Clock-in and clock-out are separate punches, so this only
              moves the {directionWord(adjustDialog.punch.direction)}. The
              original time is kept in the history.
            </DialogContentText>
          )}
          <Stack spacing={2}>
            <TextField
              label={
                adjustDialog?.punch
                  ? `New ${directionWord(adjustDialog.punch.direction)} time`
                  : 'New time'
              }
              type="datetime-local"
              value={adjustDialog?.value || ''}
              onChange={(e) =>
                setAdjustDialog((d) => ({ ...d, value: e.target.value }))
              }
              InputLabelProps={{ shrink: true }}
              helperText="Boutique local time"
              fullWidth
            />
            {adjustDialog?.punch && (
              <Box>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: 'block', mb: 0.75 }}
                >
                  Common reasons (tap to fill)
                </Typography>
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                  {quickReasons(adjustDialog.punch.direction).map((r) => (
                    <Chip
                      key={r}
                      label={r}
                      size="small"
                      variant={adjustDialog?.reason === r ? 'filled' : 'outlined'}
                      color={adjustDialog?.reason === r ? 'primary' : 'default'}
                      onClick={() =>
                        setAdjustDialog((d) => ({ ...d, reason: r }))
                      }
                    />
                  ))}
                </Stack>
              </Box>
            )}
            <TextField
              label="Reason for the change"
              placeholder="e.g. She forgot to clock out; left at 5 PM"
              value={adjustDialog?.reason || ''}
              onChange={(e) =>
                setAdjustDialog((d) => ({ ...d, reason: e.target.value }))
              }
              required
              multiline
              minRows={2}
              fullWidth
            />
            {adjustDialog?.punch && (
              <Typography variant="body2" color="text.secondary">
                Currently set to{' '}
                {formatLocalLong(adjustDialog.punch.punched_at_local)}.
              </Typography>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAdjustDialog(null)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={submitAdjust}
            disabled={busyId === adjustDialog?.punch?.id}
          >
            Save adjustment
          </Button>
        </DialogActions>
      </Dialog>

      {/* Void dialog */}
      <Dialog
        open={voidDialog !== null}
        onClose={() => setVoidDialog(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {voidDialog?.punch
            ? `Void ${punchStylistLabel(
                staff,
                voidDialog.punch.user_id,
              )}'s ${directionWord(voidDialog.punch.direction)}?`
            : 'Void this punch?'}
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            {voidDialog?.punch ? (
              <>
                This removes the {formatLocalLong(
                  voidDialog.punch.punched_at_local,
                )}{' '}
                {directionWord(voidDialog.punch.direction)} from hours
                totals. It does not delete anything and the action is kept
                in the history.
              </>
            ) : (
              'Voiding does not delete the row. It marks the punch as void so totals exclude it. The action is recorded in the history.'
            )}
          </DialogContentText>
          <TextField
            label="Reason"
            value={voidDialog?.reason || ''}
            onChange={(e) =>
              setVoidDialog((d) => ({ ...d, reason: e.target.value }))
            }
            required
            multiline
            minRows={2}
            fullWidth
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setVoidDialog(null)}>Cancel</Button>
          <Button
            color="error"
            variant="contained"
            onClick={submitVoid}
            disabled={busyId === voidDialog?.punch?.id}
          >
            Void
          </Button>
        </DialogActions>
      </Dialog>

      {/* Decide correction dialog */}
      <Dialog
        open={decideDialog !== null}
        onClose={() => setDecideDialog(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {decideDialog?.decision === 'approved'
            ? 'Approve correction request?'
            : 'Deny correction request?'}
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            {decideDialog?.decision === 'approved'
              ? "Approving will apply the proposed time to the linked punch (if any) and write an audit row. The stylist's request stays on the timeline so the change is traceable."
              : 'Denying records the decision and leaves the punch unchanged.'}
          </DialogContentText>
          <TextField
            label="Decision notes (optional)"
            value={decideDialog?.notes || ''}
            onChange={(e) =>
              setDecideDialog((d) => ({ ...d, notes: e.target.value }))
            }
            multiline
            minRows={2}
            fullWidth
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDecideDialog(null)}>Cancel</Button>
          <Button
            variant="contained"
            color={decideDialog?.decision === 'approved' ? 'primary' : 'error'}
            onClick={submitDecision}
            disabled={busyId === decideDialog?.request?.id}
          >
            {decideDialog?.decision === 'approved' ? 'Approve' : 'Deny'}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
