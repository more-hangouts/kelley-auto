import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  ButtonBase,
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
  FormControlLabel,
  IconButton,
  Link,
  MenuItem,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { Link as RouterLink } from 'react-router-dom'
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import EventBusyIcon from '@mui/icons-material/EventBusy'
import TodayIcon from '@mui/icons-material/Today'

import {
  createScheduleEntry,
  deleteScheduleEntry,
  excuseScheduleEntry,
  generateDraftScheduleWeek,
  getAdminScheduleWeek,
  getAutoScheduleRules,
  listSchedulePresets,
  patchScheduleEntry,
  publishScheduleEntry,
  publishScheduleWeek,
  resendPublishedScheduleWeek,
  setScheduleEntryNotes,
} from '../services/api'

// Manager weekly grid (Phase 10). Rows = active staff, cols = Mon-Sun.
// Each cell renders the published / draft entries for that (staff,
// day); empty cells are clickable to drop in a quick shift.
//
// The "secret sauce" the user asked for: approved time-off rows from
// the API gray out the matching cells with [Time off] and disable the
// click-to-add affordance — the manager physically cannot schedule
// over an approved request. Validation is also enforced server-side
// in services.staff_schedule, so this gate is for UX clarity.
//
// The "Preset" dropdown in the create-shift dialog is fed from
// /api/admin/schedule/presets (Slice 3). Falling back to an empty
// preset list keeps the dialog usable for fully-custom shifts when
// the presets endpoint errors.

const DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function startOfWeek(d) {
  const day = d.getDay() // 0=Sun, 1=Mon, ..., 6=Sat
  const offset = day === 0 ? 6 : day - 1
  const monday = new Date(d)
  monday.setDate(d.getDate() - offset)
  monday.setHours(0, 0, 0, 0)
  return monday
}

function isoDate(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function addDays(d, n) {
  const out = new Date(d)
  out.setDate(d.getDate() + n)
  return out
}

function parseIsoDate(iso) {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d)
}

function formatDayHeader(iso) {
  const dt = parseIsoDate(iso)
  return dt.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  })
}

function formatTimeOnly(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function entryHours(entry) {
  const start = new Date(entry.starts_at_local)
  const end = new Date(entry.ends_at_local)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return 0
  }
  const hours = (end.getTime() - start.getTime()) / 3_600_000
  return hours > 0 ? hours : 0
}

function formatHours(hours) {
  if (!Number.isFinite(hours)) return '0h'
  const rounded = Math.round(hours * 10) / 10
  return `${rounded.toFixed(rounded % 1 === 0 ? 0 : 1)}h`
}

function formatLaborCost(cents) {
  if (!Number.isFinite(cents) || cents <= 0) return '$0 labor'
  const dollars = Math.round(cents / 100)
  return `$${dollars.toLocaleString()} labor`
}

function formatDollarsCompact(cents) {
  if (!Number.isFinite(cents)) return '$0'
  const dollars = Math.round(cents / 100)
  return `$${dollars.toLocaleString()}`
}

function formatDensityWarning(warning) {
  const start = formatTimeOnly(warning.bucket_start_local)
  const end = formatTimeOnly(warning.bucket_end_local)
  return `${warning.business_date} ${start}-${end}: ${warning.appointment_count} appointments, ${warning.scheduled_stylist_count}/${warning.required_stylist_count} stylists`
}

function localDateTimeInputValue(date, hhmm) {
  // Build a "YYYY-MM-DDTHH:MM" string the <input type="datetime-local">
  // round-trips through. The browser interprets these in the user's
  // local zone, which is what the manager wants — they think in
  // boutique-local time.
  const [hh, mm] = hhmm.split(':')
  return `${date}T${hh.padStart(2, '0')}:${mm.padStart(2, '0')}`
}

function isoFromLocalInput(value) {
  // datetime-local inputs come back as 'YYYY-MM-DDTHH:mm' with no
  // timezone. Treat them as boutique-local by feeding them through
  // `new Date(...)` (browser's local tz) and emitting the resulting
  // absolute ISO string. The backend stamps the entry with this
  // boutique-local interpretation and validates business_date against
  // the same conversion.
  if (!value) return null
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return null
  return d.toISOString()
}

function isoToLocalDateTimeInput(iso) {
  // Inverse of `isoFromLocalInput` for pre-filling the edit dialog:
  // takes an absolute ISO timestamp from the API and emits
  // 'YYYY-MM-DDTHH:MM' in the user's local timezone — the browser's
  // local tz is the boutique's tz in practice, which matches what
  // the backend validates against.
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${day}T${hh}:${mm}`
}

function businessDateFromLocalInput(value) {
  // The backend's `_validate_business_date` requires the
  // `business_date` field on a PATCH to equal the local-date portion
  // of `starts_at_local`. Browser's local-tz parse of the datetime-
  // local input gives us the same calendar date the backend will see.
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function cellKey(userId, dayIso) {
  return `${userId}|${dayIso}`
}

function dayCoversBlock(dayIso, block) {
  // A time-off block covers the day if its [starts_at_local,
  // ends_at_local) interval intersects the day's local 00:00 → next-day
  // 00:00 window. Half-open at the end so a block ending exactly at
  // midnight does not bleed into the next day.
  const dayStart = new Date(`${dayIso}T00:00:00`)
  const dayEnd = new Date(dayStart)
  dayEnd.setDate(dayStart.getDate() + 1)
  const bStart = new Date(block.starts_at_local)
  const bEnd = new Date(block.ends_at_local)
  return bStart < dayEnd && bEnd > dayStart
}

// HH:MM → "h:MM AM/PM" for the auto-schedule dialog's selects. Backend
// stays HH:MM so the user types and the wire format never drift.
function formatTimeOfDay24(hhmm) {
  const [hStr, mStr] = hhmm.split(':')
  const h = Number(hStr)
  const m = Number(mStr)
  const period = h >= 12 ? 'PM' : 'AM'
  const h12 = h % 12 === 0 ? 12 : h % 12
  return `${h12}:${String(m).padStart(2, '0')} ${period}`
}

// 9:00 AM through 9:00 PM in 30-minute increments. Backend accepts
// HH:MM (24-hour), so each option keeps the wire value separate from
// the human-friendly label.
const SHIFT_TIME_OPTIONS = (() => {
  const opts = []
  for (let mins = 9 * 60; mins <= 21 * 60; mins += 30) {
    const h = Math.floor(mins / 60)
    const m = mins % 60
    const value = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
    opts.push({ value, label: formatTimeOfDay24(value) })
  }
  return opts
})()

// Mirrors `ALLOWED_APPOINTMENT_BUFFERS` in services/auto_scheduler.py.
const APPOINTMENT_BUFFER_OPTIONS = [30, 60, 90, 120]

function blockSummary(block) {
  const start = new Date(block.starts_at_local)
  const end = new Date(block.ends_at_local)
  const fmt = (d) =>
    d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  if (start.toDateString() === end.toDateString()) {
    return `${fmt(start)} (full day)`
  }
  return `${fmt(start)} → ${fmt(end)}`
}

export default function AdminScheduleGrid() {
  const [weekStart, setWeekStart] = useState(() => isoDate(startOfWeek(new Date())))
  const [data, setData] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [busy, setBusy] = useState(false)
  // Phase 10 Slice 3: presets come from /api/admin/schedule/presets.
  // A failed presets fetch is non-fatal — the dialog stays usable for
  // fully-custom shifts.
  const [presets, setPresets] = useState([])

  const [createDialog, setCreateDialog] = useState(null)
  const [detailDialog, setDetailDialog] = useState(null)
  const [generateDialog, setGenerateDialog] = useState(null)
  const [actionInfo, setActionInfo] = useState(null)

  async function refresh() {
    setLoadError(null)
    try {
      const body = await getAdminScheduleWeek({ week_start: weekStart })
      setData(body)
    } catch {
      setLoadError("Couldn't load the schedule. Try again.")
      setData({ staff: [], entries: [], time_off_blocks: [], days: [] })
    }
  }

  async function loadPresets() {
    try {
      const body = await listSchedulePresets()
      setPresets(body.presets || [])
    } catch {
      // Soft-fail. The Add-shift dialog still works without presets;
      // the manager can hand-type a start/end and custom grace.
      setPresets([])
    }
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weekStart])

  useEffect(() => {
    loadPresets()
  }, [])

  const entriesByCell = useMemo(() => {
    const out = new Map()
    if (!data?.entries) return out
    for (const e of data.entries) {
      const key = cellKey(e.user_id, e.business_date)
      if (!out.has(key)) out.set(key, [])
      out.get(key).push(e)
    }
    return out
  }, [data])

  const timeOffByCell = useMemo(() => {
    const out = new Map()
    if (!data?.staff || !data?.days || !data?.time_off_blocks) return out
    for (const staff of data.staff) {
      const userBlocks = data.time_off_blocks.filter(
        (b) => b.user_id === staff.id,
      )
      for (const day of data.days) {
        const hit = userBlocks.find((b) => dayCoversBlock(day, b))
        if (hit) out.set(cellKey(staff.id, day), hit)
      }
    }
    return out
  }, [data])

  // Phase 10 Slice 6: recurring unavailability blocks shown on the
  // grid the same way time-off is. The backend pre-expands them to
  // one entry per (user, business_date) so this is a flat group-by.
  const recurringBlocksByCell = useMemo(() => {
    const out = new Map()
    if (!data?.recurring_unavailable_blocks) return out
    for (const b of data.recurring_unavailable_blocks) {
      const key = cellKey(b.user_id, b.business_date)
      const list = out.get(key) ?? []
      list.push(b)
      out.set(key, list)
    }
    return out
  }, [data])

  const draftCount = useMemo(() => {
    if (!data?.entries) return 0
    return data.entries.filter((e) => e.status === 'draft').length
  }, [data])

  const publishedRecipientCount = useMemo(() => {
    // Number of unique staffers with at least one published shift in
    // this week — drives the "Resend week" button's enabled state and
    // its confirm-dialog copy. Counting unique users, not entries, so
    // a stylist with three shifts that week is one recipient.
    if (!data?.entries) return 0
    const ids = new Set()
    for (const e of data.entries) {
      if (e.status === 'published') {
        ids.add(e.user_id)
      }
    }
    return ids.size
  }, [data])

  const weeklyHoursByUser = useMemo(() => {
    const out = new Map()
    if (!data?.entries) return out
    for (const entry of data.entries) {
      out.set(entry.user_id, (out.get(entry.user_id) || 0) + entryHours(entry))
    }
    return out
  }, [data])

  const overtimeCount = useMemo(() => {
    if (!data?.staff) return 0
    return data.staff.filter((s) => (weeklyHoursByUser.get(s.id) || 0) > 40)
      .length
  }, [data, weeklyHoursByUser])

  const laborCostCents = data?.labor_cost?.total_cents ?? 0
  const unknownWageCount =
    data?.labor_cost?.unknown_wage_user_ids?.length ?? 0
  const draftLaborCents = data?.labor_cost?.draft_cents ?? 0

  const laborTarget = data?.labor_target ?? null
  const targetPct = laborTarget?.target_pct ?? null
  const targetSalesCents = laborTarget?.target_sales_cents ?? null
  const actualSalesCents = laborTarget?.actual_sales_cents ?? 0
  const salesGapCents = laborTarget?.gap_cents ?? null
  const densityWarnings = useMemo(
    () => data?.appointment_density_warnings ?? [],
    [data?.appointment_density_warnings],
  )
  const densityWarningsByDay = useMemo(() => {
    const out = new Map()
    for (const warning of densityWarnings) {
      const list = out.get(warning.business_date) ?? []
      list.push(warning)
      out.set(warning.business_date, list)
    }
    return out
  }, [densityWarnings])
  const exceptionCounts = data?.schedule_exception_counts ?? {}
  const exceptionCountsByDate = exceptionCounts.by_date ?? {}
  const exceptionCountsByCell = exceptionCounts.by_cell ?? {}

  // Phase 0: advisory overlap warnings. The backend flags same-stylist
  // entries whose intervals overlap; we outline the affected entries and
  // surface a count up top. Manual split shifts still schedule freely —
  // this never blocks, it just warns.
  const overlapWarnings = useMemo(
    () => data?.overlap_warnings ?? [],
    [data?.overlap_warnings],
  )
  const overlapEntryIds = useMemo(() => {
    const out = new Set()
    for (const w of overlapWarnings) {
      for (const id of w.entry_ids ?? []) out.add(id)
    }
    return out
  }, [overlapWarnings])

  function bumpWeek(deltaDays) {
    const next = addDays(parseIsoDate(weekStart), deltaDays)
    setWeekStart(isoDate(next))
  }

  function goToday() {
    setWeekStart(isoDate(startOfWeek(new Date())))
  }

  function openCreate(staff, dayIso) {
    setActionError(null)
    setCreateDialog({
      staff,
      dayIso,
      preset: '',
      start: localDateTimeInputValue(dayIso, '09:00'),
      end: localDateTimeInputValue(dayIso, '17:00'),
      lateGrace: 30,
      publish: false,
      notes: '',
    })
  }

  function applyPreset(presetId) {
    setCreateDialog((d) => {
      if (!d) return d
      if (!presetId) {
        // "Custom" — keep current start/end/grace.
        return { ...d, preset: '' }
      }
      const preset = presets.find((p) => String(p.id) === String(presetId))
      if (!preset) return { ...d, preset: '' }
      return {
        ...d,
        preset: String(preset.id),
        start: localDateTimeInputValue(d.dayIso, preset.start_time),
        end: localDateTimeInputValue(d.dayIso, preset.end_time),
        lateGrace: preset.late_grace_minutes,
      }
    })
  }

  function applyDetailPreset(presetId) {
    setDetailDialog((d) => {
      if (!d) return d
      if (!presetId) {
        return { ...d, presetDraft: '' }
      }
      const preset = presets.find((p) => String(p.id) === String(presetId))
      if (!preset) return { ...d, presetDraft: '' }
      // Combine the preset's time-of-day with whichever day the
      // manager is currently editing — startDraft's day if it parses,
      // otherwise the entry's original business_date. Keeps a
      // mid-edit day move from being reverted by a preset pick.
      const dayIso =
        businessDateFromLocalInput(d.startDraft) || d.entry.business_date
      return {
        ...d,
        presetDraft: String(preset.id),
        startDraft: localDateTimeInputValue(dayIso, preset.start_time),
        endDraft: localDateTimeInputValue(dayIso, preset.end_time),
        lateGraceDraft: preset.late_grace_minutes,
      }
    })
  }

  async function handleCreate(publish) {
    if (!createDialog) return
    const startIso = isoFromLocalInput(createDialog.start)
    const endIso = isoFromLocalInput(createDialog.end)
    if (!startIso || !endIso) {
      setActionError('Pick a valid start and end.')
      return
    }
    setBusy(true)
    setActionError(null)
    try {
      await createScheduleEntry({
        user_id: createDialog.staff.id,
        business_date: createDialog.dayIso,
        starts_at_local: startIso,
        ends_at_local: endIso,
        source: 'manual',
        late_grace_minutes: Number(createDialog.lateGrace),
        manager_notes: createDialog.notes?.trim() || null,
        publish,
      })
      setCreateDialog(null)
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'invalid_date_range') {
        setActionError('End must be after start.')
      } else if (code === 'business_date_mismatch') {
        setActionError(
          "The start time's date doesn't match the cell. Pick a time on the same day.",
        )
      } else if (code === 'time_off_conflict') {
        setActionError(
          'That stylist has approved time off covering this shift.',
        )
      } else if (code === 'duplicate_entry') {
        setActionError('An identical shift already exists.')
      } else {
        setActionError("Couldn't save that shift.")
      }
    } finally {
      setBusy(false)
    }
  }

  async function handleDeleteEntry(entry) {
    if (!window.confirm('Delete this draft shift?')) return
    setBusy(true)
    try {
      await deleteScheduleEntry(entry.id)
      setDetailDialog(null)
      await refresh()
    } catch {
      setActionError("Couldn't delete that shift.")
    } finally {
      setBusy(false)
    }
  }

  async function handleSaveDetailNotes(entry, notes) {
    setBusy(true)
    try {
      await setScheduleEntryNotes(entry.id, notes)
      setDetailDialog(null)
      await refresh()
    } catch {
      setActionError("Couldn't save the note.")
    } finally {
      setBusy(false)
    }
  }

  async function handleSaveDraftEdit() {
    if (!detailDialog?.entry) return
    const startIso = isoFromLocalInput(detailDialog.startDraft)
    const endIso = isoFromLocalInput(detailDialog.endDraft)
    if (!startIso || !endIso) {
      setActionError('Pick a valid start and end.')
      return
    }
    const business_date = businessDateFromLocalInput(detailDialog.startDraft)
    if (!business_date) {
      setActionError('Pick a valid start time.')
      return
    }
    setBusy(true)
    setActionError(null)
    try {
      await patchScheduleEntry(detailDialog.entry.id, {
        business_date,
        starts_at_local: startIso,
        ends_at_local: endIso,
        late_grace_minutes: Number(detailDialog.lateGraceDraft),
        manager_notes: (detailDialog.notesDraft || '').trim() || null,
      })
      setDetailDialog(null)
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'invalid_date_range') {
        setActionError('End must be after start.')
      } else if (code === 'business_date_mismatch') {
        setActionError(
          "The start time's date doesn't match the cell.",
        )
      } else if (code === 'duplicate_entry') {
        setActionError('An identical shift already exists.')
      } else if (code === 'entry_already_published') {
        // Defensive: another tab published this row between our open
        // and our save. Refresh so the UI catches up.
        setActionError(
          'This shift was published in another tab. Refreshing.',
        )
        await refresh()
      } else {
        setActionError("Couldn't save that shift.")
      }
    } finally {
      setBusy(false)
    }
  }

  async function handlePublishEntry(entry) {
    setBusy(true)
    setActionError(null)
    try {
      await publishScheduleEntry(entry.id)
      setDetailDialog(null)
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'time_off_conflict') {
        setActionError(
          'Cannot publish: this stylist has approved time off during the shift.',
        )
      } else if (code === 'entry_already_published') {
        setActionError(
          'That shift is already published. Refreshing.',
        )
        await refresh()
      } else if (code === 'entry_not_found') {
        setActionError('That shift no longer exists.')
        await refresh()
      } else {
        setActionError("Couldn't publish that shift.")
      }
    } finally {
      setBusy(false)
    }
  }

  async function handleExcuseEntry(entry, notes) {
    setBusy(true)
    try {
      await excuseScheduleEntry(entry.id, notes)
      setDetailDialog(null)
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'entry_not_no_show') {
        setActionError('Only no-show shifts can be marked excused.')
      } else {
        setActionError("Couldn't excuse that shift.")
      }
    } finally {
      setBusy(false)
    }
  }

  async function openGenerateDialog() {
    setActionError(null)
    setGenerateDialog({
      rules: null,
      form: null,
      formError: null,
      loading: true,
      running: false,
    })
    try {
      const rules = await getAutoScheduleRules()
      // Form is seeded from backend defaults so a click-through with
      // no edits sends exactly the existing behavior. open_days isn't
      // user-editable in this slice but we carry it on the form so the
      // submit payload is complete.
      const form = {
        open_days: rules.open_days,
        no_appointment_shift_start: rules.no_appointment_shift_start,
        no_appointment_shift_end: rules.no_appointment_shift_end,
        appointment_buffer_minutes: Number(rules.appointment_buffer_minutes),
        min_stylists_when_appointments: Number(
          rules.min_stylists_when_appointments,
        ),
        min_stylists_when_quiet: Number(rules.min_stylists_when_quiet),
        rotate_fairly: Boolean(rules.rotate_fairly),
      }
      setGenerateDialog({
        rules,
        form,
        formError: null,
        loading: false,
        running: false,
      })
    } catch {
      setGenerateDialog(null)
      setActionError("Couldn't load auto-schedule rules.")
    }
  }

  function updateGenerateForm(patch) {
    setGenerateDialog((d) =>
      d && d.form
        ? { ...d, form: { ...d.form, ...patch }, formError: null }
        : d,
    )
  }

  function validateGenerateForm(form) {
    if (!form) return 'Form not ready.'
    if (
      !form.no_appointment_shift_start ||
      !form.no_appointment_shift_end ||
      form.no_appointment_shift_end <= form.no_appointment_shift_start
    ) {
      return 'No-booking shift end must be after the start.'
    }
    if (!APPOINTMENT_BUFFER_OPTIONS.includes(form.appointment_buffer_minutes)) {
      return 'Appointment buffer must be 30, 60, 90, or 120 minutes.'
    }
    const minA = Number(form.min_stylists_when_appointments)
    const minQ = Number(form.min_stylists_when_quiet)
    if (!Number.isFinite(minA) || minA < 1 || !Number.isFinite(minQ) || minQ < 1) {
      return 'Minimum stylist counts must be at least 1.'
    }
    return null
  }

  async function handleGenerateDraftWeek() {
    if (!generateDialog || generateDialog.running) return
    const form = generateDialog.form
    const formError = validateGenerateForm(form)
    if (formError) {
      setGenerateDialog((d) => (d ? { ...d, formError } : d))
      return
    }
    setGenerateDialog((d) =>
      d ? { ...d, running: true, formError: null } : d,
    )
    setActionError(null)
    setActionInfo(null)
    try {
      const overrides = {
        open_days: form.open_days,
        no_appointment_shift_start: form.no_appointment_shift_start,
        no_appointment_shift_end: form.no_appointment_shift_end,
        appointment_buffer_minutes: Number(form.appointment_buffer_minutes),
        min_stylists_when_appointments: Number(
          form.min_stylists_when_appointments,
        ),
        min_stylists_when_quiet: Number(form.min_stylists_when_quiet),
        rotate_fairly: Boolean(form.rotate_fairly),
      }
      const body = await generateDraftScheduleWeek({
        week_start: weekStart,
        overrides,
      })
      await refresh()
      const created = body?.created_count ?? 0
      const skippedExisting = body?.skipped_existing_count ?? 0
      const skippedTimeOff = body?.skipped_time_off_count ?? 0
      const warnings = body?.warnings ?? []
      const parts = [
        `Generated ${created} draft shift${created === 1 ? '' : 's'}. Review them before publishing.`,
      ]
      if (skippedExisting > 0) {
        parts.push(
          `Left ${skippedExisting} existing entr${skippedExisting === 1 ? 'y' : 'ies'} untouched.`,
        )
      }
      if (skippedTimeOff > 0) {
        parts.push(
          `Skipped ${skippedTimeOff} stylist-day${skippedTimeOff === 1 ? '' : 's'} on approved time off.`,
        )
      }
      if (warnings.length > 0) {
        parts.push(`Warnings: ${warnings.join('; ')}`)
      }
      setActionInfo({
        severity: warnings.length > 0 ? 'warning' : 'success',
        message: parts.join(' '),
      })
      setGenerateDialog(null)
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      let inline = null
      if (code === 'week_start_not_monday') {
        setActionError('Pick a Monday-anchored week before generating drafts.')
      } else if (code === 'invalid_appointment_buffer') {
        inline = 'Appointment buffer must be 30, 60, 90, or 120 minutes.'
      } else if (code === 'invalid_min_stylists') {
        inline = 'Minimum stylist counts must be at least 1.'
      } else if (code === 'invalid_no_appointment_window') {
        inline = 'No-booking shift end must be after the start.'
      } else if (code === 'invalid_business_hours') {
        inline = 'Business close time must be after open time.'
      } else {
        setActionError("Couldn't generate the draft week.")
      }
      setGenerateDialog((d) =>
        d ? { ...d, running: false, formError: inline ?? d.formError } : d,
      )
    }
  }

  async function handleResendWeek() {
    if (publishedRecipientCount === 0) return
    if (
      !window.confirm(
        `Resend the published schedule to ${publishedRecipientCount} staff member${publishedRecipientCount === 1 ? '' : 's'} for this week? Each affected staffer gets one "Your schedule was published" email.`,
      )
    ) {
      return
    }
    setBusy(true)
    setActionError(null)
    try {
      const body = await resendPublishedScheduleWeek(weekStart, {})
      const sent = body?.jobs_enqueued ?? 0
      setActionInfo({
        severity: 'success',
        message:
          sent === 0
            ? 'No staff to resend to (no published shifts in this week).'
            : `Resent the schedule to ${sent} staff member${sent === 1 ? '' : 's'}.`,
      })
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'week_start_not_monday') {
        setActionError("Can't resend: week start must be a Monday.")
      } else {
        setActionError("Couldn't resend the schedule.")
      }
    } finally {
      setBusy(false)
    }
  }

  async function handlePublishWeek() {
    if (
      !window.confirm(
        `Publish ${draftCount} draft shift${draftCount === 1 ? '' : 's'} for this week? Staff will see them in their portal.`,
      )
    ) {
      return
    }
    setBusy(true)
    setActionError(null)
    try {
      // Slice-4: publish is per-shift partial. The response includes
      // `skipped[]` listing drafts that overlap an approved time-off
      // request — those stay drafts, the rest publish.
      const body = await publishScheduleWeek({ week_start: weekStart })
      await refresh()
      const skipped = body?.skipped ?? []
      if (skipped.length > 0) {
        setActionError(
          `Cannot publish: ${skipped.length === 1 ? 'employee has' : `${skipped.length} shifts have`} approved time off during this shift. Edit or delete ${skipped.length === 1 ? 'that draft' : 'those drafts'} and try again.`,
        )
      }
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'time_off_conflict') {
        // Defensive: pre-Slice-4 wholesale-abort shape; should not
        // fire under the new backend but kept so a partial deploy
        // doesn't break the UI.
        const offending = err?.response?.data?.detail?.entries ?? []
        setActionError(
          `Cannot publish: ${offending.length} shift${offending.length === 1 ? '' : 's'} overlap approved time off.`,
        )
      } else {
        setActionError("Couldn't publish the week.")
      }
    } finally {
      setBusy(false)
    }
  }

  if (data === null) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress />
      </Box>
    )
  }

  const days = data.days || []
  const staff = data.staff || []

  return (
    <Stack spacing={2}>
      {/* Header: navigation + publish */}
      <Stack
        direction={{ xs: 'column', md: 'row' }}
        justifyContent="space-between"
        alignItems={{ xs: 'flex-start', md: 'center' }}
        spacing={1.5}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <Tooltip title="Previous week" arrow>
            <span>
              <IconButton
                size="small"
                onClick={() => bumpWeek(-7)}
                disabled={busy}
              >
                <ChevronLeftIcon />
              </IconButton>
            </span>
          </Tooltip>
          <Typography variant="h6" sx={{ minWidth: 220 }}>
            {`Week of ${formatDayHeader(weekStart)}`}
          </Typography>
          <Tooltip title="Next week" arrow>
            <span>
              <IconButton
                size="small"
                onClick={() => bumpWeek(7)}
                disabled={busy}
              >
                <ChevronRightIcon />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Jump to this week" arrow>
            <span>
              <IconButton
                size="small"
                onClick={goToday}
                disabled={busy}
                sx={{ color: 'text.secondary' }}
              >
                <TodayIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </Stack>
        <Stack direction="row" spacing={1} alignItems="center">
          <Chip
            label={`${draftCount} draft${draftCount === 1 ? '' : 's'}`}
            size="small"
            color={draftCount > 0 ? 'warning' : 'default'}
            variant={draftCount > 0 ? 'filled' : 'outlined'}
          />
          <Chip
            label={`${overtimeCount} overtime`}
            size="small"
            color={overtimeCount > 0 ? 'error' : 'default'}
            variant={overtimeCount > 0 ? 'filled' : 'outlined'}
          />
          <Tooltip
            arrow
            title={
              unknownWageCount > 0
                ? `${unknownWageCount} stylist${unknownWageCount === 1 ? '' : 's'} missing an hourly wage. Set wages in Staff settings to include them in this total.`
                : draftLaborCents > 0
                  ? `Scheduled hours × hourly wage for the visible week. Includes $${Math.round(draftLaborCents / 100).toLocaleString()} from drafts.`
                  : 'Scheduled hours × hourly wage for the visible week.'
            }
          >
            <Chip
              label={formatLaborCost(laborCostCents)}
              size="small"
              variant="outlined"
              color={unknownWageCount > 0 ? 'warning' : 'default'}
            />
          </Tooltip>
          {targetSalesCents != null && (
            <Tooltip
              arrow
              title={
                salesGapCents != null && salesGapCents > 0
                  ? `Target ${targetPct}% labor share. Need ${formatDollarsCompact(salesGapCents)} more in sales this week to hit goal.`
                  : `Target ${targetPct}% labor share. You are ${formatDollarsCompact(Math.abs(salesGapCents ?? 0))} past goal.`
              }
            >
              <Chip
                label={`Goal ${formatDollarsCompact(targetSalesCents)} · actual ${formatDollarsCompact(actualSalesCents)}`}
                size="small"
                variant="outlined"
                color={
                  salesGapCents != null && salesGapCents > 0
                    ? 'warning'
                    : 'success'
                }
              />
            </Tooltip>
          )}
          <Button
            variant="outlined"
            onClick={openGenerateDialog}
            disabled={busy}
          >
            Generate draft schedule
          </Button>
          <Tooltip
            title={
              publishedRecipientCount === 0
                ? 'No published shifts to resend in this week'
                : `Resend the schedule email to ${publishedRecipientCount} staff member${publishedRecipientCount === 1 ? '' : 's'}`
            }
            arrow
          >
            <span>
              <Button
                variant="outlined"
                onClick={handleResendWeek}
                disabled={busy || publishedRecipientCount === 0}
              >
                Resend week
              </Button>
            </span>
          </Tooltip>
          <Button
            variant="contained"
            onClick={handlePublishWeek}
            disabled={busy || draftCount === 0}
          >
            Publish week
          </Button>
        </Stack>
      </Stack>

      {loadError && <Alert severity="error">{loadError}</Alert>}
      {actionError && (
        <Alert severity="error" onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}
      {actionInfo && (
        <Alert
          severity={actionInfo.severity}
          onClose={() => setActionInfo(null)}
        >
          {actionInfo.message}
        </Alert>
      )}
      {densityWarnings.length > 0 && (
        <Alert severity="warning">
          {densityWarnings.length} appointment density warning
          {densityWarnings.length === 1 ? '' : 's'} this week:{' '}
          {densityWarnings.slice(0, 3).map(formatDensityWarning).join('; ')}
          {densityWarnings.length > 3 ? '...' : ''}
        </Alert>
      )}
      {overlapWarnings.length > 0 && (
        <Alert severity="warning">
          {overlapWarnings.length} overlapping shift
          {overlapWarnings.length === 1 ? '' : 's'} this week. Outlined
          cells have a stylist scheduled for two shifts at once. Split
          shifts that don&apos;t overlap are fine; fix or confirm the rest
          before publishing.
        </Alert>
      )}

      {staff.length === 0 ? (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="body2" color="text.secondary">
              No active staff to schedule. Add a stylist in Staff
              profiles, then come back.
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <Card variant="outlined">
          <CardContent sx={{ p: { xs: 1, sm: 1.5 }, overflowX: 'auto' }}>
            <Table size="small" sx={{ tableLayout: 'fixed', minWidth: 900 }}>
              <TableHead>
                <TableRow>
                  <TableCell sx={{ width: 160, fontWeight: 600 }}>
                    Stylist
                  </TableCell>
                  {days.map((day, idx) => (
                    <TableCell
                      key={day}
                      align="center"
                      sx={{ fontWeight: 600, py: 1 }}
                    >
                      {(() => {
                        const dayWarnings = densityWarningsByDay.get(day) || []
                        const dayExceptions = exceptionCountsByDate[day] || {}
                        const pendingCount =
                          dayExceptions.pending_requests || 0
                        const openShiftCount = dayExceptions.open_shifts || 0
                        const conflictCount = dayExceptions.conflicts || 0
                        return (
                          <>
                      <Box>{DOW_LABELS[idx]}</Box>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                      >
                        {formatDayHeader(day)}
                      </Typography>
                            {dayWarnings.length > 0 && (
                              <Tooltip
                                arrow
                                title={dayWarnings
                                  .map(formatDensityWarning)
                                  .join('; ')}
                              >
                                <Chip
                                  size="small"
                                  color="warning"
                                  label={`${dayWarnings.length} density`}
                                  sx={{ mt: 0.5 }}
                                />
                              </Tooltip>
                            )}
                            {(pendingCount > 0 ||
                              openShiftCount > 0 ||
                              conflictCount > 0) && (
                              <Stack
                                direction="row"
                                spacing={0.5}
                                justifyContent="center"
                                flexWrap="wrap"
                                useFlexGap
                                sx={{ mt: 0.5 }}
                              >
                                {pendingCount > 0 && (
                                  <Chip
                                    size="small"
                                    color="info"
                                    label={`${pendingCount} req`}
                                  />
                                )}
                                {openShiftCount > 0 && (
                                  <Chip
                                    size="small"
                                    color="success"
                                    variant="outlined"
                                    label={`${openShiftCount} open`}
                                  />
                                )}
                                {conflictCount > 0 && (
                                  <Chip
                                    size="small"
                                    color="warning"
                                    label={`${conflictCount} conflict`}
                                  />
                                )}
                              </Stack>
                            )}
                          </>
                        )
                      })()}
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {staff.map((s) => {
                  const weeklyHours = weeklyHoursByUser.get(s.id) || 0
                  const isNearOvertime = weeklyHours >= 36 && weeklyHours <= 40
                  const isOvertime = weeklyHours > 40
                  return (
                    <TableRow
                      key={s.id}
                      sx={
                        isOvertime
                          ? { backgroundColor: 'rgba(244,67,54,0.035)' }
                          : isNearOvertime
                            ? { backgroundColor: 'rgba(255,152,0,0.035)' }
                            : undefined
                      }
                    >
                      <TableCell sx={{ verticalAlign: 'top' }}>
                        <Stack spacing={0.75} alignItems="flex-start">
                          <Typography variant="body2" sx={{ fontWeight: 500 }}>
                            {s.full_name || s.username}
                          </Typography>
                          <Tooltip
                            title={
                              isOvertime
                                ? 'Scheduled over 40 hours this week'
                                : isNearOvertime
                                  ? 'Approaching 40 scheduled hours this week'
                                  : 'Scheduled hours this week'
                            }
                            arrow
                          >
                            <Chip
                              size="small"
                              label={formatHours(weeklyHours)}
                              color={
                                isOvertime
                                  ? 'error'
                                  : isNearOvertime
                                    ? 'warning'
                                    : 'default'
                              }
                              variant={
                                isOvertime || isNearOvertime
                                  ? 'filled'
                                  : 'outlined'
                              }
                            />
                          </Tooltip>
                        </Stack>
                      </TableCell>
                      {days.map((day) => {
                      const key = cellKey(s.id, day)
                      const block = timeOffByCell.get(key)
                      const cellEntries = entriesByCell.get(key) || []
                      const recurringBlocks = recurringBlocksByCell.get(key) || []
                      const cellExceptions = exceptionCountsByCell[key] || {}
                      return (
                        <TableCell
                          key={day}
                          align="center"
                          sx={{
                            verticalAlign: 'top',
                            py: 0.5,
                            backgroundColor: block
                              ? 'rgba(244,67,54,0.06)'
                              : recurringBlocks.length > 0
                                ? 'rgba(255,152,0,0.05)'
                                : undefined,
                            border: block
                              ? '1px dashed rgba(244,67,54,0.4)'
                              : undefined,
                          }}
                        >
                          <Stack spacing={0.5}>
                            {block && (
                              <Tooltip
                                title={`Approved time off — ${blockSummary(block)}`}
                                arrow
                              >
                                <Stack
                                  alignItems="center"
                                  spacing={0.25}
                                  sx={{ color: 'error.dark' }}
                                >
                                  <EventBusyIcon fontSize="small" />
                                  <Typography
                                    variant="caption"
                                    sx={{ fontWeight: 600 }}
                                  >
                                    Time off
                                  </Typography>
                                </Stack>
                              </Tooltip>
                            )}
                            {recurringBlocks.map((rb) => (
                              <Tooltip
                                key={rb.block_id}
                                arrow
                                title={
                                  rb.reason
                                    ? `Stylist marked unavailable: ${rb.reason}`
                                    : 'Stylist marked unavailable'
                                }
                              >
                                <Chip
                                  size="small"
                                  variant="outlined"
                                  color="warning"
                                  label={`Unavail ${formatTimeOnly(rb.starts_at_local)} – ${formatTimeOnly(rb.ends_at_local)}`}
                                />
                              </Tooltip>
                            ))}
                            {(cellExceptions.pending_requests > 0 ||
                              cellExceptions.conflicts > 0) && (
                              <Stack
                                direction="row"
                                spacing={0.5}
                                justifyContent="center"
                                flexWrap="wrap"
                                useFlexGap
                              >
                                {cellExceptions.pending_requests > 0 && (
                                  <Chip
                                    size="small"
                                    color="info"
                                    label={`${cellExceptions.pending_requests} req`}
                                  />
                                )}
                                {cellExceptions.conflicts > 0 && (
                                  <Chip
                                    size="small"
                                    color="warning"
                                    label={`${cellExceptions.conflicts} conflict`}
                                  />
                                )}
                              </Stack>
                            )}
                            {cellEntries.map((entry) => (
                              <ButtonBase
                                key={entry.id}
                                onClick={() =>
                                  setDetailDialog({
                                    entry,
                                    notesDraft: entry.manager_notes || '',
                                    // Pre-seed editable fields for the
                                    // draft-edit path. Published entries
                                    // ignore these.
                                    startDraft: isoToLocalDateTimeInput(
                                      entry.starts_at_local,
                                    ),
                                    endDraft: isoToLocalDateTimeInput(
                                      entry.ends_at_local,
                                    ),
                                    lateGraceDraft: entry.late_grace_minutes,
                                    // Preset is 'Custom' by default —
                                    // we don't try to back-match the
                                    // entry's times to an existing
                                    // preset (an edited draft might
                                    // not align with any single row).
                                    presetDraft: '',
                                  })
                                }
                                sx={{
                                  display: 'block',
                                  width: '100%',
                                  textAlign: 'left',
                                  borderRadius: 1,
                                  p: 0.75,
                                  backgroundColor:
                                    entry.status === 'published'
                                      ? 'primary.50'
                                      : 'rgba(0,0,0,0.04)',
                                  border:
                                    entry.status === 'published'
                                      ? '1px solid'
                                      : '1px dashed',
                                  borderColor:
                                    isOvertime
                                      ? 'error.main'
                                      : overlapEntryIds.has(entry.id)
                                        ? 'warning.main'
                                      : isNearOvertime
                                        ? 'warning.main'
                                        : entry.status === 'published'
                                      ? 'primary.main'
                                      : 'text.disabled',
                                }}
                              >
                                <Typography
                                  variant="caption"
                                  sx={{ fontWeight: 600, display: 'block' }}
                                >
                                  {formatTimeOnly(entry.starts_at_local)} –{' '}
                                  {formatTimeOnly(entry.ends_at_local)}
                                </Typography>
                                <Stack
                                  direction="row"
                                  spacing={0.5}
                                  sx={{ mt: 0.25 }}
                                  flexWrap="wrap"
                                >
                                  {entry.status === 'draft' && (
                                    <Chip
                                      label="Draft"
                                      size="small"
                                      variant="outlined"
                                    />
                                  )}
                                  {overlapEntryIds.has(entry.id) && (
                                    <Chip
                                      label="Overlap"
                                      size="small"
                                      color="warning"
                                    />
                                  )}
                                  {entry.attendance_status === 'present' && (
                                    <Chip
                                      label="Present"
                                      size="small"
                                      color="success"
                                    />
                                  )}
                                  {entry.attendance_status === 'late' && (
                                    <Chip
                                      label="Late"
                                      size="small"
                                      color="warning"
                                    />
                                  )}
                                  {entry.attendance_status === 'no_show' && (
                                    <Chip
                                      label="No show"
                                      size="small"
                                      color="error"
                                    />
                                  )}
                                  {entry.attendance_status === 'excused' && (
                                    <Chip
                                      label="Excused"
                                      size="small"
                                      variant="outlined"
                                    />
                                  )}
                                </Stack>
                              </ButtonBase>
                            ))}
                            <Tooltip
                              arrow
                              title={
                                block
                                  ? 'Approved time off covers this day — remove the time off to add a shift'
                                  : ''
                              }
                            >
                              <span>
                                <ButtonBase
                                  onClick={() => openCreate(s, day)}
                                  disabled={busy || !!block}
                                  sx={{
                                    display: 'block',
                                    width: '100%',
                                    py: 0.5,
                                    borderRadius: 1,
                                    color: 'text.secondary',
                                    fontSize: '0.75rem',
                                    '&:hover': {
                                      backgroundColor: 'action.hover',
                                      color: 'text.primary',
                                    },
                                  }}
                                >
                                  + Add shift
                                </ButtonBase>
                              </span>
                            </Tooltip>
                          </Stack>
                        </TableCell>
                      )
                      })}
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Create-entry dialog */}
      <Dialog
        open={createDialog !== null}
        onClose={() => setCreateDialog(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {createDialog
            ? `Add shift — ${createDialog.staff.full_name || createDialog.staff.username} · ${formatDayHeader(createDialog.dayIso)}`
            : 'Add shift'}
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Pick a preset or set custom times. <strong>Save draft</strong>{' '}
            keeps the shift hidden from staff so you can compose the
            whole week first; <strong>Save and publish</strong> makes it
            visible to the stylist's portal right away. You can also
            publish a saved draft later by clicking it and choosing
            "Publish shift".
          </DialogContentText>
          <Stack spacing={2}>
            <TextField
              select
              label="Preset"
              value={createDialog?.preset ?? ''}
              onChange={(e) => applyPreset(e.target.value)}
              size="small"
              fullWidth
              helperText={
                presets.length === 0 ? (
                  <>
                    No presets configured.{' '}
                    <Link
                      component={RouterLink}
                      to="/settings/staff/schedule/presets"
                    >
                      Add some
                    </Link>{' '}
                    to populate this dropdown.
                  </>
                ) : (
                  <>
                    Manage in{' '}
                    <Link
                      component={RouterLink}
                      to="/settings/staff/schedule/presets"
                    >
                      Shift presets
                    </Link>
                    .
                  </>
                )
              }
            >
              <MenuItem value="">Custom</MenuItem>
              {presets.map((p) => (
                <MenuItem key={p.id} value={String(p.id)}>
                  {p.label}
                </MenuItem>
              ))}
            </TextField>
            <Stack direction="row" spacing={1}>
              <TextField
                label="Start"
                type="datetime-local"
                value={createDialog?.start ?? ''}
                onChange={(e) =>
                  setCreateDialog((d) => ({ ...d, start: e.target.value }))
                }
                InputLabelProps={{ shrink: true }}
                fullWidth
              />
              <TextField
                label="End"
                type="datetime-local"
                value={createDialog?.end ?? ''}
                onChange={(e) =>
                  setCreateDialog((d) => ({ ...d, end: e.target.value }))
                }
                InputLabelProps={{ shrink: true }}
                fullWidth
              />
            </Stack>
            <TextField
              label="Late grace (minutes)"
              type="number"
              value={createDialog?.lateGrace ?? 30}
              onChange={(e) =>
                setCreateDialog((d) => ({ ...d, lateGrace: e.target.value }))
              }
              inputProps={{ min: 0, max: 120 }}
              helperText="If the stylist isn't clocked in by this many minutes past the start, the cron flags it as a no-show."
              fullWidth
            />
            <TextField
              label="Manager notes (optional)"
              value={createDialog?.notes ?? ''}
              onChange={(e) =>
                setCreateDialog((d) => ({ ...d, notes: e.target.value }))
              }
              multiline
              minRows={2}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateDialog(null)}>Cancel</Button>
          <Button onClick={() => handleCreate(false)} disabled={busy}>
            Save draft
          </Button>
          <Button
            variant="contained"
            onClick={() => handleCreate(true)}
            disabled={busy}
          >
            Save and publish
          </Button>
        </DialogActions>
      </Dialog>

      {/* Entry detail dialog */}
      <Dialog
        open={detailDialog !== null}
        onClose={() => setDetailDialog(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {detailDialog?.entry
            ? `Shift · ${formatDayHeader(detailDialog.entry.business_date)} · ${formatTimeOnly(detailDialog.entry.starts_at_local)} – ${formatTimeOnly(detailDialog.entry.ends_at_local)}`
            : ''}
        </DialogTitle>
        <DialogContent>
          {detailDialog?.entry && (
            <Stack spacing={2}>
              <Stack direction="row" spacing={1}>
                <Chip
                  label={detailDialog.entry.status}
                  size="small"
                  color={
                    detailDialog.entry.status === 'published'
                      ? 'primary'
                      : 'default'
                  }
                />
                <Chip
                  label={detailDialog.entry.attendance_status}
                  size="small"
                  color={
                    detailDialog.entry.attendance_status === 'no_show'
                      ? 'error'
                      : detailDialog.entry.attendance_status === 'late'
                        ? 'warning'
                        : detailDialog.entry.attendance_status === 'present'
                          ? 'success'
                          : 'default'
                  }
                />
              </Stack>
              <Divider />
              {/* Draft entries expose start/end/grace; published rows
                  keep their times immutable per backend contract and
                  only let the manager amend notes here. */}
              {detailDialog.entry.status === 'draft' && (
                <>
                  <TextField
                    select
                    label="Preset"
                    value={detailDialog.presetDraft ?? ''}
                    onChange={(e) => applyDetailPreset(e.target.value)}
                    size="small"
                    fullWidth
                    helperText={
                      presets.length === 0 ? (
                        <>
                          No presets configured.{' '}
                          <Link
                            component={RouterLink}
                            to="/settings/staff/schedule/presets"
                          >
                            Add some
                          </Link>{' '}
                          to populate this dropdown.
                        </>
                      ) : (
                        <>
                          Pick to refill start, end, and grace from a
                          configured preset.
                        </>
                      )
                    }
                  >
                    <MenuItem value="">Custom</MenuItem>
                    {presets.map((p) => (
                      <MenuItem key={p.id} value={String(p.id)}>
                        {p.label}
                      </MenuItem>
                    ))}
                  </TextField>
                  <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                    <TextField
                      label="Start"
                      type="datetime-local"
                      value={detailDialog.startDraft || ''}
                      onChange={(e) =>
                        setDetailDialog((d) => ({
                          ...d,
                          startDraft: e.target.value,
                          // Hand-edit resets preset to Custom so the
                          // dropdown reflects "no preset chosen."
                          presetDraft: '',
                        }))
                      }
                      InputLabelProps={{ shrink: true }}
                      fullWidth
                    />
                    <TextField
                      label="End"
                      type="datetime-local"
                      value={detailDialog.endDraft || ''}
                      onChange={(e) =>
                        setDetailDialog((d) => ({
                          ...d,
                          endDraft: e.target.value,
                          presetDraft: '',
                        }))
                      }
                      InputLabelProps={{ shrink: true }}
                      fullWidth
                    />
                  </Stack>
                  <TextField
                    label="Late grace (minutes)"
                    type="number"
                    value={detailDialog.lateGraceDraft ?? 30}
                    onChange={(e) =>
                      setDetailDialog((d) => ({
                        ...d,
                        lateGraceDraft: e.target.value,
                        presetDraft: '',
                      }))
                    }
                    inputProps={{ min: 0, max: 120 }}
                    helperText="No-show flag fires this many minutes past start without a clock-in."
                    fullWidth
                  />
                </>
              )}
              <TextField
                label="Manager notes"
                value={detailDialog.notesDraft}
                onChange={(e) =>
                  setDetailDialog((d) => ({
                    ...d,
                    notesDraft: e.target.value,
                  }))
                }
                multiline
                minRows={3}
                fullWidth
              />
              {detailDialog.entry.attendance_status === 'no_show' && (
                <Alert severity="warning" sx={{ alignItems: 'center' }}>
                  <Stack
                    direction={{ xs: 'column', sm: 'row' }}
                    spacing={1}
                    alignItems={{ xs: 'flex-start', sm: 'center' }}
                  >
                    <Box sx={{ flex: 1 }}>
                      This stylist did not clock in within the grace
                      window. Excuse it once you've talked to them.
                    </Box>
                    <Button
                      size="small"
                      variant="outlined"
                      color="warning"
                      onClick={() =>
                        handleExcuseEntry(
                          detailDialog.entry,
                          detailDialog.notesDraft,
                        )
                      }
                      disabled={busy}
                    >
                      Mark excused
                    </Button>
                  </Stack>
                </Alert>
              )}
            </Stack>
          )}
        </DialogContent>
        <DialogActions sx={{ justifyContent: 'space-between', px: 3 }}>
          <Box>
            {detailDialog?.entry?.status === 'draft' && (
              <IconButton
                color="error"
                size="small"
                onClick={() => handleDeleteEntry(detailDialog.entry)}
                disabled={busy}
              >
                <DeleteOutlineIcon fontSize="small" />
              </IconButton>
            )}
          </Box>
          <Stack direction="row" spacing={1}>
            <Button onClick={() => setDetailDialog(null)}>Close</Button>
            {detailDialog?.entry?.status === 'draft' ? (
              <>
                <Button
                  onClick={handleSaveDraftEdit}
                  disabled={busy}
                >
                  Save changes
                </Button>
                <Button
                  variant="contained"
                  color="primary"
                  onClick={() => handlePublishEntry(detailDialog.entry)}
                  disabled={busy}
                >
                  Publish shift
                </Button>
              </>
            ) : (
              <Button
                variant="contained"
                onClick={() =>
                  handleSaveDetailNotes(
                    detailDialog.entry,
                    detailDialog.notesDraft,
                  )
                }
                disabled={busy}
              >
                Save notes
              </Button>
            )}
          </Stack>
        </DialogActions>
      </Dialog>

      <Dialog
        open={Boolean(generateDialog)}
        onClose={() => {
          if (!generateDialog?.running) setGenerateDialog(null)
        }}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Generate draft schedule</DialogTitle>
        <DialogContent>
          {generateDialog?.loading || !generateDialog?.form ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
              <CircularProgress size={28} />
            </Box>
          ) : (
            <Stack spacing={2.5} sx={{ mt: 0.5 }}>
              <DialogContentText>
                Creates DRAFT shifts for the selected week so you can
                review and edit before publishing. Nothing is published
                automatically; existing entries are left untouched.
              </DialogContentText>
              <Box>
                <Typography variant="overline" color="text.secondary">
                  Selected week
                </Typography>
                <Typography variant="body2">
                  {`Week of ${formatDayHeader(weekStart)} (${generateDialog.form.open_days.join(', ')})`}
                </Typography>
              </Box>

              <Box>
                <Typography
                  variant="subtitle2"
                  sx={{ mb: 1, color: 'text.primary' }}
                >
                  Shift settings
                </Typography>
                <Stack
                  direction={{ xs: 'column', sm: 'row' }}
                  spacing={1.5}
                >
                  <TextField
                    select
                    label="No-booking shift start"
                    size="small"
                    fullWidth
                    value={generateDialog.form.no_appointment_shift_start}
                    onChange={(e) =>
                      updateGenerateForm({
                        no_appointment_shift_start: e.target.value,
                      })
                    }
                    disabled={generateDialog.running}
                  >
                    {SHIFT_TIME_OPTIONS.map((opt) => (
                      <MenuItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </MenuItem>
                    ))}
                  </TextField>
                  <TextField
                    select
                    label="No-booking shift end"
                    size="small"
                    fullWidth
                    value={generateDialog.form.no_appointment_shift_end}
                    onChange={(e) =>
                      updateGenerateForm({
                        no_appointment_shift_end: e.target.value,
                      })
                    }
                    disabled={generateDialog.running}
                  >
                    {SHIFT_TIME_OPTIONS.map((opt) => (
                      <MenuItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </MenuItem>
                    ))}
                  </TextField>
                </Stack>
                <TextField
                  select
                  label="Appointment buffer"
                  size="small"
                  fullWidth
                  value={String(generateDialog.form.appointment_buffer_minutes)}
                  onChange={(e) =>
                    updateGenerateForm({
                      appointment_buffer_minutes: Number(e.target.value),
                    })
                  }
                  disabled={generateDialog.running}
                  helperText="How long before the first appointment a stylist starts."
                  sx={{ mt: 1.5 }}
                >
                  {APPOINTMENT_BUFFER_OPTIONS.map((mins) => (
                    <MenuItem key={mins} value={String(mins)}>
                      {`${mins} minutes`}
                    </MenuItem>
                  ))}
                </TextField>
              </Box>

              <Box>
                <Typography
                  variant="subtitle2"
                  sx={{ mb: 1, color: 'text.primary' }}
                >
                  Staffing rules
                </Typography>
                <Stack
                  direction={{ xs: 'column', sm: 'row' }}
                  spacing={1.5}
                >
                  <TextField
                    label="Min stylists on appointment days"
                    type="number"
                    size="small"
                    fullWidth
                    value={generateDialog.form.min_stylists_when_appointments}
                    onChange={(e) =>
                      updateGenerateForm({
                        min_stylists_when_appointments: e.target.value,
                      })
                    }
                    inputProps={{ min: 1, step: 1 }}
                    disabled={generateDialog.running}
                  />
                  <TextField
                    label="Min stylists on quiet days"
                    type="number"
                    size="small"
                    fullWidth
                    value={generateDialog.form.min_stylists_when_quiet}
                    onChange={(e) =>
                      updateGenerateForm({
                        min_stylists_when_quiet: e.target.value,
                      })
                    }
                    inputProps={{ min: 1, step: 1 }}
                    disabled={generateDialog.running}
                  />
                </Stack>
                <FormControlLabel
                  sx={{ mt: 1 }}
                  control={
                    <Switch
                      checked={generateDialog.form.rotate_fairly}
                      onChange={(e) =>
                        updateGenerateForm({
                          rotate_fairly: e.target.checked,
                        })
                      }
                      disabled={generateDialog.running}
                    />
                  }
                  label="Fair rotation across the week"
                />
              </Box>

              {generateDialog.formError && (
                <Alert severity="error">{generateDialog.formError}</Alert>
              )}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => setGenerateDialog(null)}
            disabled={generateDialog?.running}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleGenerateDraftWeek}
            disabled={
              !generateDialog ||
              generateDialog.loading ||
              generateDialog.running
            }
          >
            {generateDialog?.running ? 'Generating…' : 'Generate drafts'}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
