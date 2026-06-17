import {
  Box,
  Button,
  Chip,
  Divider,
  Drawer,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import {
  STYLE_LABELS,
  BUDGET_LABELS,
  formatSizeRange,
} from '../utils/boutiqueExperience'
import { formatUSD } from '../utils/money'
import CloseIcon from '@mui/icons-material/Close'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import { useEffect, useState } from 'react'
import { Link as RouterLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { getEvent, getEventWorkflow } from '../services/api'
import { celebrantDiffersFromContact } from '../utils/eventCelebrant'

function formatDate(d) {
  if (!d) return '—'
  return dayjs(d).format('MMM D, YYYY')
}

function KV({ label, value }) {
  return (
    <Stack direction="row" spacing={2} sx={{ py: 0.5 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ minWidth: 110, fontWeight: 500 }}
      >
        {label}
      </Typography>
      <Typography variant="body2" sx={{ flex: 1 }}>
        {value || '—'}
      </Typography>
    </Stack>
  )
}

export default function EventQuickViewDrawer({ card, onClose, onStatusChange }) {
  const [statusDraft, setStatusDraft] = useState('')

  useEffect(() => {
    if (card) setStatusDraft(card.status)
  }, [card])

  const { data: workflow } = useQuery({
    queryKey: ['events', 'workflow', card?.event_type || 'quinceanera'],
    queryFn: () => getEventWorkflow(card?.event_type || 'quinceanera'),
    enabled: !!card,
    staleTime: 5 * 60_000,
  })

  const { data: detail } = useQuery({
    queryKey: ['event', card?.id],
    queryFn: () => getEvent(card.id),
    enabled: !!card,
  })

  const latestAppt = detail?.appointments?.[0]

  function applyStatus(newStatus) {
    setStatusDraft(newStatus)
    if (card && newStatus !== card.status) {
      onStatusChange?.(card.id, newStatus)
    }
  }

  return (
    <Drawer
      anchor="right"
      open={!!card}
      onClose={onClose}
      PaperProps={{ sx: { width: { xs: '100%', md: 440 } } }}
    >
      <Box sx={{ p: 3, height: '100%', display: 'flex', flexDirection: 'column' }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" mb={2}>
          <Typography variant="overline" color="text.secondary">
            Event #{card?.id}
          </Typography>
          <Stack direction="row" alignItems="center" spacing={0.5}>
            {card && (
              <Button
                component={RouterLink}
                to={`/events/${card.id}`}
                size="small"
                startIcon={<OpenInNewIcon />}
              >
                Open full view
              </Button>
            )}
            <IconButton size="small" onClick={onClose}>
              <CloseIcon />
            </IconButton>
          </Stack>
        </Stack>

        {card && (
          <>
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              {card.event_name}
            </Typography>
            {celebrantDiffersFromContact(detail) ? (
              <Typography variant="caption" color="text.secondary">
                Contact: {card.primary_contact?.display_name}
              </Typography>
            ) : (
              <Typography color="text.secondary" variant="body2">
                {card.primary_contact?.display_name}
              </Typography>
            )}

            <Box sx={{ mt: 2.5 }}>
              <TextField
                select
                fullWidth
                size="small"
                label="Status"
                value={statusDraft}
                onChange={(e) => applyStatus(e.target.value)}
              >
                {(workflow?.statuses || []).map((s) => (
                  <MenuItem key={s.code} value={s.code}>
                    {s.label}
                  </MenuItem>
                ))}
              </TextField>
            </Box>

            <Divider sx={{ my: 2.5 }} />

            <Stack spacing={0.5}>
              <KV label="Event date" value={formatDate(card.event_date)} />
              <KV label="Court size" value={card.court_size ?? '—'} />
              <KV label="Theme" value={card.quince_theme} />
              <KV
                label="Status changed"
                value={dayjs(card.status_changed_at).fromNow()}
              />
              <KV label="Owner" value={card.owner?.full_name} />
            </Stack>

            {detail?.participants?.length > 0 && (
              <>
                <Divider sx={{ my: 2.5 }} />
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{ mb: 0.5 }}
                >
                  <Typography
                    variant="overline"
                    color="text.secondary"
                    sx={{ fontWeight: 600 }}
                  >
                    Buyers
                  </Typography>
                  {card.named_buyer_count > 0 && (
                    <Chip
                      size="small"
                      label={card.named_buyer_count}
                      variant="outlined"
                    />
                  )}
                </Stack>
                <Stack spacing={0.75}>
                  {detail.participants.map((p) => {
                    const parts = []
                    if (p.linked_appointment_count > 0) {
                      parts.push(
                        `${p.linked_appointment_count} appt${
                          p.linked_appointment_count === 1 ? '' : 's'
                        }`
                      )
                    }
                    if (p.linked_quote_count > 0) {
                      parts.push(
                        `${p.linked_quote_count} quote${
                          p.linked_quote_count === 1 ? '' : 's'
                        }`
                      )
                    }
                    if (p.linked_invoice_count > 0) {
                      parts.push(
                        `${p.linked_invoice_count} invoice${
                          p.linked_invoice_count === 1 ? '' : 's'
                        }`
                      )
                    }
                    const summary =
                      parts.length > 0 ? parts.join(' · ') : 'no tagged rows yet'
                    return (
                      <Box key={p.id}>
                        <Stack
                          direction="row"
                          alignItems="baseline"
                          spacing={1}
                          sx={{ flexWrap: 'wrap' }}
                        >
                          <Typography
                            variant="body2"
                            sx={{ fontWeight: 500 }}
                          >
                            {p.role} · {p.display_name}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                          >
                            {summary}
                          </Typography>
                        </Stack>
                        {p.outstanding_balance_cents > 0 && (
                          <Typography
                            variant="caption"
                            color="warning.main"
                            sx={{ display: 'block' }}
                          >
                            {formatUSD(p.outstanding_balance_cents)} outstanding
                          </Typography>
                        )}
                      </Box>
                    )
                  })}
                </Stack>
              </>
            )}

            {latestAppt && (
              <>
                <Divider sx={{ my: 2.5 }} />
                <Typography
                  variant="overline"
                  color="text.secondary"
                  sx={{ fontWeight: 600 }}
                >
                  Latest booking
                </Typography>
                <Stack spacing={0.5} mt={0.5}>
                  <KV
                    label="Appointment"
                    value={`${dayjs(latestAppt.slot_start_at).format('MMM D, YYYY h:mm A')} · ${latestAppt.status}`}
                  />
                  <KV
                    label="Phone"
                    value={latestAppt.phone_e164 || latestAppt.phone}
                  />
                  <KV label="Email" value={latestAppt.email} />
                  {latestAppt.customer_note && (
                    <KV label="Note" value={latestAppt.customer_note} />
                  )}
                  <KV
                    label="Source"
                    value={latestAppt.utm_source || 'direct'}
                  />
                  <KV
                    label="Code"
                    value={latestAppt.confirmation_code}
                  />
                </Stack>

                <Divider sx={{ my: 2.5 }} />
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{ mb: 0.5 }}
                >
                  <Typography
                    variant="overline"
                    color="text.secondary"
                    sx={{ fontWeight: 600 }}
                  >
                    Boutique Experience
                  </Typography>
                  <Chip
                    size="small"
                    label={
                      latestAppt.boutique_experience_status === 'complete'
                        ? 'Complete'
                        : 'Not started'
                    }
                    color={
                      latestAppt.boutique_experience_status === 'complete'
                        ? 'success'
                        : 'default'
                    }
                    variant={
                      latestAppt.boutique_experience_status === 'complete'
                        ? 'filled'
                        : 'outlined'
                    }
                  />
                </Stack>
                {latestAppt.boutique_experience_status === 'complete' &&
                latestAppt.boutique_experience ? (
                  <Stack spacing={0.5}>
                    <KV
                      label="Size estimate"
                      value={formatSizeRange(latestAppt.boutique_experience)}
                    />
                    <KV
                      label="Style"
                      value={
                        STYLE_LABELS[latestAppt.boutique_experience.style] ||
                        latestAppt.boutique_experience.style
                      }
                    />
                    <KV
                      label="Budget"
                      value={
                        BUDGET_LABELS[latestAppt.boutique_experience.budget] ||
                        latestAppt.boutique_experience.budget
                      }
                    />
                  </Stack>
                ) : (
                  <Typography variant="body2" color="text.secondary">
                    Customer hasn't filled this out yet.
                  </Typography>
                )}
              </>
            )}

          </>
        )}
      </Box>
    </Drawer>
  )
}

