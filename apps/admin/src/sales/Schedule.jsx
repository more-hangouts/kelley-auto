import { useCallback, useEffect, useMemo, useState } from 'react'
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
  DialogTitle,
  Divider,
  IconButton,
  MenuItem,
  Snackbar,
  Stack,
  Tab,
  Tabs,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'

import { useSalesAuth } from '../contexts/SalesAuthContext'
import {
  salesAcceptShiftRequest,
  salesCancelShiftRequest,
  salesClaimOpenShift,
  salesCreateAvailability,
  salesCreateShiftRequest,
  salesDeclineShiftRequest,
  salesDeleteAvailability,
  salesGetSchedule,
  salesGetTeamSchedule,
  salesListMyAvailability,
  salesListMyShiftRequests,
  salesListOpenShifts,
} from '../services/api'

const WEEKDAY_LABELS = {
  1: 'Mon',
  2: 'Tue',
  3: 'Wed',
  4: 'Thu',
  5: 'Fri',
  6: 'Sat',
  7: 'Sun',
}

function formatTimeRange(start, end) {
  return `${start} to ${end}`
}

// Stylist /schedule (Phase 8 Slice D + Phase 10 Slice 5). Two-week
// view with a "This week" / "Next week" toggle. Now also includes a
// top-level tab switch:
//
//   - **My schedule** (default) — resolved per-day shift cards for
//     the logged-in stylist. Uses /api/sales/schedule, which already
//     suppresses approved time-off.
//   - **Team schedule** — published shifts for every active coworker
//     in the same week, grouped by day. Uses /api/sales/schedule/team
//     with the privacy-bounded payload (names + times only). The
//     viewer's own rows render with a "You" chip; other stylists'
//     rows expose disabled "Request cover" / "Request swap" buttons
//     as groundwork for the cover/swap-request flow that will land
//     in a later slice.

function startOfWeek(d) {
  const day = d.getDay() // 0=Sun, 1=Mon, ..., 6=Sat
  const offset = day === 0 ? 6 : day - 1
  const monday = new Date(d)
  monday.setDate(d.getDate() - offset)
  monday.setHours(0, 0, 0, 0)
  return monday
}

function isoDate(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function addDays(d, n) {
  const out = new Date(d)
  out.setDate(d.getDate() + n)
  return out
}

function formatLocalTimeOnly(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString(undefined, {
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function formatDayHeader(isoDay) {
  const [y, m, d] = isoDay.split('-').map(Number)
  const dt = new Date(y, m - 1, d)
  return dt.toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })
}

export default function Schedule() {
  const [view, setView] = useState('mine') // 'mine' | 'team'
  const [weekOffset, setWeekOffset] = useState(0) // 0 = this week, 7 = next
  const [myData, setMyData] = useState(null)
  const [teamData, setTeamData] = useState(null)
  const [openData, setOpenData] = useState(null)
  const [requestData, setRequestData] = useState(null)
  const [error, setError] = useState(null)
  const [toast, setToast] = useState('')

  const { user } = useSalesAuth()
  const viewerUserId = user?.id ?? null

  // Coworkers (for the "Request cover" candidate picker) come from the
  // privacy-bounded team payload already loaded for this week.
  const coworkers = useMemo(() => {
    const out = new Map()
    for (const e of teamData?.entries ?? []) {
      if (e.user_id !== viewerUserId && !out.has(e.user_id)) {
        out.set(e.user_id, {
          id: e.user_id,
          full_name: e.full_name || e.username,
        })
      }
    }
    return [...out.values()]
  }, [teamData, viewerUserId])

  const range = useMemo(() => {
    const monday = addDays(startOfWeek(new Date()), weekOffset)
    return {
      from_date: isoDate(monday),
      to_date: isoDate(addDays(monday, 6)),
      days: Array.from({ length: 7 }, (_, i) => isoDate(addDays(monday, i))),
    }
  }, [weekOffset])

  // Re-fetch when the week toggle changes. Switching tabs doesn't
  // refetch — the data was already loaded for this week.
  useEffect(() => {
    let cancelled = false
    setMyData(null)
    setTeamData(null)
    setOpenData(null)
    setRequestData(null)
    setError(null)
    Promise.all([
      salesGetSchedule({
        from_date: range.from_date,
        to_date: range.to_date,
      }),
      salesGetTeamSchedule({
        from_date: range.from_date,
        to_date: range.to_date,
      }),
      salesListOpenShifts({
        from_date: range.from_date,
        to_date: range.to_date,
      }),
      salesListMyShiftRequests(),
    ])
      .then(([mine, team, open, requests]) => {
        if (cancelled) return
        setMyData(mine)
        setTeamData(team)
        setOpenData(open)
        setRequestData(requests)
      })
      .catch(() => {
        if (!cancelled) {
          setError("Couldn't load the schedule. Try again.")
        }
      })
    return () => {
      cancelled = true
    }
  }, [range.from_date, range.to_date])

  const requestStatusByEntryId = useMemo(() => {
    const out = new Map()
    for (const request of requestData?.requests ?? []) {
      if (['approved', 'denied', 'cancelled', 'expired'].includes(request.status)) {
        continue
      }
      const typeLabel =
        REQUEST_TYPE_LABELS[request.request_type] || request.request_type
      const statusLabel =
        REQUEST_STATUS_CHIP[request.status]?.label || request.status
      const label = `${typeLabel}: ${statusLabel}`
      for (const id of [request.source_entry_id, request.target_entry_id]) {
        if (id !== null && id !== undefined && !out.has(id)) {
          out.set(id, label)
        }
      }
    }
    return out
  }, [requestData])

  return (
    <Stack spacing={2}>
      <Box>
        <Typography variant="h5">Schedule</Typography>
        <Typography variant="body2" color="text.secondary">
          Your assigned shifts this week, and who else is working.
          Approved time off hides your shift on the day; published
          shifts are visible to the whole team.
        </Typography>
      </Box>

      <Tabs
        value={view}
        onChange={(_, v) => v && setView(v)}
        variant="fullWidth"
      >
        <Tab value="mine" label="My schedule" />
        <Tab value="team" label="Team schedule" />
        <Tab value="open" label="Open shifts" />
        <Tab value="requests" label="Requests" />
        <Tab value="availability" label="My availability" />
      </Tabs>

      {(view === 'mine' || view === 'team' || view === 'open') && (
        <ToggleButtonGroup
          value={weekOffset}
          exclusive
          onChange={(_, val) => val != null && setWeekOffset(val)}
          size="small"
        >
          <ToggleButton value={0}>This week</ToggleButton>
          <ToggleButton value={7}>Next week</ToggleButton>
        </ToggleButtonGroup>
      )}

      {error && <Alert severity="error">{error}</Alert>}

      {view === 'mine' && (
        <MyScheduleView
          data={myData}
          coworkers={coworkers}
          requestStatusByEntryId={requestStatusByEntryId}
          onCreated={(msg) => {
            setToast(msg)
            setView('requests')
          }}
        />
      )}
      {view === 'team' && (
        <TeamScheduleView
          data={teamData}
          days={range.days}
          viewerUserId={viewerUserId}
          requestStatusByEntryId={requestStatusByEntryId}
          onCreated={(msg) => {
            setToast(msg)
            setView('requests')
          }}
        />
      )}
      {view === 'open' && (
        <OpenShiftsView
          data={openData}
          onClaimed={(msg) => {
            setToast(msg)
            setView('requests')
          }}
        />
      )}
      {view === 'requests' && <RequestsView viewerUserId={viewerUserId} />}
      {view === 'availability' && <AvailabilityView />}

      <Snackbar
        open={Boolean(toast)}
        autoHideDuration={3600}
        onClose={() => setToast('')}
        message={toast}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      />
    </Stack>
  )
}

const REQUEST_TYPE_LABELS = {
  cover: 'Cover',
  swap: 'Swap',
  drop: 'Drop',
  pickup: 'Pick up',
}

const REQUEST_STATUS_CHIP = {
  pending: { label: 'Pending', color: 'warning' },
  accepted_by_staff: { label: 'Accepted by staff', color: 'info' },
  approved: { label: 'Approved', color: 'success' },
  denied: { label: 'Denied', color: 'error' },
  cancelled: { label: 'Cancelled', color: 'default' },
  expired: { label: 'Expired', color: 'default' },
}

function requestStatusChip(status) {
  const cfg = REQUEST_STATUS_CHIP[status] || {
    label: status,
    color: 'default',
  }
  return <Chip size="small" label={cfg.label} color={cfg.color} />
}

function entrySummaryLine(entry) {
  if (!entry) return null
  return `${formatDayHeader(entry.business_date)} · ${formatLocalTimeOnly(
    entry.starts_at_local,
  )} to ${formatLocalTimeOnly(entry.ends_at_local)}`
}

function RequestsView({ viewerUserId }) {
  const [requests, setRequests] = useState(null)
  const [error, setError] = useState(null)
  const [busyId, setBusyId] = useState(null)

  const refresh = useCallback(() => {
    setError(null)
    salesListMyShiftRequests()
      .then((data) => setRequests(data.requests || []))
      .catch(() => {
        setError("Couldn't load your requests. Try again.")
        setRequests([])
      })
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  async function run(id, fn, failMsg) {
    setBusyId(id)
    setError(null)
    try {
      await fn(id)
      refresh()
    } catch {
      setError(failMsg)
    } finally {
      setBusyId(null)
    }
  }

  const cancel = (id) =>
    run(id, salesCancelShiftRequest, "Couldn't cancel that request. Try again.")
  const accept = (id) =>
    run(id, salesAcceptShiftRequest, "Couldn't accept that request. Try again.")
  const decline = (id) =>
    run(id, salesDeclineShiftRequest, "Couldn't decline that request. Try again.")

  if (requests === null) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
        <CircularProgress />
      </Box>
    )
  }

  return (
    <Stack spacing={1.5}>
      {error && <Alert severity="error">{error}</Alert>}
      <Typography variant="body2" color="text.secondary">
        Shift requests you have made, or that name you. A manager
        reviews and approves each one before the schedule changes.
      </Typography>
      {requests.length === 0 ? (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="body2" color="text.secondary">
              No requests yet.
            </Typography>
          </CardContent>
        </Card>
      ) : (
        requests.map((r) => {
          const sourceLine = entrySummaryLine(r.source_entry)
          const targetLine = entrySummaryLine(r.target_entry)
          const pickupLine = entrySummaryLine(r.open_shift_post)
          const terminal = [
            'approved',
            'denied',
            'cancelled',
            'expired',
          ].includes(r.status)
          const isRequester = r.requester_user_id === viewerUserId
          const canCancel = isRequester && !terminal
          const canRespond =
            (r.request_type === 'cover' || r.request_type === 'swap') &&
            r.status === 'pending' &&
            r.candidate_user_id === viewerUserId &&
            !isRequester
          return (
            <Card key={r.id} variant="outlined">
              <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
                <Stack
                  direction={{ xs: 'column', sm: 'row' }}
                  justifyContent="space-between"
                  alignItems={{ xs: 'flex-start', sm: 'center' }}
                  spacing={1}
                >
                  <Box>
                    <Stack
                      direction="row"
                      spacing={1}
                      alignItems="center"
                      sx={{ mb: 0.5 }}
                    >
                      <Chip
                        size="small"
                        variant="outlined"
                        label={
                          REQUEST_TYPE_LABELS[r.request_type] ||
                          r.request_type
                        }
                      />
                      {requestStatusChip(r.status)}
                    </Stack>
                    {sourceLine && (
                      <Typography variant="body2">
                        {r.request_type === 'swap'
                          ? `Your shift: ${sourceLine}`
                          : sourceLine}
                      </Typography>
                    )}
                    {r.request_type === 'pickup' && pickupLine && (
                      <Typography variant="body2">{pickupLine}</Typography>
                    )}
                    {r.request_type === 'pickup' &&
                      r.open_shift_post?.note && (
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{ mt: 0.25, fontStyle: 'italic' }}
                        >
                          {r.open_shift_post.note}
                        </Typography>
                      )}
                    {r.request_type === 'swap' && targetLine && (
                      <Typography variant="body2">
                        Their shift: {targetLine}
                        {r.candidate_full_name
                          ? ` (${r.candidate_full_name})`
                          : ''}
                      </Typography>
                    )}
                    {r.request_type === 'cover' &&
                      r.candidate_full_name && (
                        <Typography
                          variant="body2"
                          color="text.secondary"
                        >
                          Proposed cover: {r.candidate_full_name}
                        </Typography>
                      )}
                    {r.reason && (
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ mt: 0.25, fontStyle: 'italic' }}
                      >
                        {r.reason}
                      </Typography>
                    )}
                  </Box>
                  <Stack direction="row" spacing={0.5}>
                    {canRespond && (
                      <>
                        <Button
                          size="small"
                          variant="contained"
                          disabled={busyId === r.id}
                          onClick={() => accept(r.id)}
                        >
                          Accept
                        </Button>
                        <Button
                          size="small"
                          color="error"
                          variant="outlined"
                          disabled={busyId === r.id}
                          onClick={() => decline(r.id)}
                        >
                          Decline
                        </Button>
                      </>
                    )}
                    {canCancel && (
                      <Button
                        size="small"
                        color="error"
                        variant="outlined"
                        disabled={busyId === r.id}
                        onClick={() => cancel(r.id)}
                      >
                        Cancel
                      </Button>
                    )}
                  </Stack>
                </Stack>
              </CardContent>
            </Card>
          )
        })
      )}
    </Stack>
  )
}

function OpenShiftsView({ data, onClaimed }) {
  const [busyId, setBusyId] = useState(null)
  const [error, setError] = useState(null)

  async function claim(postId) {
    setBusyId(postId)
    setError(null)
    try {
      await salesClaimOpenShift(postId)
      onClaimed?.('Claim sent. A manager will confirm it.')
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      setError(
        code === 'post_not_open'
          ? 'That shift was just taken or pulled.'
          : code === 'already_claimed'
            ? "You've already claimed that shift."
            : code === 'request_cutoff_passed'
              ? 'Too close to the shift to claim it (12-hour cutoff).'
              : "Couldn't claim that shift. Try again.",
      )
    } finally {
      setBusyId(null)
    }
  }

  if (data === null) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
        <CircularProgress />
      </Box>
    )
  }
  const posts = data.posts || []
  return (
    <Stack spacing={1.5}>
      {error && <Alert severity="error">{error}</Alert>}
      <Typography variant="body2" color="text.secondary">
        Shifts the shop needs covered. Claim one and a manager confirms
        it before it lands on your schedule.
      </Typography>
      {posts.length === 0 ? (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="body2" color="text.secondary">
              No open shifts this week.
            </Typography>
          </CardContent>
        </Card>
      ) : (
        posts.map((p) => (
          <Card key={p.id} variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Stack
                direction={{ xs: 'column', sm: 'row' }}
                justifyContent="space-between"
                alignItems={{ xs: 'flex-start', sm: 'center' }}
                spacing={1}
              >
                <Box>
                  <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                    {formatDayHeader(p.business_date)}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {formatLocalTimeOnly(p.starts_at_local)} to{' '}
                    {formatLocalTimeOnly(p.ends_at_local)}
                  </Typography>
                  {p.note && (
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ mt: 0.25, fontStyle: 'italic' }}
                    >
                      {p.note}
                    </Typography>
                  )}
                </Box>
                <Button
                  size="small"
                  variant="contained"
                  disabled={busyId === p.id}
                  onClick={() => claim(p.id)}
                >
                  Claim
                </Button>
              </Stack>
            </CardContent>
          </Card>
        ))
      )}
    </Stack>
  )
}

function MyScheduleView({
  data,
  coworkers = [],
  requestStatusByEntryId = new Map(),
  onCreated,
}) {
  const [coverFor, setCoverFor] = useState(null) // { shift }
  const [candidate, setCandidate] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [dialogError, setDialogError] = useState(null)

  function openCover(shift) {
    setCoverFor({ shift })
    setCandidate('')
    setReason('')
    setDialogError(null)
  }

  async function submitCover() {
    if (!candidate) {
      setDialogError('Pick a coworker to cover this shift.')
      return
    }
    setBusy(true)
    setDialogError(null)
    try {
      await salesCreateShiftRequest({
        request_type: 'cover',
        source_entry_id: coverFor.shift.schedule_entry_id,
        candidate_user_id: candidate,
        reason: reason.trim() || undefined,
      })
      setCoverFor(null)
      onCreated?.('Cover request sent.')
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      setDialogError(
        code === 'request_cutoff_passed'
          ? 'Too close to the shift to request cover (12-hour cutoff). Ask a manager.'
          : code === 'entry_started'
            ? 'This shift has already started.'
            : "Couldn't send the request. Try again.",
      )
    } finally {
      setBusy(false)
    }
  }

  if (data === null) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
        <CircularProgress />
      </Box>
    )
  }
  const now = Date.now()
  return (
    <Stack spacing={1.5}>
      {data.days.map((day) => {
        const offDueToTimeOff = day.time_off_suppressed
        const noShift = !day.shift && !day.time_off_suppressed
        const shift = day.shift
        const coverable =
          shift &&
          shift.schedule_entry_id &&
          new Date(shift.starts_at_local).getTime() > now
        const overnight =
          shift &&
          shift.starts_at_local.slice(0, 10) !==
            shift.ends_at_local.slice(0, 10)
        return (
          <Card
            key={day.business_date}
            variant="outlined"
            sx={{ borderColor: shift ? 'primary.light' : 'divider' }}
          >
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Stack
                direction={{ xs: 'column', sm: 'row' }}
                justifyContent="space-between"
                alignItems={{ xs: 'flex-start', sm: 'center' }}
                spacing={1}
              >
                <Box>
                  <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                    {formatDayHeader(day.business_date)}
                  </Typography>
                  {shift && (
                    <Typography variant="body2" color="text.secondary">
                      {formatLocalTimeOnly(shift.starts_at_local)}
                      {' to '}
                      {formatLocalTimeOnly(shift.ends_at_local)}
                      {overnight && ' (next day)'}
                    </Typography>
                  )}
                  {shift?.manager_notes && (
                    <Typography
                      variant="body2"
                      sx={{
                        mt: 0.5,
                        fontStyle: 'italic',
                        color: 'text.secondary',
                        whiteSpace: 'pre-wrap',
                      }}
                    >
                      Note from manager: {shift.manager_notes}
                    </Typography>
                  )}
                  {shift?.schedule_entry_id &&
                    requestStatusByEntryId.has(shift.schedule_entry_id) && (
                      <Chip
                        size="small"
                        color="info"
                        variant="outlined"
                        label={requestStatusByEntryId.get(
                          shift.schedule_entry_id,
                        )}
                        sx={{ mt: 0.5 }}
                      />
                    )}
                  {offDueToTimeOff && (
                    <Typography variant="body2" color="text.secondary">
                      Approved time off
                    </Typography>
                  )}
                  {noShift && (
                    <Typography variant="body2" color="text.secondary">
                      Off
                    </Typography>
                  )}
                  {(day.recurring_unavailable_blocks ?? []).length > 0 && (
                    <Stack
                      direction="row"
                      spacing={0.5}
                      flexWrap="wrap"
                      useFlexGap
                      sx={{ mt: 0.5 }}
                    >
                      {day.recurring_unavailable_blocks.map((b) => (
                        <Chip
                          key={b.block_id}
                          size="small"
                          variant="outlined"
                          color="warning"
                          label={`Unavailable ${formatLocalTimeOnly(b.starts_at_local)} to ${formatLocalTimeOnly(b.ends_at_local)}`}
                        />
                      ))}
                    </Stack>
                  )}
                </Box>
                <Stack
                  direction="row"
                  spacing={0.5}
                  alignItems="center"
                  flexWrap="wrap"
                  useFlexGap
                >
                  {shift?.is_override && (
                    <Chip
                      size="small"
                      label="Override"
                      color="info"
                      variant="outlined"
                    />
                  )}
                  {overnight && (
                    <Chip size="small" label="Overnight" variant="outlined" />
                  )}
                  {offDueToTimeOff && (
                    <Chip size="small" label="Off" color="success" />
                  )}
                  {coverable && (
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={() => openCover(shift)}
                    >
                      Request cover
                    </Button>
                  )}
                </Stack>
              </Stack>
            </CardContent>
          </Card>
        )
      })}

      <Dialog
        open={coverFor !== null}
        onClose={() => (busy ? null : setCoverFor(null))}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>Request cover</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 0.5 }}>
            {coverFor?.shift && (
              <Typography variant="body2" color="text.secondary">
                {formatLocalTimeOnly(coverFor.shift.starts_at_local)} to{' '}
                {formatLocalTimeOnly(coverFor.shift.ends_at_local)}
              </Typography>
            )}
            <TextField
              select
              label="Ask a coworker"
              value={candidate}
              onChange={(e) => setCandidate(e.target.value)}
              size="small"
              fullWidth
              helperText={
                coworkers.length === 0
                  ? 'No coworkers are scheduled this week to ask.'
                  : 'They confirm, then a manager approves.'
              }
            >
              {coworkers.map((c) => (
                <MenuItem key={c.id} value={c.id}>
                  {c.full_name}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              label="Reason (optional)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              size="small"
              fullWidth
              multiline
              minRows={2}
              inputProps={{ maxLength: 500 }}
            />
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCoverFor(null)} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={submitCover}
            disabled={busy || coworkers.length === 0}
          >
            Send request
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}

function TeamScheduleView({
  data,
  days,
  viewerUserId,
  requestStatusByEntryId = new Map(),
  onCreated,
}) {
  const [swapTarget, setSwapTarget] = useState(null) // coworker row
  const [sourceSel, setSourceSel] = useState('')
  const [busy, setBusy] = useState(false)
  const [dialogError, setDialogError] = useState(null)

  // Group entries by business_date for stable day-by-day rendering.
  // Hook must be called unconditionally — keep it above the early
  // loading-state return.
  const byDay = useMemo(() => {
    const out = new Map()
    for (const e of data?.entries ?? []) {
      const arr = out.get(e.business_date) ?? []
      arr.push(e)
      out.set(e.business_date, arr)
    }
    return out
  }, [data])

  // Prefer the server's viewer_user_id when the auth context hasn't
  // hydrated yet — keeps "You" chip working on a hard refresh.
  const effectiveViewerId = viewerUserId ?? data?.viewer_user_id ?? null

  const now = Date.now()
  // The viewer's own future published shifts this week are the candidate
  // sources to trade away in a swap.
  const myFutureShifts = useMemo(
    () =>
      (data?.entries ?? []).filter(
        (e) =>
          e.user_id === effectiveViewerId &&
          new Date(e.starts_at_local).getTime() > now,
      ),
    [data, effectiveViewerId, now],
  )

  function openSwap(row) {
    setSwapTarget(row)
    setSourceSel('')
    setDialogError(null)
  }

  async function submitSwap() {
    if (!sourceSel) {
      setDialogError('Pick one of your shifts to trade.')
      return
    }
    setBusy(true)
    setDialogError(null)
    try {
      await salesCreateShiftRequest({
        request_type: 'swap',
        source_entry_id: sourceSel,
        target_entry_id: swapTarget.entry_id,
      })
      setSwapTarget(null)
      onCreated?.('Swap request sent.')
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      setDialogError(
        code === 'request_cutoff_passed'
          ? 'Too close to the shift to request a swap (12-hour cutoff).'
          : code === 'invalid_candidate'
            ? "You can't swap a shift with yourself."
            : "Couldn't send the swap request. Try again.",
      )
    } finally {
      setBusy(false)
    }
  }

  if (data === null) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
        <CircularProgress />
      </Box>
    )
  }

  return (
    <Stack spacing={1.5}>
      {days.map((day) => {
        const rows = byDay.get(day) ?? []
        return (
          <Card key={day} variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                {formatDayHeader(day)}
              </Typography>
              {rows.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  No published shifts for this day.
                </Typography>
              ) : (
                <Stack spacing={1}>
                  {rows.map((row) => {
                    const isViewer = row.user_id === effectiveViewerId
                    return (
                      <Stack
                        key={row.entry_id}
                        direction={{ xs: 'column', sm: 'row' }}
                        justifyContent="space-between"
                        alignItems={{ xs: 'flex-start', sm: 'center' }}
                        spacing={1}
                        sx={{
                          p: 1,
                          borderRadius: 1,
                          backgroundColor: isViewer
                            ? 'primary.50'
                            : 'transparent',
                          border: isViewer
                            ? '1px solid'
                            : '1px solid transparent',
                          borderColor: isViewer
                            ? 'primary.light'
                            : 'transparent',
                        }}
                      >
                        <Stack
                          direction="row"
                          spacing={1}
                          alignItems="center"
                          flexWrap="wrap"
                          useFlexGap
                        >
                          <Typography
                            variant="body2"
                            sx={{ fontWeight: isViewer ? 700 : 500 }}
                          >
                            {row.full_name ||
                              row.username ||
                              `Stylist #${row.user_id}`}
                          </Typography>
                          {isViewer && (
                            <Chip
                              size="small"
                              label="You"
                              color="primary"
                            />
                          )}
                          <Typography variant="body2" color="text.secondary">
                            {formatLocalTimeOnly(row.starts_at_local)}
                            {' to '}
                            {formatLocalTimeOnly(row.ends_at_local)}
                          </Typography>
                          {requestStatusByEntryId.has(row.entry_id) && (
                            <Chip
                              size="small"
                              color="info"
                              variant="outlined"
                              label={requestStatusByEntryId.get(row.entry_id)}
                            />
                          )}
                        </Stack>
                        {!isViewer &&
                          new Date(row.starts_at_local).getTime() > now && (
                            <Button
                              size="small"
                              variant="outlined"
                              disabled={myFutureShifts.length === 0}
                              onClick={() => openSwap(row)}
                            >
                              Request swap
                            </Button>
                          )}
                      </Stack>
                    )
                  })}
                </Stack>
              )}
            </CardContent>
          </Card>
        )
      })}

      <Dialog
        open={swapTarget !== null}
        onClose={() => (busy ? null : setSwapTarget(null))}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>Request swap</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 0.5 }}>
            {swapTarget && (
              <Typography variant="body2" color="text.secondary">
                Their shift:{' '}
                {swapTarget.full_name ||
                  swapTarget.username ||
                  `Stylist #${swapTarget.user_id}`}{' '}
                · {formatDayHeader(swapTarget.business_date)} ·{' '}
                {formatLocalTimeOnly(swapTarget.starts_at_local)} to{' '}
                {formatLocalTimeOnly(swapTarget.ends_at_local)}
              </Typography>
            )}
            <TextField
              select
              label="Your shift to trade"
              value={sourceSel}
              onChange={(e) => setSourceSel(e.target.value)}
              size="small"
              fullWidth
              helperText={
                myFutureShifts.length === 0
                  ? 'You have no upcoming shifts this week to trade.'
                  : 'They confirm, then a manager approves.'
              }
            >
              {myFutureShifts.map((s) => (
                <MenuItem key={s.entry_id} value={s.entry_id}>
                  {formatDayHeader(s.business_date)} ·{' '}
                  {formatLocalTimeOnly(s.starts_at_local)} to{' '}
                  {formatLocalTimeOnly(s.ends_at_local)}
                </MenuItem>
              ))}
            </TextField>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSwapTarget(null)} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={submitSwap}
            disabled={busy || myFutureShifts.length === 0}
          >
            Send request
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}

const HOUR_OPTIONS = (() => {
  const opts = []
  for (let mins = 6 * 60; mins <= 22 * 60; mins += 30) {
    const h = Math.floor(mins / 60)
    const m = mins % 60
    const wire = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
    const period = h >= 12 ? 'PM' : 'AM'
    const h12 = h % 12 === 0 ? 12 : h % 12
    const label = `${h12}:${String(m).padStart(2, '0')} ${period}`
    opts.push({ wire, label })
  }
  return opts
})()

function AvailabilityView() {
  const [blocks, setBlocks] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [form, setForm] = useState({
    weekday: 2,
    start_time_local: '18:00',
    end_time_local: '21:00',
    reason: '',
  })

  const refresh = useCallback(async () => {
    setLoadError(null)
    try {
      const body = await salesListMyAvailability()
      setBlocks(body.blocks || [])
    } catch {
      setBlocks([])
      setLoadError("Couldn't load your availability. Try again.")
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  async function handleAdd() {
    if (form.end_time_local <= form.start_time_local) {
      setActionError('End time must be after start time.')
      return
    }
    setSubmitting(true)
    setActionError(null)
    try {
      await salesCreateAvailability({
        weekday: form.weekday,
        start_time_local: form.start_time_local,
        end_time_local: form.end_time_local,
        reason: form.reason.trim() || null,
      })
      setForm((f) => ({ ...f, reason: '' }))
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'duplicate_active_rule') {
        setActionError(
          'You already have an active rule for that weekday and time.',
        )
      } else if (code === 'invalid_time_range') {
        setActionError('End time must be after start time.')
      } else {
        setActionError("Couldn't add that rule. Try again.")
      }
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDelete(blockId) {
    setActionError(null)
    try {
      await salesDeleteAvailability(blockId)
      await refresh()
    } catch {
      setActionError("Couldn't remove that rule. Try again.")
    }
  }

  if (blocks === null) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
        <CircularProgress />
      </Box>
    )
  }

  return (
    <Stack spacing={2}>
      <Card variant="outlined">
        <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            Add an unavailable block
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            Tell the schedule when you cannot work each week. Your
            manager sees these on the staff schedule and the system
            blocks shifts that overlap them from being published.
          </Typography>
          {actionError && (
            <Alert
              severity="error"
              onClose={() => setActionError(null)}
              sx={{ mb: 1.5 }}
            >
              {actionError}
            </Alert>
          )}
          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={1.25}
            alignItems={{ xs: 'stretch', sm: 'flex-end' }}
          >
            <TextField
              select
              label="Day"
              size="small"
              value={form.weekday}
              onChange={(e) =>
                setForm((f) => ({ ...f, weekday: Number(e.target.value) }))
              }
              sx={{ minWidth: 110 }}
            >
              {[1, 2, 3, 4, 5, 6, 7].map((d) => (
                <MenuItem key={d} value={d}>
                  {WEEKDAY_LABELS[d]}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              label="From"
              size="small"
              value={form.start_time_local}
              onChange={(e) =>
                setForm((f) => ({ ...f, start_time_local: e.target.value }))
              }
              sx={{ minWidth: 130 }}
            >
              {HOUR_OPTIONS.map((o) => (
                <MenuItem key={o.wire} value={o.wire}>
                  {o.label}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              label="To"
              size="small"
              value={form.end_time_local}
              onChange={(e) =>
                setForm((f) => ({ ...f, end_time_local: e.target.value }))
              }
              sx={{ minWidth: 130 }}
            >
              {HOUR_OPTIONS.map((o) => (
                <MenuItem key={o.wire} value={o.wire}>
                  {o.label}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              label="Reason (optional)"
              size="small"
              value={form.reason}
              onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))}
              inputProps={{ maxLength: 200 }}
              sx={{ flex: 1, minWidth: 160 }}
            />
            <Button
              variant="contained"
              onClick={handleAdd}
              disabled={submitting}
            >
              Add block
            </Button>
          </Stack>
        </CardContent>
      </Card>

      {loadError && <Alert severity="error">{loadError}</Alert>}

      <Card variant="outlined">
        <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
            Your unavailable blocks
          </Typography>
          {blocks.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              You have no active blocks. Add one above to tell the
              schedule when you cannot work.
            </Typography>
          ) : (
            <Stack divider={<Divider flexItem />} spacing={1}>
              {blocks.map((b) => (
                <Stack
                  key={b.id}
                  direction="row"
                  justifyContent="space-between"
                  alignItems="center"
                  spacing={1}
                >
                  <Box>
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>
                      {WEEKDAY_LABELS[b.weekday]}
                      {' · '}
                      {formatTimeRange(b.start_time_local, b.end_time_local)}
                    </Typography>
                    {b.reason && (
                      <Typography variant="body2" color="text.secondary">
                        {b.reason}
                      </Typography>
                    )}
                    {b.effective_until && (
                      <Typography variant="caption" color="text.secondary">
                        Ends {b.effective_until}
                      </Typography>
                    )}
                  </Box>
                  <Tooltip title="Remove this block" arrow>
                    <IconButton
                      size="small"
                      onClick={() => handleDelete(b.id)}
                      aria-label="Remove block"
                    >
                      <DeleteOutlineIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Stack>
              ))}
            </Stack>
          )}
        </CardContent>
      </Card>
    </Stack>
  )
}
