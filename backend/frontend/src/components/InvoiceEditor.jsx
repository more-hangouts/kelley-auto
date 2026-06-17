import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  Drawer,
  IconButton,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import CloseIcon from '@mui/icons-material/Close'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'

import {
  cancelInvoice,
  createInvoice,
  deleteInvoice,
  getBusinessProfile,
  getInvoice,
  retryInvoicePdf,
  sendInvoice,
  updateInvoice,
  viewInvoicePdf,
} from '../services/api'
import { formatUSD } from '../utils/money'
import {
  customerLineDescription,
  detectCatalogLeak,
  emptyLine,
  hydrateLineFromInvoice,
  normalizeQuantityInput,
  serializeLineForApi,
} from '../utils/lineItems'
import CatalogPicker from './CatalogPicker'
import ConfirmDialog from './ConfirmDialog'
import CurrencyInput from './CurrencyInput'
import LineDiscountControl from './LineDiscountControl'
import OrderDiscountsControl from './OrderDiscountsControl'
import PlanSelector from './PlanSelector'
import TaxRateInput from './TaxRateInput'

// Drawer width — wide enough for the line-item table without horizontal
// scrolling on common desktop sizes.
const DRAWER_WIDTH = 920

const STATUS_COLOR = {
  draft: 'default',
  sent: 'primary',
  partial: 'warning',
  paid: 'success',
  cancelled: 'default',
  reversed: 'default',
}

const STATUS_LABEL = {
  draft: 'Draft',
  sent: 'Sent',
  partial: 'Partial',
  paid: 'Paid',
  cancelled: 'Cancelled',
  reversed: 'Reversed',
}

const LINE_KINDS = [
  { value: 'product', label: 'Product' },
  { value: 'service', label: 'Service' },
  { value: 'alteration', label: 'Alteration' },
  { value: 'fee', label: 'Fee' },
]

// ---------------------------------------------------------------------------
// Pure helpers — keep money math out of the render path.
// ---------------------------------------------------------------------------

function bankRound(value) {
  // Banker's rounding to integer. Mirrors backend `Decimal.ROUND_HALF_EVEN`.
  const sign = value < 0 ? -1 : 1
  const abs = Math.abs(value)
  const floor = Math.floor(abs)
  const diff = abs - floor
  if (diff > 0.5 || (diff === 0.5 && floor % 2 === 1)) {
    return sign * (floor + 1)
  }
  return sign * floor
}

function computeLineAmounts(line, orderDiscountPercent = null) {
  const qty = parseFloat(line.quantity || '0') || 0
  const unit = Number(line.unit_price_cents || 0)
  const discount = Number(line.discount_cents || 0)
  const rate = parseFloat(line.tax_rate || '0') || 0
  const linePreOrder = bankRound(qty * unit - discount)
  let subtotal
  if (orderDiscountPercent != null) {
    const factor = 1 - Number(orderDiscountPercent) / 100
    subtotal = bankRound(linePreOrder * factor)
  } else {
    subtotal = linePreOrder
  }
  const tax = bankRound(subtotal * rate)
  const total = subtotal + tax
  return {
    line_pre_order_cents: linePreOrder,
    line_subtotal_cents: subtotal,
    line_tax_cents: tax,
    line_total_cents: total,
  }
}

// Phase 2a money math.
// - When `orderDiscountPercent` is null, behaves like the legacy editor:
//   `total = SUM(line_total) - legacyDiscountCents`.
// - When set, the percent path applies the discount pre-tax: each line
//   shrinks by `(1 - pct/100)` before tax, the subtotal stays the
//   pre-order taxable base, and the total comes straight from the line
//   sum (which already has the discount baked in).
function computeTotals(lines, orderDiscountPercent = null, legacyDiscountCents = 0) {
  let preOrderSubtotal = 0
  let lineTaxSum = 0
  let lineTotalSum = 0
  let lineDiscountTotal = 0
  for (const line of lines) {
    const amounts = computeLineAmounts(line, orderDiscountPercent)
    preOrderSubtotal += amounts.line_pre_order_cents
    lineTaxSum += amounts.line_tax_cents
    lineTotalSum += amounts.line_total_cents
    lineDiscountTotal += Number(line.discount_cents || 0)
  }
  let discountCents = 0
  let totalCents
  if (orderDiscountPercent != null) {
    discountCents = bankRound(
      (preOrderSubtotal * Number(orderDiscountPercent)) / 100,
    )
    totalCents = lineTotalSum
  } else {
    discountCents = Number(legacyDiscountCents || 0)
    totalCents = lineTotalSum - discountCents
  }
  return {
    subtotal_cents: preOrderSubtotal,
    tax_cents: lineTaxSum,
    discount_cents: discountCents,
    total_cents: totalCents,
    line_discount_total_cents: lineDiscountTotal,
  }
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------

export default function InvoiceEditor({ open, onClose, onCreated, eventId, eventDate, contactId, contactName, invoiceId }) {
  const queryClient = useQueryClient()
  const isEditing = invoiceId != null

  const invoiceQuery = useQuery({
    queryKey: ['invoice', invoiceId],
    queryFn: () => getInvoice(invoiceId),
    enabled: open && isEditing,
  })

  const profileQuery = useQuery({
    queryKey: ['business-profile'],
    queryFn: getBusinessProfile,
    enabled: open,
    staleTime: 5 * 60_000,
  })

  const lineDefaults = useMemo(() => {
    const p = profileQuery.data
    if (!p) return undefined
    return { tax_rate: p.default_tax_rate, tax_name: p.default_tax_name }
  }, [profileQuery.data])

  // Form state
  const [lines, setLines] = useState([])
  const [installments, setInstallments] = useState([])
  const [issueDate, setIssueDate] = useState(dayjs().format('YYYY-MM-DD'))
  const [terms, setTerms] = useState('')
  const [footer, setFooter] = useState('')
  const [publicNotes, setPublicNotes] = useState('')
  const [privateNotes, setPrivateNotes] = useState('')
  const [poNumber, setPoNumber] = useState('')
  const [moreOpen, setMoreOpen] = useState(false)
  const [errorMsg, setErrorMsg] = useState(null)
  // Phase 7: stacked order-level discounts. Each entry is either a
  // preset reference or a custom one-off; combined cap at 50% is
  // enforced server-side.
  const [orderDiscounts, setOrderDiscounts] = useState([])
  // Phase 5 plan selector escape hatch. Per-write flag, not persisted.
  // Carries forward across saves while the drawer is open so toggling
  // the switch does not reset every cart edit.
  const [customAmounts, setCustomAmounts] = useState(false)

  const [confirmSendOpen, setConfirmSendOpen] = useState(false)
  const [confirmCancelOpen, setConfirmCancelOpen] = useState(false)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)

  // Hydrate from server invoice (edit mode) or business-profile defaults
  // (create mode). Only run when the drawer opens or the source data
  // changes; otherwise stale form keystrokes get overwritten.
  useEffect(() => {
    if (!open) return
    if (isEditing && invoiceQuery.data) {
      const inv = invoiceQuery.data
      setLines(inv.line_items.map(hydrateLineFromInvoice))
      setInstallments(
        inv.installments.map((inst) => ({
          id: inst.id,
          label: inst.label,
          amount_cents: inst.amount_cents,
          due_date: inst.due_date,
          paid_at: inst.paid_at,
        })),
      )
      setIssueDate(inv.issue_date)
      setTerms(inv.terms || '')
      setFooter(inv.footer || '')
      setPublicNotes(inv.public_notes || '')
      setPrivateNotes(inv.private_notes || '')
      setPoNumber(inv.po_number || '')
      setOrderDiscounts(
        (inv.order_discounts || []).map((od) => ({
          kind: od.preset_id ? 'preset' : 'custom',
          preset_id: od.preset_id || undefined,
          label: od.label,
          percent: Number(od.percent),
        })),
      )
      setCustomAmounts(false)
    } else if (!isEditing && profileQuery.data) {
      const p = profileQuery.data
      setLines([])
      setInstallments([])
      setIssueDate(dayjs().format('YYYY-MM-DD'))
      setTerms(p.default_invoice_terms || '')
      setFooter(p.default_invoice_footer || '')
      setPublicNotes(p.default_payment_instructions || '')
      setPrivateNotes('')
      setPoNumber('')
      setOrderDiscounts([])
      setCustomAmounts(false)
    }
    setErrorMsg(null)
  }, [open, isEditing, invoiceQuery.data, profileQuery.data])

  const allPresets = useMemo(
    () => profileQuery.data?.discount_presets || [],
    [profileQuery.data?.discount_presets],
  )

  // Phase 7: combined order-discount percent is the sum of every
  // active row in the stack. Returns null (legacy path) only when no
  // stacked discounts are present.
  const effectiveDiscountPercent = useMemo(() => {
    if (orderDiscounts.length === 0) return null
    const sum = orderDiscounts.reduce(
      (acc, row) => acc + (Number(row.percent) || 0),
      0,
    )
    return sum > 0 ? sum : null
  }, [orderDiscounts])

  // Legacy `discount_cents` only matters when the editor leaves the
  // record on the legacy flat-amount path (no order discounts and the
  // record already had a flat amount). Saving with an empty stack
  // clears the legacy flat amount via `_recompute_totals`.
  const legacyDiscountCents =
    orderDiscounts.length === 0 &&
    !(invoiceQuery.data?.order_discounts || []).length
      ? Number(invoiceQuery.data?.discount_cents || 0)
      : 0

  const totals = useMemo(
    () => computeTotals(lines, effectiveDiscountPercent, legacyDiscountCents),
    [lines, effectiveDiscountPercent, legacyDiscountCents],
  )

  const scheduleSum = useMemo(
    () => installments.reduce((s, i) => s + (i.amount_cents || 0), 0),
    [installments],
  )

  const status = invoiceQuery.data?.status || 'draft'
  const isLocked = ['paid', 'cancelled', 'reversed'].includes(status)
  const isSentOrPartial = ['sent', 'partial'].includes(status)
  const canDeleteDraft = isEditing && status === 'draft'
  const editableHeader = !isLocked

  const scheduleBalanced = scheduleSum === totals.total_cents
  const sendDisabled =
    !editableHeader || lines.length === 0 || installments.length === 0 ||
    !scheduleBalanced

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['event', eventId, 'invoices'] })
    queryClient.invalidateQueries({ queryKey: ['invoices'] })
    if (invoiceId) {
      queryClient.invalidateQueries({ queryKey: ['invoice', invoiceId] })
    }
    queryClient.invalidateQueries({ queryKey: ['event', eventId, 'document-counts'] })
    queryClient.invalidateQueries({ queryKey: ['events', 'board'] })
    // Soft-deleting a draft invoice unlinks the originating quote
    // (returns it to 'approved'). Invalidate the quote caches so the
    // Quotes tab and any open quote drawer see the status flip.
    queryClient.invalidateQueries({ queryKey: ['event', eventId, 'quotes'] })
    queryClient.invalidateQueries({ queryKey: ['quote'] })
    // Phase 9: every invoice mutation emits an activity row.
    queryClient.invalidateQueries({ queryKey: ['event', eventId, 'activity'] })
    // Phase 10: invoice state changes the AR rollups (status flips,
    // total changes, send/cancel — every editor mutation can move the
    // outstanding balance figure on the dashboard).
    queryClient.invalidateQueries({ queryKey: ['dashboard'] })
  }

  const errorFromResponse = (err) => {
    const detail = err?.response?.data?.detail
    if (!detail) return err.message || 'Save failed.'
    if (typeof detail === 'string') return detail
    if (detail.code === 'schedule_unbalanced') {
      const sched = detail.schedule_sum_cents
      const total = detail.total_cents
      return `Payment schedule (${formatUSD(sched)}) does not match invoice total (${formatUSD(total)}).`
    }
    if (detail.code === 'schedule_required') {
      return 'Add at least one payment installment before sending.'
    }
    if (detail.code === 'plan_count_invalid') {
      return 'Payment plan must be 1, 2, or 3 installments.'
    }
    if (detail.code === 'deposit_below_floor') {
      return (
        'Deposit must be at least 50% of the total. Raise the deposit ' +
        'or toggle "Custom amounts" to override.'
      )
    }
    if (detail.code === 'line_items_required') {
      return 'Add at least one line item before sending.'
    }
    if (detail.code === 'invoice_locked') {
      return 'This invoice is locked and cannot be edited.'
    }
    if (detail.code === 'paid_installment_dropped') {
      return 'Cannot remove an installment that has already been paid.'
    }
    if (detail.code === 'catalog_line_legacy_text') {
      return 'Catalog-backed line cannot have customer description, ' +
        'description, or notes — clear those fields and try again.'
    }
    if (detail.code === 'catalog_leak') {
      const kind = detail.identifier_kind || 'identifier'
      return `Customer copy must not contain the catalog ${kind}; ` +
        'edit the line and remove the staff-side text.'
    }
    if (detail.code === 'public_description_required') {
      return 'Add a customer description for non-catalog lines.'
    }
    if (detail.code === 'line_public_description_conflict') {
      return 'Customer description and the legacy field disagree; clear ' +
        'one of them.'
    }
    if (detail.code === 'catalog_item_not_found') {
      return 'Selected catalog item could not be found. Pick a different one.'
    }
    if (detail.code === 'catalog_item_inactive') {
      return 'Selected catalog item is inactive. Pick an active row or ' +
        'reactivate it from the admin catalog screen.'
    }
    return detail.code || 'Save failed.'
  }

  const buildOrderDiscountsPayload = () =>
    orderDiscounts.map((row) =>
      row.kind === 'preset'
        ? { preset_id: row.preset_id }
        : {
            label: (row.label || 'Custom').trim() || 'Custom',
            percent: row.percent === '' ? null : row.percent,
          },
    )

  const buildPatchBody = () => {
    const body = {
      line_items: lines.map((li, idx) => serializeLineForApi(li, idx)),
      installments: installments.map((inst, idx) => ({
        label: inst.label,
        amount_cents: inst.amount_cents,
        due_date: inst.due_date,
        sort_order: idx,
      })),
      // Phase 7 stacked discounts. Empty array clears the stack and
      // returns the record to the legacy flat-amount path.
      order_discounts: buildOrderDiscountsPayload(),
      // Phase 5 plan validity flag. Default false (deposit floor enforced);
      // staff opt out via the PlanSelector switch.
      custom_amounts: customAmounts,
      issue_date: issueDate,
      terms: terms || null,
      footer: footer || null,
      public_notes: publicNotes || null,
      private_notes: privateNotes || null,
      po_number: poNumber || null,
    }
    // Clear the prior derived `discount_cents` when the stack just got
    // emptied; the server zeroes this for us when at least one order
    // discount is present.
    if (orderDiscounts.length === 0) body.discount_cents = 0
    return body
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (isEditing) {
        return updateInvoice(invoiceId, buildPatchBody())
      }
      return createInvoice(eventId, {
        contact_id: contactId,
        ...buildPatchBody(),
      })
    },
    onSuccess: (data) => {
      invalidate()
      if (!isEditing) {
        // Seed the cache so the upcoming useQuery for this id resolves
        // synchronously, then promote the drawer into edit mode by handing
        // the new id back to the parent. Without this, a second click on
        // Save would call createInvoice again and produce a duplicate
        // draft, and Send would stay disabled because !isEditing.
        queryClient.setQueryData(['invoice', data.id], data)
        if (onCreated) onCreated(data.id)
      }
    },
    onError: (err) => setErrorMsg(errorFromResponse(err)),
  })

  const sendMutation = useMutation({
    mutationFn: () => sendInvoice(invoiceId),
    onSuccess: () => {
      invalidate()
      setConfirmSendOpen(false)
    },
    onError: (err) => {
      setConfirmSendOpen(false)
      setErrorMsg(errorFromResponse(err))
    },
  })

  const cancelMutation = useMutation({
    mutationFn: (reason) => cancelInvoice(invoiceId, reason),
    onSuccess: () => {
      invalidate()
      setConfirmCancelOpen(false)
    },
    onError: (err) => {
      setConfirmCancelOpen(false)
      setErrorMsg(errorFromResponse(err))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteInvoice(invoiceId),
    onSuccess: () => {
      invalidate()
      setConfirmDeleteOpen(false)
      onClose()
    },
    onError: (err) => {
      setConfirmDeleteOpen(false)
      setErrorMsg(errorFromResponse(err))
    },
  })

  const retryPdfMutation = useMutation({
    mutationFn: () => retryInvoicePdf(invoiceId),
    onSuccess: () => invalidate(),
    onError: (err) => setErrorMsg(errorFromResponse(err)),
  })

  const updateLine = (idx, patch) => {
    setLines((prev) => prev.map((l, i) => (i === idx ? { ...l, ...patch } : l)))
  }

  const moveLine = (idx, delta) => {
    setLines((prev) => {
      const next = [...prev]
      const target = idx + delta
      if (target < 0 || target >= next.length) return prev
      ;[next[idx], next[target]] = [next[target], next[idx]]
      return next
    })
  }

  const addLine = () => setLines((prev) => [...prev, emptyLine(lineDefaults)])
  const removeLine = (idx) => setLines((prev) => prev.filter((_, i) => i !== idx))

  const headerNumber = invoiceQuery.data?.invoice_number || (isEditing ? '—' : 'Draft')
  const dueDate = installments.length
    ? installments
        .map((i) => i.due_date)
        .sort()
        .slice(-1)[0]
    : null

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { width: { xs: '100%', md: DRAWER_WIDTH }, maxWidth: '100vw' } }}
    >
      <Box sx={{ p: 3, display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Top bar */}
        <Stack direction="row" alignItems="center" spacing={1} mb={2}>
          <Typography variant="h5" sx={{ fontWeight: 600, flexGrow: 1 }}>
            {isEditing ? 'Invoice' : 'New invoice'}
          </Typography>
          <Chip
            size="small"
            label={STATUS_LABEL[status] || status}
            color={STATUS_COLOR[status] || 'default'}
          />
          <IconButton onClick={onClose} aria-label="close">
            <CloseIcon />
          </IconButton>
        </Stack>

        {invoiceQuery.isLoading || profileQuery.isLoading ? (
          <Box sx={{ p: 6, display: 'flex', justifyContent: 'center' }}>
            <CircularProgress />
          </Box>
        ) : (
          <Stack spacing={3} sx={{ flexGrow: 1, overflowY: 'auto' }}>
            {errorMsg && (
              <Alert severity="error" onClose={() => setErrorMsg(null)}>
                {errorMsg}
              </Alert>
            )}

            {isEditing && invoiceQuery.data?.last_pdf_render_error && (
              <Alert
                severity="warning"
                action={
                  <Button
                    color="inherit"
                    size="small"
                    onClick={() => retryPdfMutation.mutate()}
                    disabled={retryPdfMutation.isPending}
                  >
                    {retryPdfMutation.isPending ? 'Retrying…' : 'Retry render'}
                  </Button>
                }
              >
                The last PDF render failed. {invoiceQuery.data.last_pdf_render_error}
              </Alert>
            )}

            {isEditing && !invoiceQuery.data?.last_pdf_render_error && (
              <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
                <Button
                  size="small"
                  variant="text"
                  onClick={() => {
                    viewInvoicePdf(invoiceId).catch((err) =>
                      setErrorMsg(errorFromResponse(err)),
                    )
                  }}
                >
                  View PDF
                </Button>
              </Box>
            )}

            {/* Header row */}
            <Paper variant="outlined" sx={{ p: 2 }}>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <Box sx={{ flex: 1 }}>
                  <Typography variant="caption" color="text.secondary">
                    Customer
                  </Typography>
                  <Typography variant="body1" sx={{ fontWeight: 500 }}>
                    {contactName || '—'}
                  </Typography>
                </Box>
                <Box sx={{ flex: 1 }}>
                  <Typography variant="caption" color="text.secondary">
                    Invoice number
                  </Typography>
                  <Typography variant="body1" sx={{ fontWeight: 500 }}>
                    {headerNumber}
                  </Typography>
                </Box>
                <TextField
                  label="Issue date"
                  type="date"
                  size="small"
                  value={issueDate}
                  onChange={(e) => setIssueDate(e.target.value)}
                  disabled={!editableHeader}
                  InputLabelProps={{ shrink: true }}
                  sx={{ minWidth: 160 }}
                />
                <Box sx={{ minWidth: 140 }}>
                  <Typography variant="caption" color="text.secondary">
                    Due date
                  </Typography>
                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                    {dueDate ? dayjs(dueDate).format('MMM D, YYYY') : 'Not set'}
                  </Typography>
                </Box>
              </Stack>
            </Paper>

            {/* Line items */}
            <Paper variant="outlined" sx={{ p: 2 }}>
              <Stack direction="row" alignItems="center" mb={1.5}>
                <Typography variant="subtitle1" sx={{ fontWeight: 600, flexGrow: 1 }}>
                  Line items
                </Typography>
                <Button
                  startIcon={<AddIcon />}
                  size="small"
                  onClick={addLine}
                  disabled={isLocked}
                >
                  Add line
                </Button>
              </Stack>
              {lines.length === 0 ? (
                <Typography color="text.secondary" sx={{ py: 2, textAlign: 'center' }}>
                  No line items yet. Click Add line to start.
                </Typography>
              ) : (
                <Stack spacing={1.5}>
                  {lines.map((line, idx) => {
                    const amounts = computeLineAmounts(line)
                    return (
                      <LineRow
                        key={line.id ?? `new-${idx}`}
                        line={line}
                        amounts={amounts}
                        isLocked={isLocked}
                        canMoveUp={idx > 0}
                        canMoveDown={idx < lines.length - 1}
                        onChange={(patch) => updateLine(idx, patch)}
                        onMove={(delta) => moveLine(idx, delta)}
                        onRemove={() => removeLine(idx)}
                      />
                    )
                  })}
                </Stack>
              )}
            </Paper>

            {/* Phase 5 plan selector. Replaces the free-form schedule
                rows with a constrained 1/2/3-payment plan; staff toggle
                "Custom amounts" only when the standard plan does not
                fit a customer. Anchor dates roll off the issue date and
                event date automatically. */}
            <Paper variant="outlined" sx={{ p: 2 }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                Payment schedule
              </Typography>
              <PlanSelector
                installments={installments}
                onInstallmentsChange={setInstallments}
                customAmounts={customAmounts}
                onCustomAmountsChange={setCustomAmounts}
                totalCents={totals.total_cents}
                issueDate={issueDate}
                eventDate={eventDate}
                defaultPlanCount={profileQuery.data?.default_payment_plan_count}
                defaultDepositPercent={profileQuery.data?.default_deposit_percent}
                disabled={isLocked}
              />
            </Paper>

            {/* Phase 7: stacked order-level discount editor + totals
                panel. Multiple presets (or custom rows) combine
                additively; combined cap of 50% is enforced on the
                server. */}
            <Paper variant="outlined" sx={{ p: 2 }}>
              <Stack spacing={1.25}>
                <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                  Order discounts
                </Typography>
                <OrderDiscountsControl
                  value={orderDiscounts}
                  onChange={setOrderDiscounts}
                  presets={allPresets}
                  disabled={!editableHeader}
                />
                {totals.discount_cents > 0 && (
                  <Typography variant="body2" color="success.main">
                    Saves {formatUSD(totals.discount_cents)}
                  </Typography>
                )}
                <Divider />
                <Row label="Subtotal" value={totals.subtotal_cents} />
                {totals.discount_cents > 0 && (
                  <Row
                    label={`Discount${
                      effectiveDiscountPercent != null
                        ? ` (${effectiveDiscountPercent}%)`
                        : ''
                    }`}
                    value={-totals.discount_cents}
                  />
                )}
                <Row label="Tax" value={totals.tax_cents} />
                <Divider />
                <Row label="Total" value={totals.total_cents} bold />
                {invoiceQuery.data?.paid_to_date_cents > 0 && (
                  <>
                    <Row label="Paid to date" value={invoiceQuery.data.paid_to_date_cents} />
                    <Row
                      label="Balance"
                      value={invoiceQuery.data.balance_cents}
                      bold
                    />
                  </>
                )}
              </Stack>
            </Paper>

            {/* More options (collapsed) */}
            <Paper variant="outlined" sx={{ p: 2 }}>
              <Button
                onClick={() => setMoreOpen((v) => !v)}
                endIcon={moreOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                size="small"
              >
                More options (terms, notes, PO number)
              </Button>
              <Collapse in={moreOpen}>
                <Stack spacing={2} sx={{ mt: 2 }}>
                  <TextField
                    label="Payment terms"
                    multiline
                    minRows={2}
                    value={terms}
                    onChange={(e) => setTerms(e.target.value)}
                    disabled={isLocked}
                  />
                  <TextField
                    label="Footer"
                    multiline
                    minRows={2}
                    value={footer}
                    onChange={(e) => setFooter(e.target.value)}
                    disabled={isLocked}
                  />
                  <TextField
                    label="Public notes (visible to customer)"
                    multiline
                    minRows={2}
                    value={publicNotes}
                    onChange={(e) => setPublicNotes(e.target.value)}
                    disabled={isLocked}
                  />
                  <TextField
                    label="Private notes (staff only)"
                    multiline
                    minRows={2}
                    value={privateNotes}
                    onChange={(e) => setPrivateNotes(e.target.value)}
                    disabled={isLocked}
                  />
                  <TextField
                    label="PO number"
                    value={poNumber}
                    onChange={(e) => setPoNumber(e.target.value)}
                    disabled={isLocked}
                  />
                </Stack>
              </Collapse>
            </Paper>
          </Stack>
        )}

        {/* Action bar */}
        <Box
          sx={{
            mt: 2,
            pt: 2,
            borderTop: '1px solid',
            borderColor: 'divider',
            display: 'flex',
            justifyContent: 'space-between',
            gap: 1,
            flexWrap: 'wrap',
          }}
        >
          <Stack direction="row" spacing={1}>
            {canDeleteDraft && (
              <Button
                color="error"
                onClick={() => setConfirmDeleteOpen(true)}
                disabled={deleteMutation.isPending}
              >
                Delete
              </Button>
            )}
            {isSentOrPartial && (
              <Button
                color="warning"
                onClick={() => setConfirmCancelOpen(true)}
                disabled={cancelMutation.isPending}
              >
                Cancel invoice
              </Button>
            )}
          </Stack>
          <Stack direction="row" spacing={1}>
            <Button onClick={onClose}>Close</Button>
            <Button
              variant="outlined"
              onClick={() => saveMutation.mutate()}
              disabled={isLocked || saveMutation.isPending}
            >
              {saveMutation.isPending ? 'Saving…' : isEditing ? 'Save' : 'Save draft'}
            </Button>
            {(status === 'draft' || !isEditing) && (
              <Button
                variant="contained"
                onClick={() => setConfirmSendOpen(true)}
                disabled={sendDisabled || sendMutation.isPending || !isEditing}
              >
                Send
              </Button>
            )}
          </Stack>
        </Box>
      </Box>

      <ConfirmDialog
        open={confirmSendOpen}
        title="Send invoice?"
        message={`This locks the invoice number and stamps it as sent. Customer will see this invoice at their portal link.`}
        confirmLabel="Send"
        onConfirm={() => sendMutation.mutate()}
        onCancel={() => setConfirmSendOpen(false)}
        pending={sendMutation.isPending}
      />
      <ConfirmDialog
        open={confirmCancelOpen}
        title="Cancel this invoice?"
        message={`The invoice number stays on the books for the audit trail. The status will flip to Cancelled and no further edits are allowed.`}
        confirmLabel="Cancel invoice"
        onConfirm={() => cancelMutation.mutate(null)}
        onCancel={() => setConfirmCancelOpen(false)}
        pending={cancelMutation.isPending}
      />
      <ConfirmDialog
        open={confirmDeleteOpen}
        title="Delete this draft?"
        message={
          invoiceQuery.data?.source_quote_number
            ? `The draft will be removed. Quote ${invoiceQuery.data.source_quote_number} will return to Approved so it can be converted again.`
            : 'The draft will be removed. Drafts have no invoice number and leave no trace.'
        }
        confirmLabel="Delete"
        onConfirm={() => deleteMutation.mutate()}
        onCancel={() => setConfirmDeleteOpen(false)}
        pending={deleteMutation.isPending}
      />
    </Drawer>
  )
}

function LineRow({
  line,
  amounts,
  isLocked,
  canMoveUp,
  canMoveDown,
  onChange,
  onMove,
  onRemove,
}) {
  const isCatalog = !!line.catalog_item_id
  const customerPreview = isCatalog
    ? customerLineDescription(line.catalog, line.size_label)
    : ''
  const leak = !isCatalog
    ? null
    : detectCatalogLeak(line.catalog, line.public_description) ||
      detectCatalogLeak(line.catalog, line.description)

  return (
    <Paper
      variant="outlined"
      sx={{
        p: 1.5,
        borderColor: isCatalog ? 'primary.light' : 'divider',
      }}
    >
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns:
            '40px 1fr 80px 130px 110px auto auto',
          gap: 1,
          alignItems: 'center',
          mb: 1,
        }}
      >
        <Stack>
          <IconButton
            size="small"
            onClick={() => onMove(-1)}
            disabled={!canMoveUp || isLocked}
          >
            <ArrowUpwardIcon fontSize="small" />
          </IconButton>
          <IconButton
            size="small"
            onClick={() => onMove(1)}
            disabled={!canMoveDown || isLocked}
          >
            <ArrowDownwardIcon fontSize="small" />
          </IconButton>
        </Stack>

        {/* Identifier slot. Catalog-backed lines lock this to the
            catalog row and surface the staff SKU + public BVX code.
            Non-catalog lines edit the customer-facing public copy
            directly. */}
        {isCatalog ? (
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {line.catalog?.internal_sku || `Catalog #${line.catalog_item_id}`}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {line.catalog?.public_code} ·{' '}
              {customerPreview || 'no customer copy yet'}
            </Typography>
          </Box>
        ) : (
          <TextField
            size="small"
            placeholder="Customer description"
            value={line.public_description || line.description || ''}
            onChange={(e) =>
              onChange({
                public_description: e.target.value,
                description: e.target.value,
              })
            }
            disabled={isLocked}
          />
        )}

        <TextField
          size="small"
          type="text"
          inputProps={{ inputMode: 'numeric', pattern: '[0-9]*' }}
          value={line.quantity}
          onChange={(e) =>
            onChange({ quantity: normalizeQuantityInput(e.target.value) })
          }
          onBlur={() => {
            if (!line.quantity) onChange({ quantity: '1' })
          }}
          disabled={isLocked}
          label="Qty"
          InputLabelProps={{ shrink: true }}
        />
        <CurrencyInput
          value={line.unit_price_cents}
          onChange={(v) => onChange({ unit_price_cents: v ?? 0 })}
          disabled={isLocked}
          label="Unit"
          inputProps={{ 'aria-label': 'unit price' }}
        />
        <TaxRateInput
          value={line.tax_rate}
          onChange={(rate) => onChange({ tax_rate: rate })}
          disabled={isLocked}
          label="Tax"
        />
        <Typography
          variant="body2"
          sx={{ minWidth: 90, textAlign: 'right', fontWeight: 500 }}
        >
          {formatUSD(amounts.line_total_cents)}
        </Typography>
        <IconButton
          size="small"
          onClick={onRemove}
          disabled={isLocked}
          aria-label="remove line"
        >
          <DeleteOutlineIcon fontSize="small" />
        </IconButton>
      </Box>

      {/* Per-line discount slider. Default state shows just an
          "Apply discount" button so unaffected lines stay clean; the
          slider expands inline once staff opt in. */}
      <Box sx={{ mb: 1 }}>
        <LineDiscountControl
          line={line}
          onChange={onChange}
          disabled={isLocked}
        />
      </Box>

      {/* Catalog picker + size label row. Showing this whether or not
          the line is catalog-backed lets staff promote a free-text
          line into a catalog-backed one in place. */}
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} alignItems="flex-start">
        <Box sx={{ flex: 1 }}>
          <CatalogPicker
            disabled={isLocked}
            value={line.catalog}
            onChange={(snapshot) => {
              if (snapshot) {
                // Promote to catalog-backed: clear the customer copy
                // (the catalog row owns it now) so the API doesn't
                // 422 with `catalog_line_legacy_text`.
                const patch = {
                  catalog_item_id: snapshot.id,
                  catalog: snapshot,
                  public_description: '',
                  description: '',
                  notes: null,
                }
                if (
                  typeof snapshot.unit_price_cents === 'number' &&
                  snapshot.unit_price_cents >= 0
                ) {
                  patch.unit_price_cents = snapshot.unit_price_cents
                }
                onChange(patch)
              } else {
                onChange({
                  catalog_item_id: null,
                  catalog: null,
                  size_label: '',
                })
              }
            }}
          />
        </Box>
        {isCatalog && (
          <TextField
            size="small"
            label="Size"
            placeholder="08, 10, L…"
            value={line.size_label || ''}
            onChange={(e) => onChange({ size_label: e.target.value })}
            disabled={isLocked}
            InputLabelProps={{ shrink: true }}
            sx={{ width: 120 }}
          />
        )}
        <TextField
          size="small"
          label="Internal notes (staff only)"
          value={line.internal_notes || ''}
          onChange={(e) => onChange({ internal_notes: e.target.value })}
          disabled={isLocked}
          InputLabelProps={{ shrink: true }}
          multiline
          maxRows={3}
          sx={{ flex: 1 }}
        />
      </Stack>

      {leak && (
        <Alert severity="warning" sx={{ mt: 1 }}>
          Customer-facing text contains the catalog{' '}
          {leak.kind.replace('_', ' ')} ({leak.value}). Remove it before
          saving — the server will reject it.
        </Alert>
      )}
    </Paper>
  )
}

function Row({ label, value, bold }) {
  return (
    <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
      <Typography variant="body2" sx={{ color: 'text.secondary', fontWeight: bold ? 600 : 400 }}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ fontWeight: bold ? 600 : 400 }}>
        {formatUSD(value)}
      </Typography>
    </Box>
  )
}
