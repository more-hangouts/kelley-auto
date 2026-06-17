import { useEffect, useState } from 'react'
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
  DialogContentText,
  DialogTitle,
  FormControlLabel,
  IconButton,
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
import ArchiveIcon from '@mui/icons-material/Archive'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import RestoreIcon from '@mui/icons-material/Restore'

import {
  archiveSchedulePreset,
  createSchedulePreset,
  listSchedulePresets,
  patchSchedulePreset,
} from '../services/api'

// Admin-configurable schedule shift presets (Phase 10 Slice 3).
// Mounted at /settings/staff/schedule/presets. The manager grid's
// "Preset" dropdown reads the same list, so editing here feeds the
// grid immediately on its next refresh.

const DEFAULT_FORM = {
  label: '',
  start_time: '09:00',
  end_time: '17:00',
  late_grace_minutes: 30,
  sort_order: 100,
}

function fmtTime(value) {
  if (!value) return ''
  // Server returns "HH:MM"; render as 12h for readability.
  const [h, m] = value.split(':').map(Number)
  const d = new Date()
  d.setHours(h, m, 0, 0)
  return d.toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  })
}

function errorCodeToMessage(code) {
  switch (code) {
    case 'label_required':
      return 'A label is required.'
    case 'label_too_long':
      return 'Label is too long (max 80 characters).'
    case 'invalid_time_range':
      return 'End time must be after start time.'
    case 'late_grace_out_of_range':
      return 'Late grace must be between 0 and 120 minutes.'
    case 'sort_order_negative':
      return 'Sort order cannot be negative.'
    case 'duplicate_label':
      return 'Another active preset already uses that label.'
    case 'preset_not_found':
      return 'That preset no longer exists.'
    case 'nothing_to_update':
      return 'No changes to save.'
    default:
      return null
  }
}

export default function AdminSchedulePresets() {
  const [presets, setPresets] = useState(null)
  const [includeArchived, setIncludeArchived] = useState(false)
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [busyId, setBusyId] = useState(null)

  const [dialog, setDialog] = useState(null) // { mode: 'create'|'edit', form, preset? }

  async function refresh() {
    setLoadError(null)
    try {
      const body = await listSchedulePresets({
        includeArchived,
      })
      setPresets(body.presets || [])
    } catch {
      setLoadError("Couldn't load presets.")
      setPresets([])
    }
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeArchived])

  function openCreate() {
    setActionError(null)
    setDialog({ mode: 'create', form: { ...DEFAULT_FORM } })
  }

  function openEdit(preset) {
    setActionError(null)
    setDialog({
      mode: 'edit',
      preset,
      form: {
        label: preset.label,
        start_time: preset.start_time,
        end_time: preset.end_time,
        late_grace_minutes: preset.late_grace_minutes,
        sort_order: preset.sort_order,
      },
    })
  }

  function setFormField(key, value) {
    setDialog((d) => (d ? { ...d, form: { ...d.form, [key]: value } } : d))
  }

  function handleApiError(err) {
    const code = err?.response?.data?.detail?.code
    setActionError(errorCodeToMessage(code) || "Couldn't save the preset.")
  }

  async function handleSave() {
    if (!dialog) return
    const form = dialog.form
    if (!form.label?.trim()) {
      setActionError('A label is required.')
      return
    }
    const body = {
      label: form.label.trim(),
      start_time: form.start_time,
      end_time: form.end_time,
      late_grace_minutes: Number(form.late_grace_minutes),
      sort_order: Number(form.sort_order),
    }
    setBusyId('save')
    setActionError(null)
    try {
      if (dialog.mode === 'create') {
        await createSchedulePreset(body)
      } else {
        await patchSchedulePreset(dialog.preset.id, body)
      }
      setDialog(null)
      await refresh()
    } catch (err) {
      handleApiError(err)
    } finally {
      setBusyId(null)
    }
  }

  async function handleArchive(preset) {
    if (
      !window.confirm(
        `Archive "${preset.label}"? It'll disappear from the grid's preset dropdown but stay in the audit history.`,
      )
    ) {
      return
    }
    setBusyId(preset.id)
    setActionError(null)
    try {
      await archiveSchedulePreset(preset.id)
      await refresh()
    } catch (err) {
      handleApiError(err)
    } finally {
      setBusyId(null)
    }
  }

  async function handleRestore(preset) {
    setBusyId(preset.id)
    setActionError(null)
    try {
      await patchSchedulePreset(preset.id, { active: true })
      await refresh()
    } catch (err) {
      handleApiError(err)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Stack spacing={2}>
      <Box>
        <Typography variant="h5">Shift presets</Typography>
        <Typography variant="body2" color="text.secondary">
          Presets populate the "Preset" dropdown on the manager
          schedule grid. Each preset is a time-of-day pair plus a
          default grace period; the grid combines the preset with the
          cell's date to build a concrete shift.
        </Typography>
      </Box>

      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        justifyContent="space-between"
        alignItems={{ xs: 'flex-start', sm: 'center' }}
        spacing={1}
      >
        <FormControlLabel
          control={
            <Switch
              checked={includeArchived}
              onChange={(e) => setIncludeArchived(e.target.checked)}
              size="small"
            />
          }
          label="Show archived"
        />
        <Button variant="contained" onClick={openCreate}>
          Add preset
        </Button>
      </Stack>

      {loadError && <Alert severity="error">{loadError}</Alert>}
      {actionError && (
        <Alert severity="error" onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}

      <Card variant="outlined">
        <CardContent sx={{ p: { xs: 1.5, sm: 2 } }}>
          {presets === null ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={28} />
            </Box>
          ) : presets.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No presets {includeArchived ? '' : '(active)'} yet. Add one
              to get started.
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell sx={{ width: 60 }}>Order</TableCell>
                  <TableCell>Label</TableCell>
                  <TableCell>Hours</TableCell>
                  <TableCell align="right">Late grace</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {presets.map((p) => (
                  <TableRow
                    key={p.id}
                    sx={{
                      opacity: p.active ? 1 : 0.55,
                    }}
                  >
                    <TableCell>{p.sort_order}</TableCell>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {p.label}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      {fmtTime(p.start_time)} – {fmtTime(p.end_time)}
                    </TableCell>
                    <TableCell align="right">
                      {p.late_grace_minutes}m
                    </TableCell>
                    <TableCell>
                      {p.active ? (
                        <Chip
                          label="Active"
                          size="small"
                          color="success"
                          variant="outlined"
                        />
                      ) : (
                        <Chip
                          label="Archived"
                          size="small"
                          variant="outlined"
                        />
                      )}
                    </TableCell>
                    <TableCell align="right">
                      <Stack
                        direction="row"
                        spacing={0.5}
                        justifyContent="flex-end"
                      >
                        <Tooltip title="Edit" arrow>
                          <span>
                            <IconButton
                              size="small"
                              onClick={() => openEdit(p)}
                              disabled={busyId === p.id}
                            >
                              <EditOutlinedIcon fontSize="small" />
                            </IconButton>
                          </span>
                        </Tooltip>
                        {p.active ? (
                          <Tooltip title="Archive" arrow>
                            <span>
                              <IconButton
                                size="small"
                                color="error"
                                onClick={() => handleArchive(p)}
                                disabled={busyId === p.id}
                              >
                                <ArchiveIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                        ) : (
                          <Tooltip title="Restore" arrow>
                            <span>
                              <IconButton
                                size="small"
                                onClick={() => handleRestore(p)}
                                disabled={busyId === p.id}
                              >
                                <RestoreIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                        )}
                      </Stack>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {dialog?.mode === 'create' ? 'Add preset' : 'Edit preset'}
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Picking this preset on the grid fills the start, end, and
            late-grace fields. Times use the boutique's local clock.
          </DialogContentText>
          <Stack spacing={2}>
            <TextField
              label="Label"
              value={dialog?.form?.label ?? ''}
              onChange={(e) => setFormField('label', e.target.value)}
              required
              fullWidth
              inputProps={{ maxLength: 80 }}
              helperText="Shown in the grid's preset dropdown (e.g. 'Opening (9am – 5pm)')."
            />
            <Stack direction="row" spacing={1}>
              <TextField
                label="Start time"
                type="time"
                value={dialog?.form?.start_time ?? '09:00'}
                onChange={(e) => setFormField('start_time', e.target.value)}
                InputLabelProps={{ shrink: true }}
                fullWidth
              />
              <TextField
                label="End time"
                type="time"
                value={dialog?.form?.end_time ?? '17:00'}
                onChange={(e) => setFormField('end_time', e.target.value)}
                InputLabelProps={{ shrink: true }}
                fullWidth
              />
            </Stack>
            <Stack direction="row" spacing={1}>
              <TextField
                label="Late grace (min)"
                type="number"
                value={dialog?.form?.late_grace_minutes ?? 30}
                onChange={(e) =>
                  setFormField('late_grace_minutes', e.target.value)
                }
                inputProps={{ min: 0, max: 120 }}
                fullWidth
                helperText="No-show flag fires this many minutes past start without a clock-in."
              />
              <TextField
                label="Sort order"
                type="number"
                value={dialog?.form?.sort_order ?? 100}
                onChange={(e) => setFormField('sort_order', e.target.value)}
                inputProps={{ min: 0 }}
                fullWidth
                helperText="Lower numbers appear first in the dropdown."
              />
            </Stack>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(null)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleSave}
            disabled={busyId === 'save'}
          >
            {dialog?.mode === 'create' ? 'Add preset' : 'Save changes'}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
