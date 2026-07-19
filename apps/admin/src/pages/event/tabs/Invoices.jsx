import { useEffect, useMemo, useRef, useState } from 'react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Snackbar,
  Stack,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import AttachFileIcon from '@mui/icons-material/AttachFile'
import DownloadIcon from '@mui/icons-material/Download'
import ReceiptLongOutlinedIcon from '@mui/icons-material/ReceiptLongOutlined'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'

import InvoiceEditor from '../../../components/InvoiceEditor'
import {
  downloadDocument,
  listEventDocuments,
  listInvoices,
  uploadEventDocument,
  viewInvoicePdf,
} from '../../../services/api'
import { formatUSD } from '../../../utils/money'

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

export default function Invoices() {
  const { event } = useOutletContext()
  const eventId = event.id
  const contactId = event.primary_contact?.id
  const contactName = event.primary_contact?.display_name
  const queryClient = useQueryClient()

  const invoicesQuery = useQuery({
    queryKey: ['event', eventId, 'invoices'],
    queryFn: () => listInvoices(eventId),
  })

  // Phase 4b: list external_invoice attachments alongside the canonical
  // invoices so freshly uploaded vendor PDFs show up immediately.
  const attachmentsQuery = useQuery({
    queryKey: ['event', eventId, 'documents', { kind: 'external_invoice' }],
    queryFn: () => listEventDocuments(eventId, 'external_invoice'),
  })

  const [editorOpen, setEditorOpen] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [snack, setSnack] = useState(null)
  const fileInputRef = useRef(null)
  const [searchParams, setSearchParams] = useSearchParams()

  // Phase 10.6: ?edit=<invoiceId> deep-link. Lets the Overview tab's
  // buyer-journey rows open this tab and pop the editor directly. We
  // strip the param after consuming it so a refresh doesn't reopen.
  useEffect(() => {
    const editParam = searchParams.get('edit')
    if (!editParam) return
    const idNum = Number(editParam)
    if (Number.isFinite(idNum)) {
      setEditingId(idNum)
      setEditorOpen(true)
    }
    const next = new URLSearchParams(searchParams)
    next.delete('edit')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  const invoices = useMemo(() => invoicesQuery.data || [], [invoicesQuery.data])
  const attachments = useMemo(
    () => attachmentsQuery.data || [],
    [attachmentsQuery.data],
  )

  const totals = useMemo(() => {
    let billed = 0
    let paid = 0
    let outstanding = 0
    for (const inv of invoices) {
      if (inv.status === 'cancelled' || inv.status === 'reversed') continue
      billed += inv.total_cents
      paid += inv.paid_to_date_cents
      if (inv.status === 'sent' || inv.status === 'partial') {
        outstanding += inv.balance_cents
      }
    }
    return { billed, paid, outstanding }
  }, [invoices])

  const attachMutation = useMutation({
    mutationFn: (file) =>
      uploadEventDocument({
        eventId,
        file,
        kind: 'external_invoice',
      }),
    onSuccess: (doc) => {
      queryClient.invalidateQueries({
        queryKey: ['event', eventId, 'documents'],
      })
      queryClient.invalidateQueries({
        queryKey: ['event', eventId, 'document-counts'],
      })
      setSnack({ severity: 'success', message: `Attached ${doc.filename}` })
    },
    onError: (err) => {
      const detail = err?.response?.data?.detail || 'Upload failed.'
      setSnack({ severity: 'error', message: detail })
    },
  })

  const openNew = () => {
    setEditingId(null)
    setEditorOpen(true)
  }
  const openEdit = (id) => {
    setEditingId(id)
    setEditorOpen(true)
  }
  const onAttachClick = () => {
    fileInputRef.current?.click()
  }
  const onFilePicked = (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    attachMutation.mutate(file)
  }

  return (
    <Box>
      {/* Totals header */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={3}
          divider={<Box sx={{ borderLeft: '1px solid', borderColor: 'divider' }} />}
        >
          <SummaryCell label="Total billed" value={totals.billed} />
          <SummaryCell label="Paid" value={totals.paid} valueColor="success.main" />
          <SummaryCell
            label="Outstanding"
            value={totals.outstanding}
            valueColor={totals.outstanding > 0 ? 'warning.main' : 'text.primary'}
          />
        </Stack>
      </Paper>

      {/* Action bar */}
      <Stack direction="row" spacing={1} mb={2} alignItems="center">
        <Button variant="contained" startIcon={<AddIcon />} onClick={openNew}>
          New invoice
        </Button>
        <Button
          variant="text"
          startIcon={<AttachFileIcon />}
          onClick={onAttachClick}
          color="inherit"
          disabled={attachMutation.isPending}
        >
          {attachMutation.isPending ? 'Uploading…' : 'Attach external PDF'}
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf,image/jpeg,image/png,image/heic"
          hidden
          onChange={onFilePicked}
        />
      </Stack>

      {/* List */}
      {invoicesQuery.isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
          <CircularProgress />
        </Box>
      ) : invoicesQuery.error ? (
        <Alert severity="error">
          {invoicesQuery.error?.response?.data?.detail || 'Failed to load invoices.'}
        </Alert>
      ) : invoices.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <ReceiptLongOutlinedIcon sx={{ fontSize: 36, color: 'text.disabled', mb: 1 }} />
          <Typography variant="body2" color="text.secondary">
            No invoices on this event yet. Create one to send a deposit request or final bill.
          </Typography>
        </Paper>
      ) : (
        <Paper sx={{ overflow: 'hidden' }}>
          {invoices.map((inv, i) => (
            <Box
              key={inv.id}
              onClick={() => openEdit(inv.id)}
              sx={{
                p: 2,
                cursor: 'pointer',
                borderBottom: i < invoices.length - 1 ? '1px solid' : 'none',
                borderColor: 'divider',
                display: 'flex',
                alignItems: 'center',
                gap: 2,
                flexWrap: 'wrap',
                '&:hover': { bgcolor: 'rgba(93, 58, 107, 0.04)' },
              }}
            >
              <ReceiptLongOutlinedIcon color="action" />
              <Box sx={{ flex: '1 1 200px', minWidth: 0 }}>
                <Typography variant="body2" sx={{ fontWeight: 500 }}>
                  {inv.invoice_number || 'Draft'}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {inv.sent_at
                    ? `sent ${dayjs(inv.sent_at).format('MMM D, YYYY')}`
                    : `created ${dayjs(inv.created_at).format('MMM D, YYYY')}`}
                </Typography>
              </Box>
              {inv.due_date && (inv.status === 'sent' || inv.status === 'partial') && (
                <Chip
                  size="small"
                  label={`Due ${dayjs(inv.due_date).format('MMM D')}`}
                  variant="outlined"
                />
              )}
              <Box sx={{ minWidth: 110, textAlign: 'right' }}>
                <Typography variant="caption" color="text.secondary" display="block">
                  Total
                </Typography>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {formatUSD(inv.total_cents)}
                </Typography>
              </Box>
              {inv.balance_cents > 0 && inv.status !== 'cancelled' && (
                <Box sx={{ minWidth: 110, textAlign: 'right' }}>
                  <Typography variant="caption" color="text.secondary" display="block">
                    Balance
                  </Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600, color: 'warning.main' }}>
                    {formatUSD(inv.balance_cents)}
                  </Typography>
                </Box>
              )}
              <Chip
                size="small"
                label={STATUS_LABEL[inv.status] || inv.status}
                color={STATUS_COLOR[inv.status] || 'default'}
                sx={{ minWidth: 64 }}
              />
              <Button
                size="small"
                variant="text"
                startIcon={<DownloadIcon />}
                onClick={(e) => {
                  e.stopPropagation()
                  viewInvoicePdf(inv.id).catch(() => {
                    alert('Could not open the invoice PDF. Please try again.')
                  })
                }}
              >
                PDF
              </Button>
            </Box>
          ))}
        </Paper>
      )}

      {/* External attachments (Phase 4b: vendor PDFs and migrated legacy uploads) */}
      {attachments.length > 0 && (
        <Box sx={{ mt: 3 }}>
          <Typography
            variant="overline"
            color="text.secondary"
            sx={{ display: 'block', mb: 1 }}
          >
            External attachments
          </Typography>
          <Paper sx={{ overflow: 'hidden' }}>
            {attachments.map((doc, i) => {
              const linked = invoices.find((inv) => inv.id === doc.linked_invoice_id)
              return (
                <Box
                  key={doc.id}
                  sx={{
                    p: 2,
                    borderBottom:
                      i < attachments.length - 1 ? '1px solid' : 'none',
                    borderColor: 'divider',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 2,
                    flexWrap: 'wrap',
                  }}
                >
                  <AttachFileIcon color="action" fontSize="small" />
                  <Box sx={{ flex: '1 1 200px', minWidth: 0 }}>
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>
                      {doc.label || doc.filename}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      uploaded {dayjs(doc.created_at).format('MMM D, YYYY')}
                      {linked &&
                        ` · linked to ${linked.invoice_number || 'draft'}`}
                    </Typography>
                  </Box>
                  <Button
                    size="small"
                    variant="text"
                    startIcon={<DownloadIcon />}
                    onClick={() => downloadDocument(doc.id, doc.filename)}
                  >
                    Download
                  </Button>
                </Box>
              )
            })}
          </Paper>
        </Box>
      )}

      <InvoiceEditor
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        onCreated={(newId) => setEditingId(newId)}
        eventId={eventId}
        eventDate={event.event_date}
        contactId={contactId}
        contactName={contactName}
        invoiceId={editingId}
      />

      <Snackbar
        open={!!snack}
        autoHideDuration={4000}
        onClose={() => setSnack(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        {snack ? (
          <Alert
            severity={snack.severity}
            onClose={() => setSnack(null)}
            variant="filled"
            sx={{ width: '100%' }}
          >
            {snack.message}
          </Alert>
        ) : undefined}
      </Snackbar>
    </Box>
  )
}

function SummaryCell({ label, value, valueColor }) {
  return (
    <Box sx={{ flex: 1 }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="h6" sx={{ fontWeight: 600, color: valueColor }}>
        {formatUSD(value)}
      </Typography>
    </Box>
  )
}
