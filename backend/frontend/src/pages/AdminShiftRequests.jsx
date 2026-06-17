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
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'

import {
  decideAdminShiftRequest,
  getAdminShiftRequest,
  listAdminShiftRequests,
} from '../services/api'

const CONFLICT_LABELS = {
  published_overlap: 'Already has an overlapping published shift',
  approved_time_off: 'Has approved time off then',
  recurring_unavailability: 'Marked recurring unavailable then',
  inactive_user: 'Is not an active staff member',
}

// Staff Management > Schedule & time off > Shift requests.
// Scheduling Phase 1 is a read-only queue: the owner can inspect every
// cover/drop/swap request, but the accept/approve/deny verbs (which move
// shifts) arrive in Phase 2. Pending rows show a disabled "Review"
// affordance so the future action has a home without implying it works.

const TYPE_LABELS = {
  cover: 'Cover',
  swap: 'Swap',
  drop: 'Drop',
  pickup: 'Pick up',
}

const STATUS_CHIP = {
  pending: { label: 'Pending', color: 'warning' },
  accepted_by_staff: { label: 'Accepted by staff', color: 'info' },
  approved: { label: 'Approved', color: 'success' },
  denied: { label: 'Denied', color: 'error' },
  cancelled: { label: 'Cancelled', color: 'default' },
  expired: { label: 'Expired', color: 'default' },
}

const STATUS_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'pending', label: 'Pending' },
  { value: 'accepted_by_staff', label: 'Accepted by staff' },
  { value: 'approved', label: 'Approved' },
  { value: 'denied', label: 'Denied' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'expired', label: 'Expired' },
]

function statusChip(status) {
  const cfg = STATUS_CHIP[status] || { label: status, color: 'default' }
  return <Chip size="small" label={cfg.label} color={cfg.color} />
}

function formatLocal(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function shiftCell(entry) {
  if (!entry) return <span style={{ color: 'rgba(0,0,0,0.4)' }}>—</span>
  return (
    <Box>
      <Typography variant="body2">
        {new Date(`${entry.business_date}T00:00:00`).toLocaleDateString(
          undefined,
          { weekday: 'short', month: 'short', day: 'numeric' },
        )}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {formatLocal(entry.starts_at_local).split(', ').pop()} to{' '}
        {formatLocal(entry.ends_at_local).split(', ').pop()}
      </Typography>
    </Box>
  )
}

export default function AdminShiftRequests() {
  const [statusFilter, setStatusFilter] = useState('all')
  const [requests, setRequests] = useState(null)
  const [loadError, setLoadError] = useState(null)

  const [review, setReview] = useState(null) // hydrated request detail
  const [reviewNotes, setReviewNotes] = useState('')
  const [reviewBusy, setReviewBusy] = useState(false)
  const [reviewError, setReviewError] = useState(null)

  const params = useMemo(
    () => (statusFilter === 'all' ? {} : { status: statusFilter }),
    [statusFilter],
  )

  const refresh = useCallback(() => {
    setLoadError(null)
    listAdminShiftRequests(params)
      .then((data) => {
        const rows = data.requests || []
        // Pending first (the actionable ones), then the rest in the
        // server's newest-first order.
        const pending = rows.filter((r) => r.status === 'pending')
        const accepted = rows.filter((r) => r.status === 'accepted_by_staff')
        const rest = rows.filter(
          (r) => !['pending', 'accepted_by_staff'].includes(r.status),
        )
        setRequests([...accepted, ...pending, ...rest])
      })
      .catch(() => {
        setLoadError("Couldn't load the shift-request queue.")
        setRequests([])
      })
  }, [params])

  useEffect(() => {
    refresh()
  }, [refresh])

  async function openReview(id) {
    setReviewError(null)
    setReviewNotes('')
    setReview(null)
    try {
      const detail = await getAdminShiftRequest(id)
      setReview(detail)
    } catch {
      setLoadError("Couldn't open that request.")
    }
  }

  async function decide(status) {
    if (!review) return
    setReviewBusy(true)
    setReviewError(null)
    try {
      await decideAdminShiftRequest(review.id, {
        status,
        decision_notes: reviewNotes.trim() || undefined,
      })
      setReview(null)
      refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      setReviewError(
        code === 'candidate_conflict'
          ? 'The candidate has a conflict on this shift. Resolve it first.'
          : code === 'entry_started'
            ? 'This shift has already started.'
            : code === 'request_not_accepted'
              ? 'The candidate has not accepted yet.'
              : "Couldn't record the decision. Try again.",
      )
    } finally {
      setReviewBusy(false)
    }
  }

  const reviewConflicts = review?.candidate_conflicts || []
  const reviewIsCover = review?.request_type === 'cover'
  // Cover and swap both need the candidate to accept before approval.
  const reviewNeedsAccept = ['cover', 'swap'].includes(review?.request_type)
  const reviewApprovable =
    review &&
    review.status !== 'approved' &&
    review.status !== 'denied' &&
    review.status !== 'cancelled' &&
    review.status !== 'expired' &&
    (!reviewNeedsAccept || review.status === 'accepted_by_staff') &&
    reviewConflicts.length === 0

  return (
    <Stack spacing={2}>
      <Box>
        <Typography variant="h6">Shift requests</Typography>
        <Typography variant="body2" color="text.secondary">
          Cover, swap, drop, and pickup requests from staff. Approving a
          cover or pickup puts the shift on the coworker's schedule, a swap
          trades two shifts, and a drop pulls a shift back to a draft.
        </Typography>
      </Box>

      <TextField
        select
        size="small"
        label="Status"
        value={statusFilter}
        onChange={(e) => setStatusFilter(e.target.value)}
        sx={{ maxWidth: 220 }}
      >
        {STATUS_FILTERS.map((s) => (
          <MenuItem key={s.value} value={s.value}>
            {s.label}
          </MenuItem>
        ))}
      </TextField>

      {loadError && <Alert severity="error">{loadError}</Alert>}

      <Card>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          {requests === null ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={28} />
            </Box>
          ) : requests.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No requests in this view.
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Requested</TableCell>
                  <TableCell>Staff</TableCell>
                  <TableCell>Type</TableCell>
                  <TableCell>Shift</TableCell>
                  <TableCell>Swap with / cover</TableCell>
                  <TableCell>Reason</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell align="right">Action</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {requests.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>{formatLocal(r.created_at)}</TableCell>
                    <TableCell>
                      {r.requester_full_name || `#${r.requester_user_id}`}
                    </TableCell>
                    <TableCell>
                      {TYPE_LABELS[r.request_type] || r.request_type}
                    </TableCell>
                    <TableCell>
                      {shiftCell(r.source_entry || r.open_shift_post)}
                    </TableCell>
                    <TableCell>
                      {r.request_type === 'swap' ? (
                        shiftCell(r.target_entry)
                      ) : r.candidate_full_name ? (
                        r.candidate_full_name
                      ) : (
                        <span style={{ color: 'rgba(0,0,0,0.4)' }}>—</span>
                      )}
                    </TableCell>
                    <TableCell>{r.reason || ''}</TableCell>
                    <TableCell>{statusChip(r.status)}</TableCell>
                    <TableCell align="right">
                      {['pending', 'accepted_by_staff'].includes(
                        r.status,
                      ) ? (
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() => openReview(r.id)}
                        >
                          Review
                        </Button>
                      ) : (
                        <span style={{ color: 'rgba(0,0,0,0.4)' }}>—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={review !== null}
        onClose={() => (reviewBusy ? null : setReview(null))}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Review request</DialogTitle>
        <DialogContent>
          {review && (
            <Stack spacing={1.5} sx={{ mt: 0.5 }}>
              <Typography variant="body2">
                <strong>
                  {TYPE_LABELS[review.request_type] || review.request_type}
                </strong>{' '}
                from {review.requester_full_name || `#${review.requester_user_id}`}
              </Typography>
              {(review.source_entry || review.open_shift_post) && (
                <Typography variant="body2" color="text.secondary">
                  Shift:{' '}
                  {(review.source_entry || review.open_shift_post).business_date}
                  ,{' '}
                  {formatLocal(
                    (review.source_entry || review.open_shift_post)
                      .starts_at_local,
                  )
                    .split(', ')
                    .pop()}{' '}
                  to{' '}
                  {formatLocal(
                    (review.source_entry || review.open_shift_post)
                      .ends_at_local,
                  )
                    .split(', ')
                    .pop()}
                </Typography>
              )}
              {review.request_type === 'swap' && review.target_entry && (
                <Typography variant="body2" color="text.secondary">
                  Their shift ({review.candidate_full_name || 'coworker'}):{' '}
                  {review.target_entry.business_date},{' '}
                  {formatLocal(review.target_entry.starts_at_local)
                    .split(', ')
                    .pop()}{' '}
                  to{' '}
                  {formatLocal(review.target_entry.ends_at_local)
                    .split(', ')
                    .pop()}
                  {review.status !== 'accepted_by_staff' &&
                    ' (waiting on their acceptance)'}
                </Typography>
              )}
              {reviewIsCover && review.candidate_full_name && (
                <Typography variant="body2" color="text.secondary">
                  Cover by: {review.candidate_full_name}
                  {review.status !== 'accepted_by_staff' &&
                    ' (waiting on their acceptance)'}
                </Typography>
              )}
              {reviewConflicts.length > 0 && (
                <Alert severity="error">
                  Can't approve — there's a scheduling conflict:
                  <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                    {reviewConflicts.map((c, i) => (
                      <li key={i}>{CONFLICT_LABELS[c.type] || c.type}</li>
                    ))}
                  </ul>
                </Alert>
              )}
              {reviewNeedsAccept &&
                review.status === 'pending' &&
                reviewConflicts.length === 0 && (
                  <Alert severity="info">
                    The coworker hasn't accepted yet — you can deny, but
                    approval unlocks once they accept.
                  </Alert>
                )}
              <TextField
                label="Decision note (optional)"
                value={reviewNotes}
                onChange={(e) => setReviewNotes(e.target.value)}
                size="small"
                fullWidth
                multiline
                minRows={2}
                inputProps={{ maxLength: 500 }}
              />
              {reviewError && <Alert severity="error">{reviewError}</Alert>}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setReview(null)} disabled={reviewBusy}>
            Close
          </Button>
          <Button
            color="error"
            onClick={() => decide('denied')}
            disabled={reviewBusy}
          >
            Deny
          </Button>
          <Button
            variant="contained"
            onClick={() => decide('approved')}
            disabled={reviewBusy || !reviewApprovable}
          >
            Approve
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
