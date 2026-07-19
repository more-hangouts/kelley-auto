import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { recordRefund } from '../services/api'
import { formatUSD } from '../utils/money'
import CurrencyInput from './CurrencyInput'

const METHODS = [
  { value: 'cash', label: 'Cash' },
  { value: 'check', label: 'Check' },
  { value: 'card', label: 'Card' },
  { value: 'transfer', label: 'Bank transfer' },
  { value: 'zelle', label: 'Zelle' },
  { value: 'other', label: 'Other' },
]

export default function RefundDialog({ open, onClose, payment, eventId, onRefunded }) {
  const queryClient = useQueryClient()
  const [refundMethod, setRefundMethod] = useState('cash')
  const [reference, setReference] = useState('')
  const [notes, setNotes] = useState('')
  const [fromUnapplied, setFromUnapplied] = useState(0)
  // Per-allocation refund slices: { allocationId: cents }
  const [allocSlices, setAllocSlices] = useState({})
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!open) return
    setRefundMethod('cash')
    setReference('')
    setNotes('')
    setFromUnapplied(0)
    setAllocSlices({})
    setError(null)
  }, [open, payment?.id])

  const allocSum = useMemo(
    () => Object.values(allocSlices).reduce((s, n) => s + (Number(n) || 0), 0),
    [allocSlices],
  )
  const totalRefund = (Number(fromUnapplied) || 0) + allocSum

  const refundableTotal = payment
    ? payment.amount_cents - payment.refunded_cents
    : 0
  const allocations = payment?.allocations || []

  const setSlice = (id, cents) => {
    setAllocSlices((prev) => {
      const next = { ...prev }
      if (!cents || cents <= 0) delete next[id]
      else next[id] = cents
      return next
    })
  }

  const valid =
    payment &&
    totalRefund > 0 &&
    totalRefund <= refundableTotal &&
    fromUnapplied >= 0 &&
    fromUnapplied <= payment.unapplied_cents

  const refundMutation = useMutation({
    mutationFn: () =>
      recordRefund(payment.id, {
        amount_cents: totalRefund,
        refund_method: refundMethod,
        refund_reference: reference || null,
        notes: notes || null,
        from_unapplied_cents: Number(fromUnapplied) || 0,
        allocation_refunds: Object.entries(allocSlices)
          .filter(([, c]) => Number(c) > 0)
          .map(([id, c]) => ({
            allocation_id: Number(id),
            refund_cents: Number(c),
          })),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['payment', payment.id] })
      queryClient.invalidateQueries({ queryKey: ['event', eventId, 'payments'] })
      queryClient.invalidateQueries({ queryKey: ['event', eventId, 'invoices'] })
      // Each touched invoice's editor cache also refreshes.
      for (const a of allocations) {
        queryClient.invalidateQueries({ queryKey: ['invoice', a.invoice_id] })
      }
      // Phase 9: payment.refunded emits an activity row.
      queryClient.invalidateQueries({ queryKey: ['event', eventId, 'activity'] })
      // Phase 10: refunds reduce deposits-this-month AND can move an
      // invoice back into the outstanding pool, so both AR widgets and
      // the kanban pill need a refetch.
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['events', 'board'] })
      if (onRefunded) onRefunded()
      onClose()
    },
    onError: (err) => setError(parseApiError(err)),
  })

  if (!payment) return null

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        <Stack direction="row" justifyContent="space-between" alignItems="center">
          Refund {payment.payment_number}
          <IconButton size="small" onClick={onClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Stack>
      </DialogTitle>
      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        <Stack spacing={1.5} sx={{ mb: 2 }}>
          <Row label="Original amount">{formatUSD(payment.amount_cents)}</Row>
          <Row label="Already refunded">{formatUSD(payment.refunded_cents)}</Row>
          <Row label="Refundable" bold>
            {formatUSD(refundableTotal)}
          </Row>
        </Stack>

        {payment.unapplied_cents > 0 && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="subtitle2">From unapplied pool</Typography>
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
              Up to {formatUSD(payment.unapplied_cents)} can come from the unallocated portion. Refunding from here does not change any invoice's balance.
            </Typography>
            <CurrencyInput
              valueCents={fromUnapplied}
              onCommit={(v) =>
                setFromUnapplied(Math.min(v, payment.unapplied_cents))
              }
              label="Amount from unapplied"
              fullWidth
            />
          </Box>
        )}

        {allocations.length > 0 && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              From allocations
            </Typography>
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
              Refunding from an allocation drops that invoice's paid_to_date by the same amount.
            </Typography>
            <Stack spacing={1}>
              {allocations.map((a) => {
                const remaining = a.applied_cents - a.refunded_cents
                return (
                  <Box
                    key={a.id}
                    sx={{
                      display: 'grid',
                      gridTemplateColumns: '1fr 140px',
                      gap: 1,
                      alignItems: 'center',
                    }}
                  >
                    <Box>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {a.invoice_number || `Invoice #${a.invoice_id}`}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        Applied {formatUSD(a.applied_cents)} · refundable {formatUSD(remaining)}
                      </Typography>
                    </Box>
                    <CurrencyInput
                      valueCents={allocSlices[a.id] || 0}
                      onCommit={(v) => setSlice(a.id, Math.min(v, remaining))}
                      label="Refund"
                      disabled={remaining === 0}
                    />
                  </Box>
                )
              })}
            </Stack>
          </Box>
        )}

        <Box sx={{ mt: 2 }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            sx={{ borderTop: 1, borderColor: 'divider', pt: 1.5, mb: 2 }}
          >
            <Typography variant="body2">Total refund</Typography>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 700,
                color:
                  totalRefund > refundableTotal ? 'error.main' : 'text.primary',
              }}
            >
              {formatUSD(totalRefund)}
              {totalRefund > refundableTotal &&
                ` (exceeds ${formatUSD(refundableTotal)})`}
            </Typography>
          </Stack>

          <Stack direction="row" spacing={2}>
            <TextField
              select
              size="small"
              label="Refund method"
              value={refundMethod}
              onChange={(e) => setRefundMethod(e.target.value)}
              sx={{ minWidth: 180 }}
            >
              {METHODS.map((m) => (
                <MenuItem key={m.value} value={m.value}>
                  {m.label}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              size="small"
              label="Reference"
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              sx={{ flex: 1 }}
            />
          </Stack>
          <TextField
            label="Notes"
            multiline
            minRows={2}
            fullWidth
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            sx={{ mt: 2 }}
          />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          color="error"
          onClick={() => refundMutation.mutate()}
          disabled={!valid || refundMutation.isPending}
        >
          {refundMutation.isPending ? 'Refunding…' : 'Record refund'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function Row({ label, children, bold }) {
  return (
    <Stack direction="row" justifyContent="space-between">
      <Typography variant="body2" sx={{ fontWeight: bold ? 700 : 400 }}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ fontWeight: bold ? 700 : 500 }}>
        {children}
      </Typography>
    </Stack>
  )
}

function parseApiError(err) {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.code) {
    const map = {
      refund_exceeds_remaining: 'Refund total exceeds the payment’s remaining refundable amount.',
      refund_split_mismatch: 'Refund slices do not add up to the total.',
      refund_unapplied_exceeds_pool:
        'Cannot refund more from the unapplied pool than what is in it.',
      refund_exceeds_allocation_remaining:
        'One allocation refund exceeds what is left on that allocation.',
      invalid_payment_state:
        'This payment cannot be refunded in its current state.',
      invalid_amount: 'Amounts must be positive.',
      invalid_method: 'Pick a valid refund method.',
    }
    return map[detail.code] || `Error: ${detail.code}`
  }
  return err?.message || 'Failed to record refund.'
}
