import { useEffect, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  TextField,
  useMediaQuery,
  useTheme,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import WalkInLeadIntakeForm from '../components/WalkInLeadIntakeForm'
import { salesCreateWalkIn, salesListAssignableStaff } from '../services/api'
import { useSalesAuth } from '../contexts/SalesAuthContext'
import {
  buildWalkInLeadPayload,
  canSaveWalkInLead,
  describeWalkInLeadError,
  emptyWalkInLeadForm,
} from '../utils/walkInLeadIntake'
import { attendanceGateMessage, isAttendanceGateError } from './attendanceGate'

// Rep-portal lead capture. Same questions as the admin dialog — they share
// WalkInLeadIntakeForm — with one difference: the rep filing it is the
// default, since they are the one standing there.
//
// The picked rep goes out on two fields, which are not the same thing:
//
//   - `assigned_user_id` — the sales-portal assignment this route has
//     always had. The server mirrors it onto the appointment and the deal
//     owner so the walk-in shows under "Today, mine". Untouched.
//   - `sales_credit_user_id` — commission credit (migration 110). Survives
//     any later reassignment of the lead by admin, which the assignment
//     does not.
//
// On this surface they name the same person; on the admin surface only the
// credit field is set, because the office staff own the leads there.

function describeError(err) {
  // The attendance gate is checked first: it also arrives as a 403, and the
  // generic "you don't have permission" text would send a rep hunting for a
  // permissions problem when they simply have not clocked in.
  if (isAttendanceGateError(err)) return attendanceGateMessage()
  if (
    err?.response?.status === 400 &&
    err?.response?.data?.detail === 'invalid_assigned_user_id'
  ) {
    return 'Pick an active salesperson.'
  }
  return describeWalkInLeadError(err)
}

export default function SalesWalkInDialog({ open, onClose, onCreated }) {
  const navigate = useNavigate()
  const theme = useTheme()
  const fullScreen = useMediaQuery(theme.breakpoints.down('sm'))
  const { user } = useSalesAuth()

  const [form, setForm] = useState(emptyWalkInLeadForm)
  const [assignedUserId, setAssignedUserId] = useState('')
  const [error, setError] = useState(null)

  const staffQuery = useQuery({
    queryKey: ['sales', 'staff', 'assignable'],
    queryFn: salesListAssignableStaff,
    enabled: open,
    staleTime: 5 * 60_000,
  })

  useEffect(() => {
    if (!open) return
    setForm(emptyWalkInLeadForm())
    setAssignedUserId(user?.id ? String(user.id) : '')
    setError(null)
  }, [open, user?.id])

  const submit = useMutation({
    mutationFn: (payload) => salesCreateWalkIn(payload),
    onSuccess: (resp) => {
      onCreated?.(resp)
      onClose?.()
      // A phone lead has no appointment, so the server sends no route — the
      // rep portal has no deal screen to land on. Close and let the
      // dashboard refresh instead of navigating nowhere.
      if (resp?.route) navigate(resp.route)
    },
    onError: (err) => setError(describeError(err)),
  })

  function handleSubmit(e) {
    e?.preventDefault?.()
    if (!canSaveWalkInLead(form) || submit.isPending) return
    setError(null)
    const repId = assignedUserId ? Number(assignedUserId) : null
    submit.mutate({
      ...buildWalkInLeadPayload(form, { salesCreditUserId: repId }),
      assigned_user_id: repId,
    })
  }

  const staff = staffQuery.data || []
  const isPhoneLead = form.booking_context === 'phone_call'

  const assigneeControl = (
    <TextField
      select
      fullWidth
      size="small"
      label="Who brought them in?"
      value={assignedUserId}
      onChange={(e) => setAssignedUserId(e.target.value)}
      disabled={staffQuery.isLoading}
      helperText={
        staffQuery.isError
          ? 'Could not load the staff list — this will default to you.'
          : undefined
      }
    >
      {user?.id && (
        <MenuItem value={String(user.id)}>
          {`${user.full_name || user.username || 'Me'} (me)`}
        </MenuItem>
      )}
      {staff
        .filter((row) => row.id !== user?.id)
        .map((row) => (
          <MenuItem key={row.id} value={String(row.id)}>
            {row.full_name || row.username}
          </MenuItem>
        ))}
    </TextField>
  )

  return (
    <Dialog
      open={open}
      onClose={submit.isPending ? undefined : onClose}
      fullScreen={fullScreen}
      fullWidth
      maxWidth="sm"
    >
      <DialogTitle>{isPhoneLead ? 'New phone lead' : 'New walk-in'}</DialogTitle>
      <DialogContent dividers>
        <Box component="form" onSubmit={handleSubmit}>
          {error && (
            <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
              {error}
            </Alert>
          )}
          <WalkInLeadIntakeForm
            value={form}
            onChange={setForm}
            searchScope="sales"
            assigneeControl={assigneeControl}
          />
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose} disabled={submit.isPending}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={!canSaveWalkInLead(form) || submit.isPending}
          startIcon={submit.isPending ? <CircularProgress size={16} /> : null}
        >
          {isPhoneLead ? 'Save phone lead' : 'Save walk-in lead'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
