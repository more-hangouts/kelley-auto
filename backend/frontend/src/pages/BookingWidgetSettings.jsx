import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  MenuItem,
  Stack,
  Switch,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'

import {
  createAvailabilityRule,
  createBlackout,
  deleteAvailabilityRule,
  deleteBlackout,
  getWidgetSettings,
  listAvailabilityRules,
  listBlackouts,
  updateAvailabilityRule,
  updateWidgetSettings,
} from '../services/api'
import SettingsPageHeader from '../components/SettingsPageHeader'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const EMBED_SNIPPET = `<div id="bellas-booking-widget"></div>
<script src="https://api.shopbellasxv.com/widgets/bellas-booking-widget.js" defer></script>
<script>
  document.addEventListener('DOMContentLoaded', function () {
    window.BellasBookingWidget.init({
      containerId: 'bellas-booking-widget',
      apiBaseUrl: 'https://api.shopbellasxv.com'
    });
  });
</script>`

export default function BookingWidgetSettings() {
  const [tab, setTab] = useState('theme')

  return (
    <Stack spacing={2}>
      <SettingsPageHeader
        crumbs={[
          { label: 'Settings', to: '/settings' },
          { label: 'Widget settings' },
        ]}
      />
      <Typography variant="h4">Widget settings</Typography>
      <Card>
        <Tabs
          value={tab}
          onChange={(_, v) => setTab(v)}
          variant="scrollable"
          allowScrollButtonsMobile
          sx={{ borderBottom: 1, borderColor: 'divider' }}
        >
          <Tab value="theme" label="Theme & copy" />
          <Tab value="flow" label="Flow" />
          <Tab value="availability" label="Availability" />
          <Tab value="blackouts" label="Blackouts" />
          <Tab value="embed" label="Embed code" />
        </Tabs>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          {tab === 'theme' && <ThemeAndCopy />}
          {tab === 'flow' && <FlowSettings />}
          {tab === 'availability' && <AvailabilityRules />}
          {tab === 'blackouts' && <Blackouts />}
          {tab === 'embed' && <EmbedCode />}
        </CardContent>
      </Card>
    </Stack>
  )
}

// ---------------------------------------------------------------------------
// Theme + copy editor
// ---------------------------------------------------------------------------

function useSettings() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const reload = useCallback(() => {
    setLoading(true)
    setError(null)
    return getWidgetSettings()
      .then(setData)
      .catch((err) => setError(err?.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  return { data, setData, loading, error, reload }
}

function ThemeAndCopy() {
  const { data, setData, loading, error, reload } = useSettings()
  const [savingMsg, setSavingMsg] = useState(null)
  const [saveError, setSaveError] = useState(null)
  const [saving, setSaving] = useState(false)

  if (loading || !data) {
    return error ? <Alert severity="error">{error}</Alert> : <CircularProgress size={24} />
  }

  function setTheme(key, value) {
    setData({ ...data, theme: { ...data.theme, [key]: value } })
  }

  function setCopy(key, value) {
    setData({ ...data, copy_text: { ...data.copy_text, [key]: value } })
  }

  async function save(payload) {
    setSaving(true)
    setSavingMsg(null)
    setSaveError(null)
    try {
      const updated = await updateWidgetSettings(payload)
      setData(updated)
      setSavingMsg('Saved.')
    } catch (err) {
      setSaveError(err?.response?.data?.detail || err.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="overline" color="text.secondary" display="block" mb={1}>
          Theme
        </Typography>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} flexWrap="wrap">
          <ColorField label="Background" value={data.theme.color_bg} onChange={(v) => setTheme('color_bg', v)} />
          <ColorField label="Surface" value={data.theme.color_surface} onChange={(v) => setTheme('color_surface', v)} />
          <ColorField label="Accent" value={data.theme.color_accent} onChange={(v) => setTheme('color_accent', v)} />
          <ColorField label="Accent dark" value={data.theme.color_accent_dark} onChange={(v) => setTheme('color_accent_dark', v)} />
          <ColorField label="Text" value={data.theme.color_text} onChange={(v) => setTheme('color_text', v)} />
          <ColorField label="Muted text" value={data.theme.color_text_muted} onChange={(v) => setTheme('color_text_muted', v)} />
        </Stack>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} mt={2}>
          <TextField
            size="small"
            label="Heading font stack"
            value={data.theme.font_heading || ''}
            onChange={(e) => setTheme('font_heading', e.target.value)}
            sx={{ flexGrow: 1 }}
          />
          <TextField
            size="small"
            label="Body font stack"
            value={data.theme.font_body || ''}
            onChange={(e) => setTheme('font_body', e.target.value)}
            sx={{ flexGrow: 1 }}
          />
          <TextField
            size="small"
            label="Corner radius"
            value={data.theme.radius || ''}
            onChange={(e) => setTheme('radius', e.target.value)}
            sx={{ width: 140 }}
          />
        </Stack>
        <Box mt={2}>
          <Button
            variant="contained"
            onClick={() => save({ theme: data.theme })}
            disabled={saving}
          >
            Save theme
          </Button>
        </Box>
      </Box>

      <Box>
        <Typography variant="overline" color="text.secondary" display="block" mb={1}>
          Copy
        </Typography>
        <Stack spacing={1.5}>
          <CopyRow label="Header brand" value={data.copy_text.header_brand} onChange={(v) => setCopy('header_brand', v)} />
          <CopyRow label="Header title" value={data.copy_text.header_title} onChange={(v) => setCopy('header_title', v)} />
          <CopyRow label="Header subtitle" value={data.copy_text.header_subtitle} onChange={(v) => setCopy('header_subtitle', v)} multiline />
          <CopyRow label="Step 2 heading" value={data.copy_text.step2_heading} onChange={(v) => setCopy('step2_heading', v)} />
          <CopyRow label="Step 3 heading" value={data.copy_text.step3_heading} onChange={(v) => setCopy('step3_heading', v)} />
          <CopyRow label="Submit button" value={data.copy_text.submit_label} onChange={(v) => setCopy('submit_label', v)} />
          <CopyRow label="Success heading" value={data.copy_text.success_heading} onChange={(v) => setCopy('success_heading', v)} />
          <CopyRow label="Success subtitle" value={data.copy_text.success_subtitle} onChange={(v) => setCopy('success_subtitle', v)} multiline />
          <CopyRow label="Boutique label" value={data.copy_text.boutique_label} onChange={(v) => setCopy('boutique_label', v)} />
          <CopyRow label="Timezone label" value={data.copy_text.timezone_label} onChange={(v) => setCopy('timezone_label', v)} />
        </Stack>
        <Box mt={2}>
          <Button
            variant="contained"
            onClick={() => save({ copy_text: data.copy_text })}
            disabled={saving}
          >
            Save copy
          </Button>
        </Box>
      </Box>

      {savingMsg && <Alert severity="success" onClose={() => setSavingMsg(null)}>{savingMsg}</Alert>}
      {saveError && <Alert severity="error" onClose={() => setSaveError(null)}>{saveError}</Alert>}
      <Button size="small" onClick={reload} disabled={saving}>
        Discard local changes
      </Button>
    </Stack>
  )
}

function ColorField({ label, value, onChange }) {
  return (
    <TextField
      size="small"
      label={label}
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
      sx={{ width: 160 }}
      InputProps={{
        startAdornment: (
          <Box
            sx={{
              width: 18,
              height: 18,
              borderRadius: '50%',
              border: '1px solid rgba(0,0,0,0.1)',
              mr: 1,
              bgcolor: value || 'transparent',
            }}
          />
        ),
      }}
    />
  )
}

function CopyRow({ label, value, onChange, multiline }) {
  return (
    <TextField
      size="small"
      label={label}
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
      multiline={!!multiline}
      minRows={multiline ? 2 : 1}
      fullWidth
    />
  )
}

// ---------------------------------------------------------------------------
// Flow editor
// ---------------------------------------------------------------------------

function FlowSettings() {
  const { data, setData, loading, error } = useSettings()
  const [saving, setSaving] = useState(false)
  const [savedMsg, setSavedMsg] = useState(null)
  const [saveError, setSaveError] = useState(null)

  if (loading || !data) {
    return error ? <Alert severity="error">{error}</Alert> : <CircularProgress size={24} />
  }

  const flow = data.flow || {}
  const durations = (flow.duration_options_minutes || []).join(', ')

  function setFlow(patch) {
    setData({ ...data, flow: { ...flow, ...patch } })
  }

  async function save() {
    setSaving(true)
    setSavedMsg(null)
    setSaveError(null)
    try {
      const updated = await updateWidgetSettings({ flow: data.flow })
      setData(updated)
      setSavedMsg('Saved.')
    } catch (err) {
      setSaveError(err?.response?.data?.detail || err.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Stack spacing={2} sx={{ maxWidth: 480 }}>
      <TextField
        size="small"
        label="Allowed durations (minutes, comma-separated)"
        value={durations}
        onChange={(e) =>
          setFlow({
            duration_options_minutes: e.target.value
              .split(',')
              .map((s) => parseInt(s.trim(), 10))
              .filter((n) => !Number.isNaN(n) && n > 0),
          })
        }
        helperText="The customer widget greys out durations not supported by any availability rule."
      />
      <TextField
        size="small"
        label="Default duration (minutes)"
        type="number"
        value={flow.default_duration_minutes || ''}
        onChange={(e) =>
          setFlow({
            default_duration_minutes: parseInt(e.target.value, 10) || null,
          })
        }
        sx={{ width: 220 }}
      />
      <TextField
        size="small"
        label="Max days ahead"
        type="number"
        value={flow.max_days_ahead || ''}
        onChange={(e) =>
          setFlow({ max_days_ahead: parseInt(e.target.value, 10) || null })
        }
        sx={{ width: 220 }}
      />
      <TextField
        size="small"
        label="Min lead time (minutes)"
        type="number"
        value={flow.min_lead_time_minutes ?? ''}
        onChange={(e) =>
          setFlow({
            min_lead_time_minutes: parseInt(e.target.value, 10) || 0,
          })
        }
        helperText="Customers can't book a slot starting sooner than this many minutes from now."
        sx={{ width: 220 }}
      />
      <Box>
        <Button variant="contained" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save flow'}
        </Button>
      </Box>
      {savedMsg && <Alert severity="success" onClose={() => setSavedMsg(null)}>{savedMsg}</Alert>}
      {saveError && <Alert severity="error" onClose={() => setSaveError(null)}>{saveError}</Alert>}
    </Stack>
  )
}

// ---------------------------------------------------------------------------
// Availability rules
// ---------------------------------------------------------------------------

const EMPTY_RULE = {
  weekday: 2,
  start_time: '12:00',
  end_time: '17:00',
  slot_duration_minutes: 45,
  capacity: 1,
  active: true,
  label: '',
}

function AvailabilityRules() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [editing, setEditing] = useState(null) // rule object or null
  const [showCreate, setShowCreate] = useState(false)

  const reload = useCallback(() => {
    setLoading(true)
    setError(null)
    return listAvailabilityRules()
      .then(setRows)
      .catch((err) => setError(err?.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  async function onDelete(id) {
    if (!window.confirm('Delete this availability rule?')) return
    try {
      await deleteAvailabilityRule(id)
      await reload()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message)
    }
  }

  return (
    <Stack spacing={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="body2" color="text.secondary">
          Recurring weekly hours. Slots come from these rules minus blackouts and existing bookings.
        </Typography>
        <Button startIcon={<AddIcon />} variant="contained" onClick={() => setShowCreate(true)}>
          Add rule
        </Button>
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      {loading ? (
        <Box sx={{ py: 4, textAlign: 'center' }}>
          <CircularProgress size={24} />
        </Box>
      ) : (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Day</TableCell>
              <TableCell>Hours</TableCell>
              <TableCell>Slot</TableCell>
              <TableCell>Capacity</TableCell>
              <TableCell>Label</TableCell>
              <TableCell>Active</TableCell>
              <TableCell align="right" />
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.id}>
                <TableCell>{WEEKDAYS[r.weekday]}</TableCell>
                <TableCell>{shortTime(r.start_time)} – {shortTime(r.end_time)}</TableCell>
                <TableCell>{r.slot_duration_minutes} min</TableCell>
                <TableCell>{r.capacity}</TableCell>
                <TableCell>{r.label || '—'}</TableCell>
                <TableCell>
                  <Chip
                    size="small"
                    label={r.active ? 'on' : 'off'}
                    color={r.active ? 'success' : 'default'}
                    variant={r.active ? 'filled' : 'outlined'}
                  />
                </TableCell>
                <TableCell align="right">
                  <Tooltip title="Edit">
                    <IconButton size="small" onClick={() => setEditing(r)}>
                      <EditOutlinedIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Delete">
                    <IconButton size="small" onClick={() => onDelete(r.id)}>
                      <DeleteOutlineIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <RuleDialog
        open={showCreate}
        initial={EMPTY_RULE}
        title="Add availability rule"
        onClose={() => setShowCreate(false)}
        onSave={async (payload) => {
          await createAvailabilityRule(payload)
          setShowCreate(false)
          await reload()
        }}
      />
      <RuleDialog
        open={!!editing}
        initial={editing}
        title="Edit availability rule"
        onClose={() => setEditing(null)}
        onSave={async (payload) => {
          await updateAvailabilityRule(editing.id, payload)
          setEditing(null)
          await reload()
        }}
      />
    </Stack>
  )
}

function shortTime(t) {
  // Backend serializes as 'HH:MM:SS'; trim seconds for display.
  return (t || '').slice(0, 5)
}

function RuleDialog({ open, initial, title, onClose, onSave }) {
  const [form, setForm] = useState(EMPTY_RULE)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (open && initial) {
      setForm({
        weekday: initial.weekday ?? 2,
        start_time: shortTime(initial.start_time) || '12:00',
        end_time: shortTime(initial.end_time) || '17:00',
        slot_duration_minutes: initial.slot_duration_minutes ?? 45,
        capacity: initial.capacity ?? 1,
        active: initial.active ?? true,
        label: initial.label || '',
      })
      setError(null)
    }
  }, [open, initial])

  async function submit() {
    setSaving(true)
    setError(null)
    try {
      await onSave({
        weekday: Number(form.weekday),
        start_time: form.start_time + ':00',
        end_time: form.end_time + ':00',
        slot_duration_minutes: Number(form.slot_duration_minutes),
        capacity: Number(form.capacity),
        active: form.active,
        label: form.label || null,
      })
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField
            select
            size="small"
            label="Day"
            value={form.weekday}
            onChange={(e) => setForm({ ...form, weekday: Number(e.target.value) })}
          >
            {WEEKDAYS.map((d, i) => (
              <MenuItem key={i} value={i}>
                {d}
              </MenuItem>
            ))}
          </TextField>
          <Stack direction="row" spacing={2}>
            <TextField
              size="small"
              type="time"
              label="Start"
              InputLabelProps={{ shrink: true }}
              value={form.start_time}
              onChange={(e) => setForm({ ...form, start_time: e.target.value })}
              sx={{ flexGrow: 1 }}
            />
            <TextField
              size="small"
              type="time"
              label="End"
              InputLabelProps={{ shrink: true }}
              value={form.end_time}
              onChange={(e) => setForm({ ...form, end_time: e.target.value })}
              sx={{ flexGrow: 1 }}
            />
          </Stack>
          <Stack direction="row" spacing={2}>
            <TextField
              size="small"
              type="number"
              label="Slot duration (min)"
              value={form.slot_duration_minutes}
              onChange={(e) =>
                setForm({ ...form, slot_duration_minutes: e.target.value })
              }
              inputProps={{ min: 5, max: 480 }}
              sx={{ flexGrow: 1 }}
            />
            <TextField
              size="small"
              type="number"
              label="Capacity"
              value={form.capacity}
              onChange={(e) => setForm({ ...form, capacity: e.target.value })}
              inputProps={{ min: 1, max: 20 }}
              sx={{ flexGrow: 1 }}
            />
          </Stack>
          <TextField
            size="small"
            label="Label (optional)"
            value={form.label}
            onChange={(e) => setForm({ ...form, label: e.target.value })}
          />
          <Stack direction="row" alignItems="center" spacing={1}>
            <Switch
              checked={!!form.active}
              onChange={(e) => setForm({ ...form, active: e.target.checked })}
            />
            <Typography variant="body2">Active</Typography>
          </Stack>
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Blackouts
// ---------------------------------------------------------------------------

function Blackouts() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showCreate, setShowCreate] = useState(false)

  const reload = useCallback(() => {
    setLoading(true)
    setError(null)
    return listBlackouts()
      .then(setRows)
      .catch((err) => setError(err?.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  async function onDelete(id) {
    if (!window.confirm('Delete this blackout? Slots inside this window will become bookable again.')) return
    try {
      await deleteBlackout(id)
      await reload()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message)
    }
  }

  return (
    <Stack spacing={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="body2" color="text.secondary">
          One-off closures (holidays, market trips, family days). Slots inside are removed from public availability.
        </Typography>
        <Button startIcon={<AddIcon />} variant="contained" onClick={() => setShowCreate(true)}>
          Add blackout
        </Button>
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      {loading ? (
        <Box sx={{ py: 4, textAlign: 'center' }}>
          <CircularProgress size={24} />
        </Box>
      ) : rows.length === 0 ? (
        <Typography variant="body2" color="text.secondary">No blackouts scheduled.</Typography>
      ) : (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Start</TableCell>
              <TableCell>End</TableCell>
              <TableCell>Reason</TableCell>
              <TableCell align="right" />
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((b) => (
              <TableRow key={b.id}>
                <TableCell>{new Date(b.start_at).toLocaleString()}</TableCell>
                <TableCell>{new Date(b.end_at).toLocaleString()}</TableCell>
                <TableCell>{b.reason || '—'}</TableCell>
                <TableCell align="right">
                  <Tooltip title="Delete">
                    <IconButton size="small" onClick={() => onDelete(b.id)}>
                      <DeleteOutlineIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <BlackoutDialog
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onSave={async (payload) => {
          await createBlackout(payload)
          setShowCreate(false)
          await reload()
        }}
      />
    </Stack>
  )
}

function BlackoutDialog({ open, onClose, onSave }) {
  const today = useMemo(() => new Date(), [])
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (open) {
      // Default to a full-day blackout starting tomorrow.
      const t = new Date(today)
      t.setDate(t.getDate() + 1)
      t.setHours(0, 0, 0, 0)
      const e = new Date(t)
      e.setHours(23, 59, 0, 0)
      setStart(toLocalIso(t))
      setEnd(toLocalIso(e))
      setReason('')
      setError(null)
    }
  }, [open, today])

  async function submit() {
    setSaving(true)
    setError(null)
    try {
      await onSave({
        start_at: new Date(start).toISOString(),
        end_at: new Date(end).toISOString(),
        reason: reason || null,
      })
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add blackout</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField
            size="small"
            type="datetime-local"
            label="Start"
            InputLabelProps={{ shrink: true }}
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
          <TextField
            size="small"
            type="datetime-local"
            label="End"
            InputLabelProps={{ shrink: true }}
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
          <TextField
            size="small"
            label="Reason (internal, optional)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function toLocalIso(date) {
  // Returns 'YYYY-MM-DDTHH:MM' for <input type="datetime-local">.
  const pad = (n) => String(n).padStart(2, '0')
  return (
    date.getFullYear() +
    '-' +
    pad(date.getMonth() + 1) +
    '-' +
    pad(date.getDate()) +
    'T' +
    pad(date.getHours()) +
    ':' +
    pad(date.getMinutes())
  )
}

// ---------------------------------------------------------------------------
// Embed code
// ---------------------------------------------------------------------------

function EmbedCode() {
  const [copied, setCopied] = useState(false)

  function copy() {
    navigator.clipboard.writeText(EMBED_SNIPPET).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <Stack spacing={2}>
      <Typography variant="body2" color="text.secondary">
        Paste this into the marketing site or any partner page where you want
        the booking widget to appear.
      </Typography>
      <Box
        sx={{
          position: 'relative',
          bgcolor: 'rgba(0,0,0,0.04)',
          borderRadius: 2,
          p: 2,
          fontFamily: 'monospace',
          fontSize: 13,
          whiteSpace: 'pre-wrap',
          overflow: 'auto',
        }}
      >
        {EMBED_SNIPPET}
        <Tooltip title={copied ? 'Copied' : 'Copy'}>
          <IconButton
            size="small"
            sx={{ position: 'absolute', top: 8, right: 8 }}
            onClick={copy}
          >
            <ContentCopyIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>
      <Typography variant="caption" color="text.secondary">
        The widget asset is canonical at <code>https://api.shopbellasxv.com/widgets/bellas-booking-widget.js</code>.
        It picks up theme + copy + flow changes you save above without redeploying the widget JS.
      </Typography>
    </Stack>
  )
}
