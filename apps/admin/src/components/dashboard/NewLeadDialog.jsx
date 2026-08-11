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
import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import WalkInLeadIntakeForm from '../WalkInLeadIntakeForm'
import { createWalkInLead, salesListAssignableStaff } from '../../services/api'
import {
  buildWalkInLeadPayload,
  canSaveWalkInLead,
  describeWalkInLeadError,
  emptyWalkInLeadForm,
} from '../../utils/walkInLeadIntake'

// Admin-side lead capture, opened from the dashboard and the command
// palette. The questions themselves live in WalkInLeadIntakeForm, shared
// with the rep portal's version of this dialog.
//
// The salesperson picker here is **commission credit, not ownership**. The
// CRM is worked by the admin staff — they own every lead, and the server
// already resolves ownership to whoever filed it. The rep who walked the
// customer through the door often never opens the CRM at all, but is owed
// the commission, so their name rides in `sales_credit_user_id` where a
// later reassignment of the lead cannot wipe it out.
//
// Form state is local on purpose. The palette context owns only open/close;
// putting form state there would couple every consumer to its lifecycle.

const NO_CREDIT = ''

export default function NewLeadDialog({ open, onClose }) {
  const navigate = useNavigate()
  const theme = useTheme()
  const fullScreen = useMediaQuery(theme.breakpoints.down('sm'))

  const [form, setForm] = useState(emptyWalkInLeadForm)
  const [salesCreditUserId, setSalesCreditUserId] = useState(NO_CREDIT)
  const [error, setError] = useState(null)

  // Same assignable-staff list the sales portal and the owner-reassignment
  // dialog use, so the set of nameable staff has one answer across the app.
  const staffQuery = useQuery({
    queryKey: ['sales', 'staff', 'assignable'],
    queryFn: salesListAssignableStaff,
    enabled: open,
    staleTime: 5 * 60_000,
  })

  // Reset whenever the dialog opens, so state never leaks between two
  // unrelated walk-ins.
  useEffect(() => {
    if (!open) return
    setForm(emptyWalkInLeadForm())
    setSalesCreditUserId(NO_CREDIT)
    setError(null)
  }, [open])

  const submit = useMutation({
    mutationFn: (payload) => createWalkInLead(payload),
    onSuccess: (resp) => {
      onClose?.()
      if (resp?.event?.id) {
        navigate(`/deals/${resp.event.id}/overview`)
      }
    },
    onError: (err) => setError(describeWalkInLeadError(err)),
  })

  function handleSubmit(e) {
    e?.preventDefault?.()
    if (!canSaveWalkInLead(form) || submit.isPending) return
    setError(null)
    submit.mutate(
      buildWalkInLeadPayload(form, {
        salesCreditUserId:
          salesCreditUserId === NO_CREDIT ? null : Number(salesCreditUserId),
      }),
    )
  }

  const staff = staffQuery.data || []
  const isPhoneLead = form.booking_context === 'phone_call'

  // Starts blank, and blank is a real answer: a customer who called in or
  // found the website on their own owes nobody a commission. There is no
  // fallback to the person filing the form — inventing credit would be
  // worse than leaving it empty.
  const assigneeControl = (
    <TextField
      select
      fullWidth
      size="small"
      label="Who brought them in?"
      value={salesCreditUserId}
      onChange={(e) => setSalesCreditUserId(e.target.value)}
      disabled={staffQuery.isLoading}
      helperText={
        staffQuery.isError
          ? 'Could not load the staff list.'
          : 'For commission credit. Doesn’t change who owns the lead.'
      }
    >
      <MenuItem value={NO_CREDIT}>
        <em>Nobody — they came in on their own</em>
      </MenuItem>
      {staff.map((row) => (
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
      maxWidth="sm"
      fullWidth
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
            searchScope="admin"
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
