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
  MenuItem,
  Select,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'

import {
  createAdminHoliday,
  deleteAdminHoliday,
  listAdminHolidays,
  listAdminStaffLocations,
  patchAdminHoliday,
} from '../services/api'

// Owner holidays admin (Phase 8 Slice D), mounted at
// /settings/holidays. Tabular CRUD over `staff_holidays`.
//
// The schema uses UNIQUE NULLS NOT DISTINCT (holiday_date,
// location_id, name), so two "global" (location_id IS NULL) entries
// with the same date+name actually collide. The service translates
// that to a stable `holiday_already_exists` 409 — we surface it as a
// specific error message rather than a generic "Couldn't save."

const DEFAULT_FORM = {
  name: '',
  holiday_date: '',
  location_id: '',
  is_paid: false,
  multiplier: '',
  notes: '',
}

function formatDate(iso) {
  if (!iso) return ''
  try {
    const [y, m, d] = iso.split('-').map(Number)
    return new Date(y, m - 1, d).toLocaleDateString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
  } catch {
    return iso
  }
}

export default function AdminHolidays() {
  const [holidays, setHolidays] = useState(null)
  const [locations, setLocations] = useState([])
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingHoliday, setEditingHoliday] = useState(null)
  const [form, setForm] = useState(DEFAULT_FORM)

  async function refresh() {
    setLoadError(null)
    try {
      const data = await listAdminHolidays()
      setHolidays(data.holidays || [])
    } catch {
      setLoadError("Couldn't load holidays.")
      setHolidays([])
    }
  }

  async function loadLocations() {
    // Best-effort: if the staff-locations route isn't reachable yet,
    // fall back to "global only" behavior.
    try {
      const data = await listAdminStaffLocations()
      setLocations(Array.isArray(data) ? data : [])
    } catch {
      setLocations([])
    }
  }

  useEffect(() => {
    refresh()
    loadLocations()
  }, [])

  function openCreateDialog() {
    setEditingHoliday(null)
    setForm(DEFAULT_FORM)
    setDialogOpen(true)
  }

  function openEditDialog(holiday) {
    setEditingHoliday(holiday)
    setForm({
      name: holiday.name || '',
      holiday_date: holiday.holiday_date || '',
      location_id: holiday.location_id == null ? '' : String(holiday.location_id),
      is_paid: Boolean(holiday.is_paid),
      multiplier:
        holiday.multiplier == null ? '' : String(holiday.multiplier),
      notes: holiday.notes || '',
    })
    setDialogOpen(true)
  }

  function closeDialog() {
    setDialogOpen(false)
    setEditingHoliday(null)
    setForm(DEFAULT_FORM)
  }

  async function handleSave() {
    if (!form.name.trim() || !form.holiday_date) {
      setActionError('Name and date are required.')
      return
    }
    setActionError(null)
    setBusyId(editingHoliday ? editingHoliday.id : 'create')
    try {
      const body = {
        name: form.name.trim(),
        holiday_date: form.holiday_date,
        location_id: form.location_id ? Number(form.location_id) : null,
        is_paid: form.is_paid,
        multiplier: form.multiplier ? Number(form.multiplier) : null,
        notes: form.notes.trim() || null,
      }
      if (editingHoliday) {
        await patchAdminHoliday(editingHoliday.id, body)
      } else {
        await createAdminHoliday(body)
      }
      closeDialog()
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'holiday_already_exists') {
        setActionError(
          'A holiday with that date, location, and name already exists.',
        )
      } else if (code === 'location_not_found') {
        setActionError('That location is no longer available.')
      } else {
        setActionError("Couldn't save the holiday.")
      }
    } finally {
      setBusyId(null)
    }
  }

  async function handleDelete(holiday) {
    if (
      !window.confirm(
        `Delete "${holiday.name}" on ${holiday.holiday_date}?`,
      )
    ) {
      return
    }
    setActionError(null)
    setBusyId(holiday.id)
    try {
      await deleteAdminHoliday(holiday.id)
      await refresh()
    } catch {
      setActionError("Couldn't delete the holiday.")
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Stack spacing={3}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        justifyContent="space-between"
        alignItems={{ xs: 'flex-start', sm: 'center' }}
        spacing={1}
      >
        <Box>
          <Typography variant="h4">Holidays</Typography>
          <Typography variant="body2" color="text.secondary">
            Advisory holiday calendar. Holidays tag punches for
            reporting but never block clock-in or clock-out. Leave the
            location blank for a global holiday that applies everywhere.
          </Typography>
        </Box>
        <Button
          variant="contained"
          onClick={openCreateDialog}
        >
          Add holiday
        </Button>
      </Stack>

      {loadError && <Alert severity="error">{loadError}</Alert>}
      {actionError && (
        <Alert severity="error" onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}

      <Card>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          {holidays === null ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={28} />
            </Box>
          ) : holidays.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No holidays configured.
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Date</TableCell>
                  <TableCell>Name</TableCell>
                  <TableCell>Location</TableCell>
                  <TableCell>Paid</TableCell>
                  <TableCell>Multiplier</TableCell>
                  <TableCell>Notes</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {holidays.map((h) => {
                  const loc = locations.find((l) => l.id === h.location_id)
                  return (
                    <TableRow key={h.id}>
                      <TableCell>{formatDate(h.holiday_date)}</TableCell>
                      <TableCell>{h.name}</TableCell>
                      <TableCell>
                        {h.location_id == null ? (
                          <Chip
                            label="Global"
                            size="small"
                            variant="outlined"
                          />
                        ) : (
                          loc?.name || `#${h.location_id}`
                        )}
                      </TableCell>
                      <TableCell>{h.is_paid ? 'Yes' : 'No'}</TableCell>
                      <TableCell>
                        {h.multiplier != null ? `${h.multiplier}x` : ''}
                      </TableCell>
                      <TableCell>{h.notes || ''}</TableCell>
                      <TableCell align="right">
                        <Button
                          size="small"
                          disabled={busyId === h.id}
                          onClick={() => openEditDialog(h)}
                          sx={{ mr: 1 }}
                        >
                          Edit
                        </Button>
                        <IconButton
                          size="small"
                          color="error"
                          disabled={busyId === h.id}
                          onClick={() => handleDelete(h)}
                        >
                          <DeleteOutlineIcon fontSize="small" />
                        </IconButton>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={dialogOpen}
        onClose={closeDialog}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {editingHoliday ? 'Edit holiday' : 'Add holiday'}
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Holidays are advisory. They tag punches that fall on the
            day for later reporting, but they never block a stylist
            from clocking in or out.
          </DialogContentText>
          <Stack spacing={2}>
            <TextField
              label="Name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
              fullWidth
            />
            <TextField
              label="Date"
              type="date"
              value={form.holiday_date}
              onChange={(e) =>
                setForm({ ...form, holiday_date: e.target.value })
              }
              InputLabelProps={{ shrink: true }}
              required
              fullWidth
            />
            <Select
              value={form.location_id}
              onChange={(e) =>
                setForm({ ...form, location_id: e.target.value })
              }
              displayEmpty
              fullWidth
              size="small"
            >
              <MenuItem value="">Global (all locations)</MenuItem>
              {locations.map((l) => (
                <MenuItem key={l.id} value={l.id}>
                  {l.name}
                </MenuItem>
              ))}
            </Select>
            <FormControlLabel
              control={
                <Switch
                  checked={form.is_paid}
                  onChange={(e) =>
                    setForm({ ...form, is_paid: e.target.checked })
                  }
                />
              }
              label="Paid holiday"
            />
            <TextField
              label="Pay multiplier (optional)"
              type="number"
              value={form.multiplier}
              onChange={(e) =>
                setForm({ ...form, multiplier: e.target.value })
              }
              inputProps={{ min: 0.1, step: 0.1 }}
              helperText="e.g. 1.5 for time-and-a-half. Used by reporting only."
              fullWidth
            />
            <TextField
              label="Notes (optional)"
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              multiline
              minRows={2}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={closeDialog}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleSave}
            disabled={busyId === (editingHoliday ? editingHoliday.id : 'create')}
          >
            Save holiday
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
