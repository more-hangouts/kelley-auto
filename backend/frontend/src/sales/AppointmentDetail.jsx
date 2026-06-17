import { useEffect, useState } from 'react'
import { Link as RouterLink, useParams } from 'react-router-dom'
import {
  Alert,
  Box,
  Breadcrumbs,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  Link,
  Stack,
  TextField,
  Typography,
} from '@mui/material'

import AddParticipantDialog from '../components/AddParticipantDialog'
import ParticipantTagDialog from '../components/ParticipantTagDialog'
import {
  salesGetAppointmentDetail,
  salesPatchAppointmentNotes,
  salesPostAppointmentStatus,
  salesTagAppointmentParticipant,
} from '../services/api'
import { isAttendanceGateError, attendanceGateMessage } from './attendanceGate'
import QuotesSection from './QuotesSection'
import SalesAssignmentDialog from './SalesAssignmentDialog'
import TriedOnSection from './TriedOnSection'

const ACTION_LABEL = {
  arrived: 'Arrived',
  no_show: 'No-show',
  cancelled: 'Cancelled',
}

const ACTION_COLOR = {
  arrived: 'success',
  no_show: 'warning',
  cancelled: 'inherit',
}

const ACTION_VARIANT = {
  arrived: 'contained',
  no_show: 'outlined',
  cancelled: 'outlined',
}

const TERMINAL_STATUSES = new Set(['attended', 'no_show', 'cancelled'])

const STATUS_COLORS = {
  confirmed: 'primary',
  pending: 'default',
  attended: 'success',
  no_show: 'warning',
  cancelled: 'default',
}

function formatTime(iso, tz) {
  if (!iso) return ''
  try {
    return new Intl.DateTimeFormat(undefined, {
      hour: 'numeric',
      minute: '2-digit',
      timeZone: tz,
    }).format(new Date(iso))
  } catch {
    return new Date(iso).toLocaleTimeString()
  }
}

function formatDateTime(iso, tz) {
  if (!iso) return ''
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: tz,
    }).format(new Date(iso))
  } catch {
    return new Date(iso).toLocaleString()
  }
}

function formatRelative(iso) {
  if (!iso) return ''
  const ts = new Date(iso).getTime()
  const diffMs = Date.now() - ts
  const minutes = Math.round(diffMs / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

function fullName(first, last) {
  return [first, last].filter(Boolean).join(' ').trim()
}

function Section({ title, children }) {
  return (
    <Card variant="outlined">
      <CardContent>
        <Typography variant="overline" color="text.secondary">
          {title}
        </Typography>
        <Box sx={{ mt: 0.5 }}>{children}</Box>
      </CardContent>
    </Card>
  )
}

function Field({ label, value }) {
  if (value === null || value === undefined || value === '') return null
  return (
    <Stack direction="row" spacing={1} sx={{ mb: 0.5 }}>
      <Typography variant="body2" color="text.secondary" sx={{ minWidth: 110 }}>
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Stack>
  )
}

function describeActivity(row) {
  const verb = row.activity_type.replace(/[._]/g, ' ')
  const who =
    row.actor_display_name ||
    (row.actor_kind === 'system' ? 'System' : 'Unknown actor')
  return `${who}: ${verb}`
}

function describePreview(appt, event, action) {
  const time = formatTime(appt.slot_start_at, appt.timezone)
  const who =
    fullName(appt.celebrant_first_name, appt.celebrant_last_name) ||
    'this appointment'
  if (action === 'arrived') {
    if (!event) {
      return `Mark ${who}'s ${time} appointment as Arrived. We'll create a CRM event in Lead and immediately move it to Consulted.`
    }
    if (event.status === 'lead') {
      return `Mark ${who}'s ${time} appointment as Arrived. We'll move the linked event from Lead to Consulted.`
    }
    return `Mark ${who}'s ${time} appointment as Arrived. The event is already ${event.status}, so its status will not change.`
  }
  if (action === 'no_show') {
    return `Mark ${who}'s ${time} appointment as a no-show. The CRM event status doesn't change.`
  }
  return `Cancel ${who}'s ${time} appointment. The CRM event status doesn't change.`
}

export default function AppointmentDetail() {
  const { appointmentId } = useParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [refreshTick, setRefreshTick] = useState(0)

  // Action modal state
  const [pendingAction, setPendingAction] = useState(null)
  const [actionSubmitting, setActionSubmitting] = useState(false)
  const [actionError, setActionError] = useState(null)

  // Notes editor state
  const [notesDraft, setNotesDraft] = useState('')
  const [notesEditing, setNotesEditing] = useState(false)
  const [notesSaving, setNotesSaving] = useState(false)
  const [notesError, setNotesError] = useState(null)

  // Add-participant dialog state
  const [participantOpen, setParticipantOpen] = useState(false)

  // Reassignment dialog state
  const [assignDialogOpen, setAssignDialogOpen] = useState(false)

  // Participant-tag dialog state (Phase 10.4)
  const [participantTagOpen, setParticipantTagOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    setError(null)
    salesGetAppointmentDetail(appointmentId)
      .then((d) => {
        if (cancelled) return
        setData(d)
        setNotesDraft(d.appointment.internal_notes || '')
      })
      .catch((err) => {
        if (cancelled) return
        const status = err?.response?.status
        if (status === 404) {
          setError("That appointment doesn't exist or has been removed.")
        } else {
          setError('Could not load this appointment.')
        }
      })
    return () => {
      cancelled = true
    }
  }, [appointmentId, refreshTick])

  async function handleConfirmAction() {
    if (!pendingAction || actionSubmitting) return
    setActionError(null)
    setActionSubmitting(true)
    try {
      await salesPostAppointmentStatus(appointmentId, pendingAction)
      setPendingAction(null)
      setRefreshTick((n) => n + 1)
    } catch (err) {
      if (isAttendanceGateError(err)) {
        setActionError(attendanceGateMessage())
        return
      }
      const detail = err?.response?.data?.detail
      setActionError(
        detail === 'missing_contact'
          ? "Can't promote this appointment. It has no contact attached."
          : detail === 'event_missing'
            ? "The linked event no longer exists. Reload and try again."
            : 'That action failed. Try again.',
      )
    } finally {
      setActionSubmitting(false)
    }
  }

  async function handleSaveNotes() {
    if (notesSaving) return
    setNotesError(null)
    setNotesSaving(true)
    // Optimistic: update the visible value, then reconcile with the response.
    const optimisticDraft = notesDraft
    try {
      const result = await salesPatchAppointmentNotes(
        appointmentId,
        optimisticDraft,
      )
      setData((prev) =>
        prev
          ? {
              ...prev,
              appointment: {
                ...prev.appointment,
                internal_notes: result.internal_notes ?? '',
              },
            }
          : prev,
      )
      setNotesEditing(false)
      // Pull the activity-row update so the timeline reflects the edit.
      setRefreshTick((n) => n + 1)
    } catch (err) {
      if (isAttendanceGateError(err)) {
        setNotesError(attendanceGateMessage())
      } else {
        setNotesError("Couldn't save those notes. Try again.")
      }
      setNotesDraft(data?.appointment?.internal_notes || '')
    } finally {
      setNotesSaving(false)
    }
  }

  function handleCancelNotes() {
    setNotesDraft(data?.appointment?.internal_notes || '')
    setNotesError(null)
    setNotesEditing(false)
  }

  if (error) {
    return (
      <Stack spacing={2}>
        <Breadcrumbs>
          <Link component={RouterLink} to="/" underline="hover">
            Today
          </Link>
          <Typography color="text.secondary">Appointment</Typography>
        </Breadcrumbs>
        <Alert severity="error">{error}</Alert>
      </Stack>
    )
  }

  if (!data) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress />
      </Box>
    )
  }

  const a = data.appointment
  const tz = a.timezone
  const isTerminal = TERMINAL_STATUSES.has(a.status)
  const previewText = pendingAction
    ? describePreview(a, data.event, pendingAction)
    : ''

  return (
    <Stack spacing={2}>
      <Breadcrumbs>
        <Link component={RouterLink} to="/" underline="hover">
          Today
        </Link>
        <Typography color="text.primary">
          {fullName(a.celebrant_first_name, a.celebrant_last_name) || 'Appointment'}
        </Typography>
      </Breadcrumbs>

      <Card>
        <CardContent>
          <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
            <Box>
              <Typography variant="h5" sx={{ fontWeight: 600 }}>
                {fullName(a.celebrant_first_name, a.celebrant_last_name) ||
                  '(no name)'}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {formatTime(a.slot_start_at, tz)} – {formatTime(a.slot_end_at, tz)}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: 'block', mt: 0.5 }}
              >
                {a.confirmation_code}
              </Typography>
            </Box>
            <Stack direction="column" spacing={0.5} alignItems="flex-end">
              <Chip
                size="small"
                label={a.status.replace('_', ' ')}
                color={STATUS_COLORS[a.status] || 'default'}
              />
              {data.event && (
                <Chip
                  size="small"
                  label={`event: ${data.event.status}`}
                  variant="outlined"
                />
              )}
              <Chip
                size="small"
                variant="outlined"
                label={
                  a.assigned_user_full_name
                    ? `assigned: ${a.assigned_user_full_name}`
                    : 'unassigned'
                }
                onClick={() => setAssignDialogOpen(true)}
              />
              {data.event && (
                <Chip
                  size="small"
                  variant="outlined"
                  label={(() => {
                    const id = a.event_participant_id ?? null
                    if (id == null) return 'buyer: untagged'
                    const p = (data.participants || []).find(
                      (row) => row.id === id,
                    )
                    if (!p) return 'buyer: (unknown)'
                    return `buyer: ${p.role} ${p.display_name}`
                  })()}
                  onClick={() => setParticipantTagOpen(true)}
                />
              )}
            </Stack>
          </Stack>

          <Divider sx={{ my: 2 }} />

          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={1}
            sx={{ '& > *': { flex: { sm: 1 } } }}
          >
            {(['arrived', 'no_show', 'cancelled']).map((act) => (
              <Button
                key={act}
                variant={ACTION_VARIANT[act]}
                color={ACTION_COLOR[act]}
                size="large"
                onClick={() => {
                  setActionError(null)
                  setPendingAction(act)
                }}
                disabled={isTerminal}
              >
                {ACTION_LABEL[act]}
              </Button>
            ))}
          </Stack>
          {isTerminal && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: 'block', mt: 1 }}
            >
              Status is set. Owner can adjust from admin if needed.
            </Typography>
          )}
        </CardContent>
      </Card>

      <Section title="Party">
        <Field
          label="Parent"
          value={fullName(a.parent_first_name, a.parent_last_name)}
        />
        <Field
          label="Celebrant"
          value={fullName(a.celebrant_first_name, a.celebrant_last_name)}
        />
        <Field label="Party size" value={a.party_size_bucket?.replace('_', ' ')} />
        <Field label="Phone" value={a.phone} />
        <Field label="Email" value={a.email} />
        {data.participants.length > 0 && (
          <>
            <Divider sx={{ my: 1 }} />
            <Typography variant="caption" color="text.secondary">
              Participants
            </Typography>
            {data.participants.map((p) => (
              <Stack
                key={p.id}
                direction="row"
                spacing={1}
                alignItems="baseline"
                sx={{ mt: 0.5 }}
              >
                <Typography variant="body2" sx={{ minWidth: 110 }}>
                  {p.role}
                </Typography>
                <Typography variant="body2">{p.display_name}</Typography>
                {p.phone && (
                  <Typography variant="caption" color="text.secondary">
                    · {p.phone}
                  </Typography>
                )}
              </Stack>
            ))}
          </>
        )}
        <Box sx={{ mt: 1.5 }}>
          {data.event ? (
            <Button
              size="small"
              variant="outlined"
              onClick={() => setParticipantOpen(true)}
            >
              Add participant
            </Button>
          ) : (
            <Typography variant="caption" color="text.secondary">
              Add a sister, friend, or court member once the appointment
              is checked in.
            </Typography>
          )}
        </Box>
      </Section>

      {data.enrichment && (
        <Section title="Enrichment">
          <Field
            label="Dress styles"
            value={
              data.enrichment.dress_styles?.length
                ? data.enrichment.dress_styles.join(', ')
                : null
            }
          />
          <Field label="Budget" value={data.enrichment.budget_range} />
          <Field label="Theme" value={data.enrichment.quince_theme} />
          <Field
            label="Theme colors"
            value={
              data.enrichment.quince_theme_colors?.length
                ? data.enrichment.quince_theme_colors.join(', ')
                : null
            }
          />
          <Field label="Court" value={data.enrichment.court_size} />
          <Field
            label="Size estimate"
            value={
              data.enrichment.estimated_size_low &&
              data.enrichment.estimated_size_high
                ? `${data.enrichment.estimated_size_low}–${data.enrichment.estimated_size_high}`
                : data.enrichment.estimated_size_low
            }
          />
          <Field label="Style preference" value={data.enrichment.style_preference} />
          <Field label="Back preference" value={data.enrichment.back_preference} />
          {data.enrichment.free_text && (
            <Box sx={{ mt: 1 }}>
              <Typography variant="caption" color="text.secondary">
                Notes from customer
              </Typography>
              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                {data.enrichment.free_text}
              </Typography>
            </Box>
          )}
        </Section>
      )}

      <TriedOnSection
        appointmentId={a.id}
        hasEvent={Boolean(data.event)}
        onArrivePrompt={
          isTerminal
            ? null
            : () => {
                setActionError(null)
                setPendingAction('arrived')
              }
        }
      />

      <QuotesSection
        event={data.event}
        contactId={data.contact?.id}
        contactName={data.contact?.display_name}
        onArrivePrompt={
          isTerminal
            ? null
            : () => {
                setActionError(null)
                setPendingAction('arrived')
              }
        }
      />

      <Section title="Internal notes">
        {notesEditing ? (
          <Stack spacing={1}>
            {notesError && <Alert severity="error">{notesError}</Alert>}
            <TextField
              multiline
              minRows={3}
              fullWidth
              autoFocus
              value={notesDraft}
              onChange={(e) => setNotesDraft(e.target.value)}
              placeholder="What does the next stylist need to know?"
            />
            <Stack direction="row" spacing={1} justifyContent="flex-end">
              <Button
                onClick={handleCancelNotes}
                size="small"
                disabled={notesSaving}
              >
                Cancel
              </Button>
              <Button
                variant="contained"
                size="small"
                onClick={handleSaveNotes}
                disabled={
                  notesSaving ||
                  notesDraft === (a.internal_notes || '')
                }
              >
                {notesSaving ? <CircularProgress size={16} /> : 'Save'}
              </Button>
            </Stack>
          </Stack>
        ) : (
          <Stack spacing={1}>
            {a.internal_notes ? (
              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                {a.internal_notes}
              </Typography>
            ) : (
              <Typography variant="body2" color="text.secondary">
                No internal notes yet.
              </Typography>
            )}
            <Box>
              <Button
                size="small"
                onClick={() => {
                  setNotesError(null)
                  setNotesDraft(a.internal_notes || '')
                  setNotesEditing(true)
                }}
              >
                {a.internal_notes ? 'Edit notes' : 'Add notes'}
              </Button>
            </Box>
          </Stack>
        )}
      </Section>

      {data.recent_activity.length > 0 && (
        <Section title="Recent activity">
          <Stack spacing={0.75}>
            {data.recent_activity.map((row) => (
              <Stack
                key={row.id}
                direction="row"
                spacing={1}
                alignItems="baseline"
              >
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ minWidth: 70, fontVariantNumeric: 'tabular-nums' }}
                >
                  {formatRelative(row.created_at)}
                </Typography>
                <Typography variant="body2">{describeActivity(row)}</Typography>
              </Stack>
            ))}
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
            Updated {formatDateTime(data.recent_activity[0]?.created_at, tz)}
          </Typography>
        </Section>
      )}

      <Dialog
        open={pendingAction !== null}
        onClose={() => (actionSubmitting ? null : setPendingAction(null))}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>
          {pendingAction ? `Mark as ${ACTION_LABEL[pendingAction]}` : ''}
        </DialogTitle>
        <DialogContent>
          {actionError && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {actionError}
            </Alert>
          )}
          <DialogContentText>{previewText}</DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => setPendingAction(null)}
            disabled={actionSubmitting}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color={pendingAction ? ACTION_COLOR[pendingAction] : 'primary'}
            onClick={handleConfirmAction}
            disabled={actionSubmitting}
          >
            {actionSubmitting ? (
              <CircularProgress size={20} sx={{ color: 'common.white' }} />
            ) : (
              `Confirm ${pendingAction ? ACTION_LABEL[pendingAction] : ''}`
            )}
          </Button>
        </DialogActions>
      </Dialog>

      <AddParticipantDialog
        eventId={data.event?.id}
        open={participantOpen}
        onClose={() => setParticipantOpen(false)}
        onAdded={() => setRefreshTick((n) => n + 1)}
      />

      <SalesAssignmentDialog
        open={assignDialogOpen}
        onClose={() => setAssignDialogOpen(false)}
        appointmentId={a.id}
        appointmentTimezone={tz}
        eventId={data.event?.id || null}
        currentAssignedUserId={a.assigned_user_id ?? null}
        currentEventOwnerId={data.event?.owner_user_id ?? null}
        onSuccess={() => setRefreshTick((n) => n + 1)}
      />

      <ParticipantTagDialog
        open={participantTagOpen}
        onClose={() => setParticipantTagOpen(false)}
        appointmentId={a.id}
        participants={data.participants || []}
        currentEventParticipantId={a.event_participant_id ?? null}
        tagFn={salesTagAppointmentParticipant}
        onSuccess={() => setRefreshTick((n) => n + 1)}
      />
    </Stack>
  )
}
