import {
  Alert,
  Avatar,
  Box,
  Button,
  Chip,
  Divider,
  IconButton,
  Paper,
  Snackbar,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import ArchiveOutlinedIcon from '@mui/icons-material/ArchiveOutlined'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import PersonAddAltOutlinedIcon from '@mui/icons-material/PersonAddAltOutlined'
import EventNoteIcon from '@mui/icons-material/EventNote'
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import ReceiptLongOutlinedIcon from '@mui/icons-material/ReceiptLongOutlined'
import { useMemo, useState } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

import AddParticipantDialog from '../../../components/AddParticipantDialog'
import AdminEventOwnerDialog from '../../../components/AdminEventOwnerDialog'
import ContactEditDialog from '../../../components/ContactEditDialog'
import ParticipantTagDialog from '../../../components/ParticipantTagDialog'
import RecordDependenciesDialog from '../../../components/RecordDependenciesDialog'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  adminTagAppointmentParticipant,
  archiveEventParticipant,
} from '../../../services/api'
import {
  STYLE_LABELS,
  BACK_LABELS,
  BUDGET_LABELS,
  formatSizeRange,
} from '../../../utils/boutiqueExperience'
import {
  celebrantDiffersFromContact,
  getCelebrantName,
} from '../../../utils/eventCelebrant'
import { formatUSD } from '../../../utils/money'

dayjs.extend(relativeTime)

function formatDateTime(d) {
  if (!d) return '—'
  return dayjs(d).format('MMM D, YYYY h:mm A')
}

function Section({ title, children }) {
  return (
    <Paper sx={{ p: 2.5, mb: 2 }}>
      <Typography variant="overline" color="text.secondary" sx={{ fontWeight: 600 }}>
        {title}
      </Typography>
      <Box mt={1}>{children}</Box>
    </Paper>
  )
}

function KV({ label, value }) {
  return (
    <Stack direction="row" spacing={2} sx={{ py: 0.5 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ minWidth: 130, fontWeight: 500 }}
      >
        {label}
      </Typography>
      <Typography variant="body2" sx={{ flex: 1 }}>
        {value || '—'}
      </Typography>
    </Stack>
  )
}

const PARTY_LABEL = {
  solo: 'Just me',
  '2_3': '2-3',
  '4_plus': '4+',
  pair: 'Parent + celebrant',
  '3_4': '3-4',
  '5_plus': '5+',
}

function BoutiqueExperienceBlock({ status, profile, summary, submittedAt }) {
  const isComplete = status === 'complete'
  return (
    <Box mt={2}>
      <Stack direction="row" alignItems="center" spacing={1} mb={0.5}>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontWeight: 600 }}
        >
          Boutique Experience
        </Typography>
        <Chip
          size="small"
          label={isComplete ? 'Complete' : 'Not started'}
          color={isComplete ? 'success' : 'default'}
          variant={isComplete ? 'filled' : 'outlined'}
        />
      </Stack>

      {!isComplete && (
        <Typography variant="body2" color="text.secondary">
          Customer hasn't filled out the Boutique Experience profile yet.
        </Typography>
      )}

      {isComplete && profile && (
        <Box>
          {submittedAt && (
            <KV label="Submitted" value={formatDateTime(submittedAt)} />
          )}
          {profile.source && (
            <KV
              label="Source"
              value={profile.source.replace(/_/g, ' ')}
            />
          )}

          <Box mt={1.5}>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontWeight: 600, display: 'block', mb: 0.5 }}
            >
              Sizing
            </Typography>
            <KV label="Estimated range" value={formatSizeRange(profile)} />
            {(profile.size_by_bust != null ||
              profile.size_by_waist != null ||
              profile.size_by_hips != null) && (
              <KV
                label="By measurement"
                value={
                  `bust ${profile.size_by_bust ?? '—'}` +
                  ` · waist ${profile.size_by_waist ?? '—'}` +
                  ` · hips ${profile.size_by_hips ?? '—'}`
                }
              />
            )}
            <KV
              label="Measurements"
              value={
                profile.bust_inches != null ||
                profile.waist_inches != null ||
                profile.hips_inches != null
                  ? `bust ${profile.bust_inches ?? '—'}"` +
                    ` · waist ${profile.waist_inches ?? '—'}"` +
                    ` · hips ${profile.hips_inches ?? '—'}"`
                  : null
              }
            />
            {(profile.height_ft != null || profile.height_in != null) && (
              <KV
                label="Height"
                value={`${profile.height_ft ?? 0}'${profile.height_in ?? 0}"`}
              />
            )}
            {profile.chart_source && (
              <KV label="Chart" value={profile.chart_source} />
            )}
            {profile.off_chart && (
              <Typography
                variant="caption"
                color="warning.main"
                sx={{ display: 'block', mt: 0.5 }}
              >
                Customer is at the upper end of the reference chart. Confirm
                with extended-size designers in store.
              </Typography>
            )}
          </Box>

          <Box mt={1.5}>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontWeight: 600, display: 'block', mb: 0.5 }}
            >
              Style preferences
            </Typography>
            <KV label="Style" value={STYLE_LABELS[profile.style] || profile.style} />
            <KV label="Back" value={BACK_LABELS[profile.back] || profile.back} />
            <KV
              label="Budget"
              value={BUDGET_LABELS[profile.budget] || profile.budget}
            />
            {profile.colors && <KV label="Colors" value={profile.colors} />}
            {profile.likes && <KV label="Likes" value={profile.likes} />}
            {profile.avoids && <KV label="Avoid" value={profile.avoids} />}
          </Box>

          {summary && (
            <Box mt={1.5}>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontWeight: 600, display: 'block', mb: 0.5 }}
              >
                Customer summary
              </Typography>
              <Typography
                variant="body2"
                sx={{ whiteSpace: 'pre-line', color: 'text.primary' }}
              >
                {summary}
              </Typography>
            </Box>
          )}
        </Box>
      )}
    </Box>
  )
}

function BookingDetail({ appointment: a, eventId, participants }) {
  const duration =
    a.slot_duration_minutes ??
    Math.round((new Date(a.slot_end_at) - new Date(a.slot_start_at)) / 60000)
  const enrichment = a.enrichment
  const queryClient = useQueryClient()
  const [tagOpen, setTagOpen] = useState(false)
  const taggedParticipant = (participants || []).find(
    (p) => p.id === (a.event_participant_id ?? null),
  )
  const buyerLabel = a.event_participant_id
    ? taggedParticipant
      ? `buyer: ${taggedParticipant.role} ${taggedParticipant.display_name}`
      : 'buyer: (unknown)'
    : 'buyer: untagged'
  // Phase 10.6: anchor id so the buyer-journey section can scroll
  // directly to the matching booking row when an appointment item
  // is clicked.
  return (
    <Box id={`booking-${a.id}`}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="flex-start"
        mb={1}
      >
        <Box>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {a.confirmation_code}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {formatDateTime(a.slot_start_at)} · {duration} min · booked{' '}
            {dayjs(a.created_at).fromNow()}
          </Typography>
        </Box>
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
          {a.rescheduled_from_id && (
            <Chip size="small" label="rescheduled" variant="outlined" />
          )}
          <Chip size="small" label={a.status} />
          <Chip
            size="small"
            variant="outlined"
            label={buyerLabel}
            onClick={() => setTagOpen(true)}
          />
        </Stack>
      </Stack>

      <ParticipantTagDialog
        open={tagOpen}
        onClose={() => setTagOpen(false)}
        appointmentId={a.id}
        participants={participants || []}
        currentEventParticipantId={a.event_participant_id ?? null}
        tagFn={adminTagAppointmentParticipant}
        onSuccess={() => {
          queryClient.invalidateQueries({ queryKey: ['event', eventId] })
          queryClient.invalidateQueries({ queryKey: ['events', 'board'] })
        }}
      />

      <KV label="Party size" value={PARTY_LABEL[a.party_size_bucket] || a.party_size_bucket} />
      <KV label="Phone" value={a.phone_e164 || a.phone} />
      <KV label="Email" value={a.email} />
      {a.customer_note && <KV label="Customer note" value={a.customer_note} />}
      {a.cancelled_at && (
        <KV
          label="Cancelled"
          value={`${formatDateTime(a.cancelled_at)}${a.cancellation_reason ? ` — ${a.cancellation_reason}` : ''}`}
        />
      )}

      <Box mt={1.5}>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontWeight: 600, display: 'block', mb: 0.5 }}
        >
          Source
        </Typography>
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
          <Chip
            size="small"
            label={a.utm_source || 'direct'}
            variant="outlined"
          />
          {a.utm_medium && (
            <Chip size="small" label={a.utm_medium} variant="outlined" />
          )}
          {a.utm_campaign && (
            <Chip size="small" label={a.utm_campaign} variant="outlined" />
          )}
          {a.has_fbclid && (
            <Chip size="small" label="fbclid" variant="outlined" color="primary" />
          )}
          {a.has_gclid && (
            <Chip size="small" label="gclid" variant="outlined" color="primary" />
          )}
        </Stack>
        {a.page_url && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
            <a href={a.page_url} target="_blank" rel="noreferrer">
              landing page <OpenInNewIcon fontSize="inherit" sx={{ verticalAlign: 'middle' }} />
            </a>
          </Typography>
        )}
      </Box>

      <BoutiqueExperienceBlock
        status={a.boutique_experience_status}
        profile={a.boutique_experience}
        summary={a.boutique_experience_summary}
        submittedAt={a.boutique_experience_submitted_at}
      />

      {enrichment && (
        <Box mt={2}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ fontWeight: 600, display: 'block', mb: 0.5 }}
          >
            Enrichment survey
          </Typography>
          {enrichment.submitted_at && (
            <KV label="Submitted" value={formatDateTime(enrichment.submitted_at)} />
          )}
          {enrichment.budget_range && (
            <KV label="Budget" value={enrichment.budget_range} />
          )}
          {enrichment.quince_theme && (
            <KV label="Theme" value={enrichment.quince_theme} />
          )}
          {enrichment.court_size != null && (
            <KV label="Court size" value={enrichment.court_size} />
          )}
          {enrichment.dress_styles?.length > 0 && (
            <KV label="Style picks" value={enrichment.dress_styles.join(', ')} />
          )}
          {enrichment.colors?.length > 0 && (
            <KV label="Color picks" value={enrichment.colors.join(', ')} />
          )}
          {enrichment.free_text && (
            <KV label="Customer text" value={enrichment.free_text} />
          )}
        </Box>
      )}
    </Box>
  )
}

// Phase 10.6: status maps for the buyer-journey rows. Kept local to the
// Overview tab so the journey display can stay loosely coupled from the
// Quotes/Invoices tab modules — a future refactor can extract these if
// another surface needs the same labels.
const QUOTE_STATUS_LABEL = {
  draft: 'Draft',
  sent: 'Sent',
  approved: 'Approved',
  rejected: 'Rejected',
  converted: 'Converted',
  expired: 'Expired',
  cancelled: 'Cancelled',
}
const QUOTE_STATUS_COLOR = {
  draft: 'default',
  sent: 'primary',
  approved: 'success',
  rejected: 'default',
  converted: 'info',
  expired: 'warning',
  cancelled: 'default',
}
const INVOICE_STATUS_LABEL = {
  draft: 'Draft',
  sent: 'Sent',
  partial: 'Partial',
  paid: 'Paid',
  cancelled: 'Cancelled',
  reversed: 'Reversed',
}
const INVOICE_STATUS_COLOR = {
  draft: 'default',
  sent: 'primary',
  partial: 'warning',
  paid: 'success',
  cancelled: 'default',
  reversed: 'default',
}

function bucketJourneyItems(event) {
  const buckets = new Map()
  const ensure = (key) => {
    if (!buckets.has(key)) buckets.set(key, [])
    return buckets.get(key)
  }
  for (const a of event.appointments || []) {
    const key = a.event_participant_id ?? 'untagged'
    ensure(key).push({
      kind: 'appointment',
      id: a.id,
      sortAt: a.slot_start_at,
      data: a,
    })
  }
  for (const q of event.quotes || []) {
    const key = q.event_participant_id ?? 'untagged'
    ensure(key).push({
      kind: 'quote',
      id: q.id,
      sortAt: q.sent_at || q.issue_date,
      data: q,
    })
  }
  for (const i of event.invoices || []) {
    const key = i.event_participant_id ?? 'untagged'
    ensure(key).push({
      kind: 'invoice',
      id: i.id,
      sortAt: i.sent_at || i.issue_date,
      data: i,
    })
  }
  for (const items of buckets.values()) {
    items.sort((a, b) => new Date(a.sortAt) - new Date(b.sortAt))
  }
  return buckets
}

function countLabel(items) {
  const a = items.filter((it) => it.kind === 'appointment').length
  const q = items.filter((it) => it.kind === 'quote').length
  const i = items.filter((it) => it.kind === 'invoice').length
  const parts = []
  if (a) parts.push(`${a} appt${a === 1 ? '' : 's'}`)
  if (q) parts.push(`${q} quote${q === 1 ? '' : 's'}`)
  if (i) parts.push(`${i} invoice${i === 1 ? '' : 's'}`)
  return parts.join(' · ')
}

const journeyRowSx = {
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'baseline',
  gap: 1,
  py: 0.5,
  px: 0.5,
  borderRadius: 0.5,
  '&:hover': { bgcolor: 'rgba(93, 58, 107, 0.04)' },
}

function JourneyItem({ item, eventId, navigate }) {
  const date = dayjs(item.sortAt).format('MMM D, YYYY')
  if (item.kind === 'appointment') {
    const a = item.data
    return (
      <Box
        onClick={() => {
          const el = document.getElementById(`booking-${a.id}`)
          if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }}
        sx={journeyRowSx}
      >
        <EventNoteIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
        <Typography variant="caption" color="text.secondary" sx={{ minWidth: 100 }}>
          {date}
        </Typography>
        <Typography variant="body2" sx={{ flex: 1 }}>
          appt #{a.confirmation_code}
        </Typography>
        <Chip size="small" label={a.status} variant="outlined" />
      </Box>
    )
  }
  if (item.kind === 'quote') {
    const q = item.data
    return (
      <Box
        onClick={() => navigate(`/events/${eventId}/quotes?edit=${q.id}`)}
        sx={journeyRowSx}
      >
        <DescriptionOutlinedIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
        <Typography variant="caption" color="text.secondary" sx={{ minWidth: 100 }}>
          {date}
        </Typography>
        <Typography variant="body2" sx={{ flex: 1 }}>
          quote {q.quote_number || '(draft)'} · {formatUSD(q.total_cents)}
        </Typography>
        <Chip
          size="small"
          label={QUOTE_STATUS_LABEL[q.status] || q.status}
          color={QUOTE_STATUS_COLOR[q.status] || 'default'}
          variant={q.status === 'draft' ? 'outlined' : 'filled'}
        />
      </Box>
    )
  }
  const i = item.data
  const showBalance = i.status === 'sent' || i.status === 'partial'
  return (
    <Box
      onClick={() => navigate(`/events/${eventId}/invoices?edit=${i.id}`)}
      sx={journeyRowSx}
    >
      <ReceiptLongOutlinedIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
      <Typography variant="caption" color="text.secondary" sx={{ minWidth: 100 }}>
        {date}
      </Typography>
      <Typography variant="body2" sx={{ flex: 1 }}>
        invoice {i.invoice_number || '(draft)'} · {formatUSD(i.total_cents)}
        {showBalance && (
          <Typography
            component="span"
            variant="caption"
            color="warning.main"
            sx={{ ml: 1 }}
          >
            {formatUSD(i.balance_cents)} balance
          </Typography>
        )}
      </Typography>
      <Chip
        size="small"
        label={INVOICE_STATUS_LABEL[i.status] || i.status}
        color={INVOICE_STATUS_COLOR[i.status] || 'default'}
        variant={i.status === 'draft' ? 'outlined' : 'filled'}
      />
    </Box>
  )
}

function JourneyCard({ header, items, eventId, navigate }) {
  if (items.length === 0) return null
  return (
    <Box
      sx={{
        p: 1.5,
        border: '1px solid',
        borderColor: 'divider',
        borderRadius: 1,
      }}
    >
      {header}
      <Divider sx={{ my: 1 }} />
      <Stack spacing={0.25}>
        {items.map((it) => (
          <JourneyItem
            key={`${it.kind}-${it.id}`}
            item={it}
            eventId={eventId}
            navigate={navigate}
          />
        ))}
      </Stack>
    </Box>
  )
}

function BuyerJourneys({ event }) {
  const navigate = useNavigate()
  const buckets = useMemo(() => bucketJourneyItems(event), [event])
  const participants = event.participants || []
  const untagged = buckets.get('untagged') || []
  const hasAnyItem =
    participants.some((p) => (buckets.get(p.id) || []).length > 0) ||
    untagged.length > 0
  if (!hasAnyItem) return null
  return (
    <Section title="Buyer journeys">
      <Stack spacing={1.5}>
        {participants.map((p) => {
          const items = buckets.get(p.id) || []
          if (items.length === 0) return null
          const header = (
            <Stack
              direction="row"
              alignItems="baseline"
              spacing={1}
              sx={{ flexWrap: 'wrap' }}
            >
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {p.role.replace(/_/g, ' ')} · {p.display_name}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {countLabel(items)}
              </Typography>
              {p.outstanding_balance_cents > 0 && (
                <Typography variant="caption" color="warning.main">
                  · {formatUSD(p.outstanding_balance_cents)} outstanding
                </Typography>
              )}
            </Stack>
          )
          return (
            <JourneyCard
              key={p.id}
              header={header}
              items={items}
              eventId={event.id}
              navigate={navigate}
            />
          )
        })}
        {untagged.length > 0 && (
          <JourneyCard
            header={
              <Stack
                direction="row"
                alignItems="baseline"
                spacing={1}
                sx={{ flexWrap: 'wrap' }}
              >
                <Typography
                  variant="body2"
                  sx={{ fontWeight: 600, color: 'text.secondary' }}
                >
                  Untagged (celebrant or legacy)
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {countLabel(untagged)}
                </Typography>
              </Stack>
            }
            items={untagged}
            eventId={event.id}
            navigate={navigate}
          />
        )}
      </Stack>
    </Section>
  )
}

function describeParticipantArchiveError(err) {
  const detail = err?.response?.data?.detail
  const code = detail?.code
  if (code === 'archive_blocked') {
    return 'This participant cannot be archived — they back an active financial record or are the sole quinceañera.'
  }
  if (code === 'participant_not_found') {
    return 'This participant no longer exists. Reload and try again.'
  }
  if (code === 'invalid_reason') {
    return 'Pick an archive reason and try again.'
  }
  return detail?.message || err?.message || 'Could not archive this participant.'
}

export default function Overview() {
  const { event } = useOutletContext()
  const [editContactOpen, setEditContactOpen] = useState(false)
  const [addParticipantOpen, setAddParticipantOpen] = useState(false)
  const [ownerDialogOpen, setOwnerDialogOpen] = useState(false)
  const [archiveParticipantId, setArchiveParticipantId] = useState(null)
  const [toast, setToast] = useState(null)
  const queryClient = useQueryClient()

  const archiveParticipantMutation = useMutation({
    mutationFn: ({ participantId, reason, note }) =>
      archiveEventParticipant(event.id, participantId, { reason, note }),
    onSuccess: () => {
      setArchiveParticipantId(null)
      setToast({
        severity: 'success',
        message: 'Participant moved to the Recycle Bin.',
      })
      queryClient.invalidateQueries({ queryKey: ['event', event.id] })
      queryClient.invalidateQueries({ queryKey: ['record-dependencies'] })
    },
  })

  const archivingParticipant = (event.participants || []).find(
    (p) => p.id === archiveParticipantId,
  )

  return (
    <Box>
      <Section title="Event">
        <KV label="Event date" value={event.event_date ? formatDateTime(event.event_date) : '—'} />
        <KV label="Court size" value={event.court_size ?? '—'} />
        <KV label="Theme" value={event.quince_theme} />
        <KV
          label="Theme colors"
          value={(event.quince_theme_colors || []).join(', ') || '—'}
        />
        <KV label="Budget range" value={event.budget_range} />
        <Stack direction="row" spacing={2} sx={{ py: 0.5, alignItems: 'center' }}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ minWidth: 130, fontWeight: 500 }}
          >
            Owner
          </Typography>
          <Typography variant="body2" sx={{ flex: 1 }}>
            {event.owner?.full_name || '—'}
          </Typography>
          <Button
            size="small"
            startIcon={<EditOutlinedIcon fontSize="inherit" />}
            onClick={() => setOwnerDialogOpen(true)}
          >
            Change
          </Button>
        </Stack>
        <KV label="Notes" value={event.notes} />
      </Section>

      <Section title="Primary contact">
        <Stack
          direction="row"
          justifyContent="flex-end"
          sx={{ mt: -1, mb: 0.5 }}
        >
          <Button
            size="small"
            startIcon={<EditOutlinedIcon fontSize="inherit" />}
            onClick={() => setEditContactOpen(true)}
            disabled={!event.primary_contact?.id}
          >
            Edit
          </Button>
        </Stack>
        <KV label="Name" value={event.primary_contact?.display_name} />
        <KV label="Phone" value={event.primary_contact_phone} />
        <KV label="Email" value={event.primary_contact_email} />
        {celebrantDiffersFromContact(event) && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: 'block', mt: 1 }}
          >
            Celebrant on this event: {getCelebrantName(event)}
          </Typography>
        )}
      </Section>

      <ContactEditDialog
        open={editContactOpen}
        contactId={event.primary_contact?.id}
        onClose={() => setEditContactOpen(false)}
      />

      <AdminEventOwnerDialog
        open={ownerDialogOpen}
        onClose={() => setOwnerDialogOpen(false)}
        eventId={event.id}
        currentOwnerUserId={event.owner?.id ?? null}
        currentOwnerName={event.owner?.full_name}
        onSuccess={() => {
          queryClient.invalidateQueries({ queryKey: ['event', event.id] })
          queryClient.invalidateQueries({ queryKey: ['events', 'board'] })
        }}
      />

      <Section title="Participants">
        <Stack direction="row" justifyContent="flex-end" sx={{ mt: -1, mb: 0.5 }}>
          <Button
            size="small"
            startIcon={<PersonAddAltOutlinedIcon fontSize="inherit" />}
            onClick={() => setAddParticipantOpen(true)}
          >
            Add
          </Button>
        </Stack>
        {(event.participants || []).length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No participants yet.
          </Typography>
        ) : (
          <Stack spacing={1}>
            {event.participants.map((p) => (
              <Stack key={p.id} direction="row" alignItems="center" spacing={2}>
                <Avatar sx={{ width: 32, height: 32, fontSize: 12 }}>
                  {p.display_name[0]?.toUpperCase()}
                </Avatar>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                    {p.display_name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {p.role.replace(/_/g, ' ')}
                  </Typography>
                </Box>
                <Tooltip title="Archive participant">
                  <IconButton
                    size="small"
                    aria-label={`Archive ${p.display_name}`}
                    onClick={() => setArchiveParticipantId(p.id)}
                  >
                    <ArchiveOutlinedIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Stack>
            ))}
          </Stack>
        )}
      </Section>

      <AddParticipantDialog
        eventId={event.id}
        open={addParticipantOpen}
        onClose={() => setAddParticipantOpen(false)}
      />

      <BuyerJourneys event={event} />

      <Section title="Booking">
        {(event.appointments || []).length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No linked appointments.
          </Typography>
        ) : (
          <Stack spacing={2.5} divider={<Divider flexItem />}>
            {event.appointments.map((a) => (
              <BookingDetail
                key={a.id}
                appointment={a}
                eventId={event.id}
                participants={event.participants || []}
              />
            ))}
          </Stack>
        )}
      </Section>

      <Section title="Status history">
        {(event.status_history || []).length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No status changes recorded.
          </Typography>
        ) : (
          <Stack spacing={1.5}>
            {event.status_history.map((h, i) => (
              <Box key={i}>
                <Typography variant="body2">
                  <strong>{(h.from_status || 'created').replace(/_/g, ' ')}</strong>
                  {' → '}
                  <strong>{h.to_status.replace(/_/g, ' ')}</strong>
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {dayjs(h.changed_at).fromNow()} · {formatDateTime(h.changed_at)}
                </Typography>
                {h.notes && (
                  <Typography variant="body2" sx={{ mt: 0.5, color: 'text.secondary' }}>
                    {h.notes}
                  </Typography>
                )}
              </Box>
            ))}
          </Stack>
        )}
      </Section>

      <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 4 }}>
        Dress orders, alterations, and payments will appear here in a later release.
      </Typography>

      <RecordDependenciesDialog
        entityType="event_participant"
        entityId={archiveParticipantId}
        open={archiveParticipantId !== null}
        onClose={() => {
          if (!archiveParticipantMutation.isPending) {
            setArchiveParticipantId(null)
            archiveParticipantMutation.reset()
          }
        }}
        title={
          archivingParticipant
            ? `Archive ${archivingParticipant.display_name}?`
            : 'Archive participant?'
        }
        confirmLabel="Move to Recycle Bin"
        confirmMode="archive"
        isSubmitting={archiveParticipantMutation.isPending}
        submitError={
          archiveParticipantMutation.isError
            ? describeParticipantArchiveError(archiveParticipantMutation.error)
            : null
        }
        onConfirm={({ reason, note }) =>
          archiveParticipantMutation.mutate({
            participantId: archiveParticipantId,
            reason,
            note,
          })
        }
      />

      <Snackbar
        open={Boolean(toast)}
        autoHideDuration={4000}
        onClose={() => setToast(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        {toast ? (
          <Alert
            severity={toast.severity}
            onClose={() => setToast(null)}
            variant="filled"
          >
            {toast.message}
          </Alert>
        ) : undefined}
      </Snackbar>
    </Box>
  )
}
