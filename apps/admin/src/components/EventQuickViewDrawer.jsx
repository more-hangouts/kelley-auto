import {
  Box,
  Button,
  Chip,
  Divider,
  Drawer,
  IconButton,
  Link,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { formatUSD } from '../utils/money'
import CloseIcon from '@mui/icons-material/Close'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import { useEffect, useState } from 'react'
import { Link as RouterLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { getEvent, getEventWorkflow } from '../services/api'

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
    queryKey: ['events', 'workflow', card?.event_type || 'vehicle_sale'],
    queryFn: () => getEventWorkflow(card?.event_type || 'vehicle_sale'),
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
            {card?.event_type === 'quinceanera' ? 'Event' : 'Deal'} #{card?.id}
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
            <Typography color="text.secondary" variant="body2">
              {card.primary_contact?.display_name}
            </Typography>

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
              {card.vehicle ? (
                <>
                  <KV
                    label="Vehicle"
                    value={
                      [
                        card.vehicle.year,
                        card.vehicle.make,
                        card.vehicle.model,
                        card.vehicle.trim,
                      ]
                        .filter(Boolean)
                        .join(' ') || '—'
                    }
                  />
                  <KV
                    label="Price"
                    value={
                      card.vehicle.price_cents != null
                        ? formatUSD(card.vehicle.price_cents)
                        : '—'
                    }
                  />
                  <KV label="Stock #" value={card.vehicle.stock_number} />
                  <KV label="VIN" value={card.vehicle.vin} />
                  <KV
                    label="Inventory"
                    value={
                      card.vehicle.vehicle_status ? (
                        <Chip
                          size="small"
                          label={card.vehicle.vehicle_status}
                          sx={{ height: 20, textTransform: 'capitalize' }}
                        />
                      ) : (
                        '—'
                      )
                    }
                  />
                  <KV
                    label="Mileage"
                    value={
                      card.vehicle.mileage != null
                        ? `${card.vehicle.mileage.toLocaleString()} mi`
                        : '—'
                    }
                  />
                  <KV label="Color" value={card.vehicle.exterior_color} />
                  <KV
                    label="Body"
                    value={
                      [card.vehicle.body_type, card.vehicle.drivetrain]
                        .filter(Boolean)
                        .join(' · ') || '—'
                    }
                  />
                  <KV
                    label="Powertrain"
                    value={
                      [card.vehicle.transmission, card.vehicle.fuel_type]
                        .filter(Boolean)
                        .join(' · ') || '—'
                    }
                  />
                  <KV
                    label="Condition"
                    value={
                      card.vehicle.condition ? (
                        <Typography
                          variant="body2"
                          sx={{ textTransform: 'capitalize' }}
                        >
                          {card.vehicle.condition}
                        </Typography>
                      ) : (
                        '—'
                      )
                    }
                  />
                  {card.vehicle.carfax_url && (
                    <KV
                      label="Carfax"
                      value={
                        <Link
                          href={card.vehicle.carfax_url}
                          target="_blank"
                          rel="noreferrer"
                          underline="hover"
                        >
                          View report
                        </Link>
                      }
                    />
                  )}
                </>
              ) : card.event_type === 'quinceanera' ? (
                /* legacy Bella's-era rows */
                <KV label="Event date" value={formatDate(card.event_date)} />
              ) : (
                <KV
                  label="Vehicle"
                  value={
                    <Typography variant="body2" color="text.secondary">
                      None linked yet — attach one from the full view
                    </Typography>
                  }
                />
              )}
              <KV
                label="Phone"
                value={
                  detail?.primary_contact_phone ? (
                    <Link
                      href={`tel:${detail.primary_contact_phone}`}
                      underline="hover"
                    >
                      {detail.primary_contact_phone}
                    </Link>
                  ) : (
                    '—'
                  )
                }
              />
              <KV
                label="Email"
                value={
                  detail?.primary_contact_email ? (
                    <Link
                      href={`mailto:${detail.primary_contact_email}`}
                      underline="hover"
                      sx={{ wordBreak: 'break-all' }}
                    >
                      {detail.primary_contact_email}
                    </Link>
                  ) : (
                    '—'
                  )
                }
              />
              <KV label="Budget" value={detail?.budget_range} />
              {card.outstanding_balance_cents > 0 && (
                <KV
                  label="Balance due"
                  value={
                    <Typography variant="body2" color="warning.main">
                      {formatUSD(card.outstanding_balance_cents)}
                    </Typography>
                  }
                />
              )}
              <KV
                label="Status changed"
                value={dayjs(card.status_changed_at).fromNow()}
              />
              <KV label="Owner" value={card.owner?.full_name} />
            </Stack>

            {detail?.notes && (
              <>
                <Divider sx={{ my: 2.5 }} />
                <Typography
                  variant="overline"
                  color="text.secondary"
                  sx={{ fontWeight: 600 }}
                >
                  Notes
                </Typography>
                <Typography
                  variant="body2"
                  sx={{ whiteSpace: 'pre-wrap', mt: 0.5, maxHeight: 160, overflowY: 'auto' }}
                >
                  {detail.notes}
                </Typography>
              </>
            )}

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

              </>
            )}

          </>
        )}
      </Box>
    </Drawer>
  )
}

