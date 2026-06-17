import { useEffect, useMemo, useRef, useState } from 'react'
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
  Tooltip,
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
  approveQuoteInStore,
  cancelQuote,
  convertQuoteToInvoice,
  createQuote,
  deleteQuote,
  getBusinessProfile,
  getQuote,
  rejectQuote,
  retryQuotePdf,
  sendQuote,
  updateQuote,
  viewQuotePdf,
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
import SignatureDialog from './SignatureDialog'
import TaxRateInput from './TaxRateInput'

const DRAWER_WIDTH = 920

// Quote-specific status colors. Differs from invoice status set —
// quotes have approved/rejected/converted/expired instead of partial/paid.
const STATUS_COLOR = {
  draft: 'default',
  sent: 'primary',
  approved: 'success',
  rejected: 'default',
  converted: 'info',
  expired: 'warning',
  cancelled: 'default',
}

const STATUS_LABEL = {
  draft: 'Draft',
  sent: 'Sent',
  approved: 'Approved',
  rejected: 'Rejected',
  converted: 'Converted',
  expired: 'Expired',
  cancelled: 'Cancelled',
}

const LINE_KINDS = [
  { value: 'product', label: 'Product' },
  { value: 'service', label: 'Service' },
  { value: 'alteration', label: 'Alteration' },
  { value: 'fee', label: 'Fee' },
]

// Statuses where editing is locked (matches backend _LOCKED_STATUSES).
const LOCKED = new Set(['approved', 'rejected', 'converted', 'expired', 'cancelled'])

// ---------------------------------------------------------------------------
// Pure helpers — same banker's rounding as invoice editor so the math
// agrees with the service's Decimal computation by construction.
// ---------------------------------------------------------------------------

function bankRound(value) {
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
  return {
    line_pre_order_cents: linePreOrder,
    line_subtotal_cents: subtotal,
    line_tax_cents: tax,
    line_total_cents: subtotal + tax,
  }
}

// Phase 2a money math, mirrors InvoiceEditor.computeTotals.
function computeTotals(lines, orderDiscountPercent = null, legacyDiscountCents = 0) {
  let preOrderSubtotal = 0
  let lineTaxSum = 0
  let lineTotalSum = 0
  let lineDiscountTotal = 0
  for (const line of lines) {
    const a = computeLineAmounts(line, orderDiscountPercent)
    preOrderSubtotal += a.line_pre_order_cents
    lineTaxSum += a.line_tax_cents
    lineTotalSum += a.line_total_cents
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

export default function QuoteEditor({
  open,
  onClose,
  onCreated,
  onConverted,
  eventId,
  eventDate,
  contactId,
  contactName,
  quoteId,
}) {
  const queryClient = useQueryClient()
  const isEditing = quoteId != null

  const quoteQuery = useQuery({
    queryKey: ['quote', quoteId],
    queryFn: () => getQuote(quoteId),
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

  // Local form state. Lines are an array; expires_at and other scalars
  // are flat strings. We seed from the quote query when editing or from
  // empty defaults when creating.
  const [lines, setLines] = useState([emptyLine()])
  const [installments, setInstallments] = useState([])
  const [issueDate, setIssueDate] = useState(dayjs().format('YYYY-MM-DD'))
  const [expiresAt, setExpiresAt] = useState(
    dayjs().add(30, 'day').format('YYYY-MM-DD'),
  )
  const [terms, setTerms] = useState('')
  const [footer, setFooter] = useState('')
  const [publicNotes, setPublicNotes] = useState('')
  const [privateNotes, setPrivateNotes] = useState('')
  const [poNumber, setPoNumber] = useState('')
  const [moreOpen, setMoreOpen] = useState(false)
  const [confirm, setConfirm] = useState(null)
  const [error, setError] = useState(null)
  // Phase 7: stacked order-level discounts.
  const [orderDiscounts, setOrderDiscounts] = useState([])
  // Phase 5 plan selector escape hatch. Per-write flag, not persisted.
  const [customAmounts, setCustomAmounts] = useState(false)

  // Hydrate local state when a quote loads. In create mode we seed once
  // per open (after the profile arrives so the default tax rate is
  // applied); a ref prevents a profile refetch from later clobbering the
  // user's edits. Edit mode re-hydrates whenever quoteQuery.data changes
  // — that's how post-save refetches refresh the form.
  const hasSeededCreateRef = useRef(false)
  useEffect(() => {
    if (!open) {
      hasSeededCreateRef.current = false
      return
    }
    if (isEditing) {
      const q = quoteQuery.data
      if (!q) return
      setLines((q.line_items || []).map(hydrateLineFromInvoice))
      setInstallments(
        (q.installments || []).map((inst) => ({
          id: inst.id,
          label: inst.label || '',
          amount_cents: inst.amount_cents,
          due_date: inst.due_date,
        })),
      )
      setIssueDate(q.issue_date || dayjs().format('YYYY-MM-DD'))
      setExpiresAt(q.expires_at || '')
      setTerms(q.terms || '')
      setFooter(q.footer || '')
      setPublicNotes(q.public_notes || '')
      setPrivateNotes(q.private_notes || '')
      setPoNumber(q.po_number || '')
      setOrderDiscounts(
        (q.order_discounts || []).map((od) => ({
          kind: od.preset_id ? 'preset' : 'custom',
          preset_id: od.preset_id || undefined,
          label: od.label,
          percent: Number(od.percent),
        })),
      )
      setCustomAmounts(false)
      setError(null)
      return
    }
    if (hasSeededCreateRef.current) return
    if (!profileQuery.data) return
    setLines([emptyLine(lineDefaults)])
    setInstallments([])
    setIssueDate(dayjs().format('YYYY-MM-DD'))
    setExpiresAt(dayjs().add(30, 'day').format('YYYY-MM-DD'))
    setTerms('')
    setFooter('')
    setPublicNotes('')
    setPrivateNotes('')
    setPoNumber('')
    setOrderDiscounts([])
    setCustomAmounts(false)
    setError(null)
    hasSeededCreateRef.current = true
  }, [open, isEditing, quoteQuery.data, profileQuery.data, lineDefaults])

  const allPresets = useMemo(
    () => profileQuery.data?.discount_presets || [],
    [profileQuery.data?.discount_presets],
  )

  // Phase 7: combined percent across the discount stack.
  const effectiveDiscountPercent = useMemo(() => {
    if (orderDiscounts.length === 0) return null
    const sum = orderDiscounts.reduce(
      (acc, row) => acc + (Number(row.percent) || 0),
      0,
    )
    return sum > 0 ? sum : null
  }, [orderDiscounts])

  const legacyDiscountCents =
    orderDiscounts.length === 0 &&
    !(quoteQuery.data?.order_discounts || []).length
      ? Number(quoteQuery.data?.discount_cents || 0)
      : 0

  const totals = useMemo(
    () => computeTotals(lines, effectiveDiscountPercent, legacyDiscountCents),
    [lines, effectiveDiscountPercent, legacyDiscountCents],
  )
  const quote = quoteQuery.data
  const isLocked = quote && LOCKED.has(quote.status)
  const isDraft = !quote || quote.status === 'draft'
  const isSent = quote?.status === 'sent'
  const isApproved = quote?.status === 'approved'
  const isConverted = quote?.status === 'converted'

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['event', eventId, 'quotes'] })
    if (quoteId) {
      queryClient.invalidateQueries({ queryKey: ['quote', quoteId] })
    }
    // Phase 9: every quote mutation emits an activity row.
    queryClient.invalidateQueries({ queryKey: ['event', eventId, 'activity'] })
    // Phase 10: send / approve / reject / cancel / convert all change
    // the awaiting-signature widget; convert also creates an invoice
    // that shifts AR. Bust all dashboard widgets.
    queryClient.invalidateQueries({ queryKey: ['dashboard'] })
  }

  // A line is "empty" — and so safe to drop on save — when it has no
  // catalog selection AND no customer copy. Pre-Phase-3 the test was
  // just `description.trim()`; with catalog-backed lines the customer
  // copy is derived from the catalog row at render time so we can't
  // rely on description alone.
  const isLineEmpty = (l) =>
    !l.catalog_item_id &&
    !(l.public_description || '').trim() &&
    !(l.description || '').trim()

  const buildOrderDiscountsPayload = () =>
    orderDiscounts.map((row) =>
      row.kind === 'preset'
        ? { preset_id: row.preset_id }
        : {
            label: (row.label || 'Custom').trim() || 'Custom',
            percent: row.percent === '' ? null : row.percent,
          },
    )

  const buildPayload = () => {
    const body = {
      contact_id: contactId,
      line_items: lines
        .filter((l) => !isLineEmpty(l))
        .map((l, idx) => serializeLineForApi(l, idx)),
      // Phase 4 schedule. Empty list = no schedule on this draft;
      // converted invoice falls back to the legacy 50/50 default.
      installments: installments.map((inst, idx) => ({
        label: inst.label || null,
        amount_cents: inst.amount_cents,
        due_date: inst.due_date,
        sort_order: idx,
      })),
      // Phase 7 stacked discounts.
      order_discounts: buildOrderDiscountsPayload(),
      // Phase 5 plan validity flag.
      custom_amounts: customAmounts,
      issue_date: issueDate || null,
      expires_at: expiresAt || null,
      terms: terms || null,
      footer: footer || null,
      public_notes: publicNotes || null,
      private_notes: privateNotes || null,
      po_number: poNumber || null,
    }
    if (orderDiscounts.length === 0) body.discount_cents = 0
    return body
  }

  const saveMutation = useMutation({
    mutationFn: () =>
      isEditing
        ? updateQuote(quoteId, buildPayload())
        : createQuote(eventId, buildPayload()),
    onSuccess: (data) => {
      invalidate()
      if (!isEditing) {
        queryClient.setQueryData(['quote', data.id], data)
        if (onCreated) onCreated(data.id)
      }
      setError(null)
    },
    onError: (err) => {
      setError(parseApiError(err))
    },
  })

  const sendMutation = useMutation({
    mutationFn: async () => {
      // Save first, then send.
      let id = quoteId
      if (!isEditing) {
        const created = await createQuote(eventId, buildPayload())
        id = created.id
        queryClient.setQueryData(['quote', id], created)
        if (onCreated) onCreated(id)
      } else {
        await updateQuote(quoteId, buildPayload())
      }
      return sendQuote(id)
    },
    onSuccess: () => {
      invalidate()
      setError(null)
    },
    onError: (err) => setError(parseApiError(err)),
  })

  const cancelMutation = useMutation({
    mutationFn: (reason) => cancelQuote(quoteId, reason),
    onSuccess: () => {
      invalidate()
      setConfirm(null)
    },
    onError: (err) => {
      setError(parseApiError(err))
      setConfirm(null)
    },
  })

  const rejectMutation = useMutation({
    mutationFn: (reason) => rejectQuote(quoteId, reason),
    onSuccess: () => {
      invalidate()
      setConfirm(null)
    },
    onError: (err) => {
      setError(parseApiError(err))
      setConfirm(null)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteQuote(quoteId),
    onSuccess: () => {
      invalidate()
      setConfirm(null)
      onClose()
    },
    onError: (err) => {
      setError(parseApiError(err))
      setConfirm(null)
    },
  })

  const convertMutation = useMutation({
    mutationFn: () => convertQuoteToInvoice(quoteId),
    onSuccess: (newInvoice) => {
      invalidate()
      queryClient.invalidateQueries({
        queryKey: ['event', eventId, 'invoices'],
      })
      queryClient.setQueryData(['invoice', newInvoice.id], newInvoice)
      onClose()
      if (onConverted) onConverted(newInvoice.id)
    },
    onError: (err) => setError(parseApiError(err)),
  })

  const retryPdfMutation = useMutation({
    mutationFn: () => retryQuotePdf(quoteId),
    onSuccess: () => invalidate(),
    onError: (err) => setError(parseApiError(err)),
  })

  const [signOpen, setSignOpen] = useState(false)
  const [signError, setSignError] = useState(null)
  const signInStoreMutation = useMutation({
    mutationFn: ({ signatureName, signatureBase64 }) =>
      approveQuoteInStore(quoteId, { signatureName, signatureBase64 }),
    onSuccess: () => {
      invalidate()
      setSignOpen(false)
      setSignError(null)
    },
    onError: (err) => setSignError(parseApiError(err)),
  })

  const addLine = () => setLines([...lines, emptyLine(lineDefaults)])
  const removeLine = (idx) => setLines(lines.filter((_, i) => i !== idx))

  const moveLine = (idx, dir) => {
    const next = [...lines]
    const target = idx + dir
    if (target < 0 || target >= next.length) return
    ;[next[idx], next[target]] = [next[target], next[idx]]
    setLines(next)
  }
  const updateLine = (idx, patch) => {
    const next = [...lines]
    next[idx] = { ...next[idx], ...patch }
    setLines(next)
  }

  // Send is allowed once the quote has at least one non-empty line.
  // Either a catalog-backed line OR a non-catalog line with customer
  // copy counts; the back-compat `description` field still satisfies
  // the check for staff who haven't moved to the picker yet.
  const canSend =
    isDraft &&
    lines.some(
      (l) =>
        !isLineEmpty(l) && Number(l.unit_price_cents || 0) >= 0,
    ) &&
    totals.total_cents >= 0
  const canSave = isDraft || isSent
  // In-store signing is the primary close path. Allowed on draft (with
  // at least one valid line) or sent (already validated server-side).
  // Requires the quote to exist server-side first — saving a brand-new
  // unsaved draft is the user's prerequisite, mirroring the Send flow.
  const canSignInStore = !!quoteId && (canSend || isSent)

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { width: { xs: '100%', md: DRAWER_WIDTH } } }}
    >
      <Box sx={{ p: 3, display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Header */}
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          mb={2}
        >
          <Stack direction="row" alignItems="center" spacing={2}>
            <Typography variant="h5" sx={{ fontWeight: 600 }}>
              {isEditing ? quote?.quote_number || 'Quote' : 'New quote'}
            </Typography>
            {quote && (
              <Chip
                size="small"
                label={STATUS_LABEL[quote.status] || quote.status}
                color={STATUS_COLOR[quote.status] || 'default'}
              />
            )}
            {isConverted && quote.converted_invoice_id && (
              <Chip
                size="small"
                variant="outlined"
                label={`→ Invoice #${quote.converted_invoice_id}`}
              />
            )}
          </Stack>
          <IconButton onClick={onClose}>
            <CloseIcon />
          </IconButton>
        </Stack>

        {error && (
          <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {isEditing && quoteQuery.data?.last_pdf_render_error && (
          <Alert
            severity="warning"
            sx={{ mb: 2 }}
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
            The last PDF render failed. {quoteQuery.data.last_pdf_render_error}
          </Alert>
        )}

        {isEditing && !quoteQuery.data?.last_pdf_render_error && (
          <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
            <Button
              size="small"
              variant="text"
              onClick={() => {
                viewQuotePdf(quoteId).catch((err) =>
                  setError(parseApiError(err)),
                )
              }}
            >
              View PDF
            </Button>
          </Box>
        )}

        {isEditing && quoteQuery.isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
            <CircularProgress />
          </Box>
        ) : (
          <Box sx={{ flex: 1, overflowY: 'auto', pr: 1 }}>
            {/* Header fields */}
            <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <TextField
                  label="Customer"
                  value={contactName || ''}
                  size="small"
                  disabled
                  sx={{ flex: 1 }}
                />
                <TextField
                  label="Issue date"
                  type="date"
                  size="small"
                  value={issueDate}
                  onChange={(e) => setIssueDate(e.target.value)}
                  disabled={isLocked}
                  InputLabelProps={{ shrink: true }}
                  sx={{ width: 170 }}
                />
                <TextField
                  label="Expires"
                  type="date"
                  size="small"
                  value={expiresAt || ''}
                  onChange={(e) => setExpiresAt(e.target.value)}
                  disabled={isLocked}
                  InputLabelProps={{ shrink: true }}
                  sx={{ width: 170 }}
                />
              </Stack>
            </Paper>

            {/* Line items */}
            <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
              <Stack
                direction="row"
                justifyContent="space-between"
                alignItems="center"
                mb={1}
              >
                <Typography variant="subtitle2">Line items</Typography>
                {!isLocked && (
                  <Button
                    size="small"
                    startIcon={<AddIcon />}
                    onClick={addLine}
                  >
                    Add line
                  </Button>
                )}
              </Stack>
              <Box>
                {lines.map((line, idx) => (
                  <LineRow
                    key={idx}
                    line={line}
                    canMoveUp={idx > 0}
                    canMoveDown={idx < lines.length - 1}
                    onMove={(dir) => moveLine(idx, dir)}
                    onRemove={() => removeLine(idx)}
                    onChange={(patch) => updateLine(idx, patch)}
                    locked={isLocked}
                  />
                ))}
              </Box>
            </Paper>

            {/* Phase 7: stacked order-level discount editor + totals
                panel. */}
            <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
              <Stack spacing={1.25}>
                <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                  Order discounts
                </Typography>
                <OrderDiscountsControl
                  value={orderDiscounts}
                  onChange={setOrderDiscounts}
                  presets={allPresets}
                  disabled={isLocked}
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
              </Stack>
            </Paper>

            {/* Phase 5 plan selector. The same constrained 1/2/3-payment
                surface as the invoice editor; the schedule rides into
                the converted invoice verbatim and the deposit floor
                holds across both surfaces. */}
            <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
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

            {/* More options */}
            <Button
              size="small"
              onClick={() => setMoreOpen(!moreOpen)}
              endIcon={moreOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
              sx={{ mb: 1 }}
            >
              More options (terms, notes, PO)
            </Button>
            <Collapse in={moreOpen}>
              <Stack spacing={2} sx={{ mb: 2 }}>
                <TextField
                  label="Terms"
                  multiline
                  minRows={2}
                  size="small"
                  value={terms}
                  onChange={(e) => setTerms(e.target.value)}
                  disabled={isLocked}
                />
                <TextField
                  label="Footer"
                  multiline
                  minRows={2}
                  size="small"
                  value={footer}
                  onChange={(e) => setFooter(e.target.value)}
                  disabled={isLocked}
                />
                <TextField
                  label="Public notes (visible to customer)"
                  multiline
                  minRows={2}
                  size="small"
                  value={publicNotes}
                  onChange={(e) => setPublicNotes(e.target.value)}
                  disabled={isLocked}
                />
                <TextField
                  label="Private notes (staff only)"
                  multiline
                  minRows={2}
                  size="small"
                  value={privateNotes}
                  onChange={(e) => setPrivateNotes(e.target.value)}
                  disabled={isLocked}
                />
                <TextField
                  label="PO number"
                  size="small"
                  value={poNumber}
                  onChange={(e) => setPoNumber(e.target.value)}
                  disabled={isLocked}
                />
              </Stack>
            </Collapse>
          </Box>
        )}

        {/* Action bar */}
        <Box
          sx={{
            borderTop: 1,
            borderColor: 'divider',
            pt: 2,
            mt: 2,
            display: 'flex',
            gap: 1,
            flexWrap: 'wrap',
          }}
        >
          <Button onClick={onClose}>Close</Button>
          <Box sx={{ flex: 1 }} />
          {isEditing && isDraft && (
            <Tooltip title="Permanently remove this draft">
              <Button
                color="error"
                onClick={() =>
                  setConfirm({
                    title: 'Delete draft',
                    message: 'This draft will be removed. You cannot undo this.',
                    confirmLabel: 'Delete draft',
                    onConfirm: () => deleteMutation.mutate(),
                  })
                }
              >
                Delete
              </Button>
            </Tooltip>
          )}
          {isSent && (
            <Button
              color="error"
              onClick={() =>
                setConfirm({
                  title: 'Reject quote',
                  message:
                    'Mark this quote as rejected. The portal link will stop accepting an approval.',
                  confirmLabel: 'Reject',
                  onConfirm: () => rejectMutation.mutate(null),
                })
              }
            >
              Reject
            </Button>
          )}
          {isSent && (
            <Button
              onClick={() =>
                setConfirm({
                  title: 'Cancel quote',
                  message:
                    'Cancellation preserves the quote number and stops the portal link from accepting approvals.',
                  confirmLabel: 'Cancel quote',
                  onConfirm: () => cancelMutation.mutate(null),
                })
              }
            >
              Cancel quote
            </Button>
          )}
          {canSave && (
            <Button
              variant="outlined"
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? 'Saving…' : 'Save draft'}
            </Button>
          )}
          {canSend && (
            <Button
              variant="outlined"
              onClick={() => sendMutation.mutate()}
              disabled={sendMutation.isPending}
            >
              {sendMutation.isPending ? 'Emailing…' : 'Email quote'}
            </Button>
          )}
          {canSignInStore && (
            <Button
              variant="contained"
              color="success"
              onClick={() => {
                setSignError(null)
                setSignOpen(true)
              }}
            >
              Sign in store
            </Button>
          )}
          {isApproved && (
            <Button
              variant="contained"
              color="success"
              onClick={() => convertMutation.mutate()}
              disabled={convertMutation.isPending}
            >
              {convertMutation.isPending ? 'Converting…' : 'Convert to invoice'}
            </Button>
          )}
        </Box>
      </Box>

      <ConfirmDialog
        open={!!confirm}
        title={confirm?.title}
        message={confirm?.message}
        confirmLabel={confirm?.confirmLabel}
        onCancel={() => setConfirm(null)}
        onConfirm={() => confirm?.onConfirm?.()}
      />

      <SignatureDialog
        open={signOpen}
        onClose={() => {
          setSignOpen(false)
          setSignError(null)
        }}
        onSubmit={(payload) => signInStoreMutation.mutate(payload)}
        submitting={signInStoreMutation.isPending}
        errorMessage={signError}
        customerName={contactName || ''}
      />
    </Drawer>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function LineRow({ line, canMoveUp, canMoveDown, onMove, onRemove, onChange, locked }) {
  const amounts = computeLineAmounts(line)
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
        mb: 1,
        borderColor: isCatalog ? 'primary.light' : 'divider',
      }}
    >
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: 'auto 2fr 80px 110px 100px 100px auto',
          gap: 1,
          alignItems: 'center',
          mb: 1,
        }}
      >
        <Stack direction="column" sx={{ width: 28 }}>
          <IconButton
            size="small"
            onClick={() => onMove(-1)}
            disabled={!canMoveUp || locked}
          >
            <ArrowUpwardIcon fontSize="inherit" />
          </IconButton>
          <IconButton
            size="small"
            onClick={() => onMove(1)}
            disabled={!canMoveDown || locked}
          >
            <ArrowDownwardIcon fontSize="inherit" />
          </IconButton>
        </Stack>

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
            disabled={locked}
          />
        )}

        <TextField
          size="small"
          type="text"
          placeholder="Qty"
          value={line.quantity}
          onChange={(e) =>
            onChange({ quantity: normalizeQuantityInput(e.target.value) })
          }
          onBlur={() => {
            if (!line.quantity) onChange({ quantity: '1' })
          }}
          disabled={locked}
          inputProps={{ inputMode: 'numeric', pattern: '[0-9]*' }}
        />
        <CurrencyInput
          value={line.unit_price_cents || 0}
          onChange={(v) => onChange({ unit_price_cents: v ?? 0 })}
          disabled={locked}
          label="Unit"
        />
        <TaxRateInput
          value={line.tax_rate}
          onChange={(rate) => onChange({ tax_rate: rate })}
          disabled={locked}
          placeholder="Tax"
        />
        <Box sx={{ textAlign: 'right' }}>
          <Typography variant="caption" color="text.secondary" display="block">
            Line total
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 500 }}>
            {formatUSD(amounts.line_total_cents)}
          </Typography>
        </Box>
        <Tooltip title="Remove line">
          <span>
            <IconButton
              size="small"
              onClick={onRemove}
              disabled={locked}
            >
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Box>

      {/* Per-line discount slider. Same opt-in UX as the invoice
          editor — the column is hidden behind an "Apply discount"
          button until staff need it. */}
      <Box sx={{ mb: 1 }}>
        <LineDiscountControl
          line={line}
          onChange={onChange}
          disabled={locked}
        />
      </Box>

      <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} alignItems="flex-start">
        <Box sx={{ flex: 1 }}>
          <CatalogPicker
            disabled={locked}
            value={line.catalog}
            onChange={(snapshot) => {
              if (snapshot) {
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
            disabled={locked}
            InputLabelProps={{ shrink: true }}
            sx={{ width: 120 }}
          />
        )}
        <TextField
          size="small"
          label="Internal notes (staff only)"
          value={line.internal_notes || ''}
          onChange={(e) => onChange({ internal_notes: e.target.value })}
          disabled={locked}
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
    <Stack direction="row" justifyContent="space-between" sx={{ py: 0.5 }}>
      <Typography
        variant="body2"
        sx={{ fontWeight: bold ? 700 : 400 }}
      >
        {label}
      </Typography>
      <Typography
        variant="body2"
        sx={{ fontWeight: bold ? 700 : 500 }}
      >
        {formatUSD(value)}
      </Typography>
    </Stack>
  )
}

function parseApiError(err) {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.code) {
    const map = {
      line_items_required: 'Add at least one line item.',
      invalid_transition: 'Cannot perform this action on the quote in its current status.',
      cancel_draft_not_allowed: 'Drafts cannot be cancelled — delete them instead.',
      quote_locked: 'This quote is locked against further edits.',
      signature_required: 'Signature is required to approve.',
      // Phase 2 catalog rejections.
      catalog_line_legacy_text:
        'Catalog-backed line cannot have customer description, description, or notes — clear those fields.',
      catalog_leak: detail.identifier_kind
        ? `Customer copy must not contain the catalog ${detail.identifier_kind}; edit the line.`
        : 'Customer copy contains a catalog identifier — edit the line.',
      public_description_required: 'Add a customer description for non-catalog lines.',
      line_public_description_conflict:
        'Customer description conflicts with the legacy field — clear one.',
      catalog_item_not_found: 'Selected catalog item could not be found.',
      catalog_item_inactive:
        'Selected catalog item is inactive. Pick an active row or reactivate it.',
      // Phase 5 plan validity rejections.
      plan_count_invalid: 'Payment plan must be 1, 2, or 3 installments.',
      deposit_below_floor:
        'Deposit must be at least 50% of the total. Raise the deposit or toggle "Custom amounts" to override.',
    }
    return map[detail.code] || `Error: ${detail.code}`
  }
  return err?.message || 'An unexpected error occurred.'
}
