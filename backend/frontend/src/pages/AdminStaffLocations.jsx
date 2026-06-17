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
  Typography,
} from '@mui/material'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import MyLocationIcon from '@mui/icons-material/MyLocation'
import GpsFixedIcon from '@mui/icons-material/GpsFixed'

import {
  createAdminStaffLocation,
  deleteAdminStaffLocation,
  listAdminStaffLocations,
  patchAdminStaffLocation,
  testStaffLocationGeofence,
} from '../services/api'

// Owner staff-locations admin (Phase 9 sub-slice 1, Priority 1).
// Mounted at /settings/staff-locations. Table CRUD over the existing
// /api/admin/staff-locations router plus a "test GPS" probe that hits
// POST /{id}/test-geofence so the owner can validate the radius
// without forcing a real clock-in.
//
// Soft-delete only. Historical punches FK back to the row, so we
// never offer hard-delete; the existing DELETE flips active=false.

const DEFAULT_FORM = {
  name: '',
  latitude: '',
  longitude: '',
  radius_m: '100',
  grace_minutes: '0',
  default_auto_session_close_time: '',
  active: true,
}

function timeForInput(value) {
  if (!value) return ''
  // API returns "HH:MM:SS"; the HTML time input wants "HH:MM".
  return value.length >= 5 ? value.slice(0, 5) : value
}

function timeForApi(value) {
  if (!value) return null
  return value.length === 5 ? `${value}:00` : value
}

function formatCoord(value) {
  if (value == null) return ''
  return Number(value).toFixed(7)
}

export default function AdminStaffLocations() {
  const [locations, setLocations] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(DEFAULT_FORM)
  const [gpsBusy, setGpsBusy] = useState(false)
  const [gpsResult, setGpsResult] = useState(null)
  const [testBusy, setTestBusy] = useState(false)
  const [testResult, setTestResult] = useState(null)

  async function refresh() {
    setLoadError(null)
    try {
      const data = await listAdminStaffLocations()
      setLocations(Array.isArray(data) ? data : [])
    } catch {
      setLoadError("Couldn't load staff locations.")
      setLocations([])
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  function openCreateDialog() {
    setEditing(null)
    setForm(DEFAULT_FORM)
    setGpsResult(null)
    setTestResult(null)
    setActionError(null)
    setDialogOpen(true)
  }

  function openEditDialog(loc) {
    setEditing(loc)
    setForm({
      name: loc.name || '',
      latitude: formatCoord(loc.latitude),
      longitude: formatCoord(loc.longitude),
      radius_m: String(loc.radius_m ?? '100'),
      grace_minutes: String(loc.grace_minutes ?? '0'),
      default_auto_session_close_time: timeForInput(
        loc.default_auto_session_close_time,
      ),
      active: Boolean(loc.active),
    })
    setGpsResult(null)
    setTestResult(null)
    setActionError(null)
    setDialogOpen(true)
  }

  function closeDialog() {
    setDialogOpen(false)
    setEditing(null)
    setForm(DEFAULT_FORM)
    setGpsResult(null)
    setTestResult(null)
  }

  function handleUseCurrentLocation() {
    if (!navigator.geolocation) {
      setGpsResult({
        ok: false,
        message: 'Your browser does not expose a location API.',
      })
      return
    }
    setGpsBusy(true)
    setGpsResult(null)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setForm((prev) => ({
          ...prev,
          latitude: pos.coords.latitude.toFixed(7),
          longitude: pos.coords.longitude.toFixed(7),
        }))
        setGpsResult({
          ok: true,
          message: `Captured your position with ±${Math.round(pos.coords.accuracy)}m accuracy.`,
        })
        setGpsBusy(false)
      },
      (err) => {
        setGpsResult({
          ok: false,
          message:
            err.code === 1
              ? 'Location permission denied. Allow it in your browser settings and try again.'
              : err.code === 3
                ? 'Location request timed out. Move outdoors or wait a moment, then try again.'
                : "Couldn't read your location.",
        })
        setGpsBusy(false)
      },
      { enableHighAccuracy: true, timeout: 10_000 },
    )
  }

  function handleTestGeofence() {
    if (!editing) return
    if (!navigator.geolocation) {
      setTestResult({
        ok: false,
        message: 'Your browser does not expose a location API.',
      })
      return
    }
    setTestBusy(true)
    setTestResult(null)
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        try {
          const data = await testStaffLocationGeofence(editing.id, {
            latitude: pos.coords.latitude,
            longitude: pos.coords.longitude,
          })
          setTestResult({
            ok: data.inside,
            distance_m: data.distance_m,
            radius_m: data.radius_m,
            accuracy_m: pos.coords.accuracy,
          })
        } catch {
          setTestResult({
            ok: false,
            message: "Couldn't reach the geofence test endpoint.",
          })
        } finally {
          setTestBusy(false)
        }
      },
      (err) => {
        setTestResult({
          ok: false,
          message:
            err.code === 1
              ? 'Location permission denied. Allow it in your browser settings and try again.'
              : err.code === 3
                ? 'Location request timed out. Move outdoors or wait a moment, then try again.'
                : "Couldn't read your location.",
        })
        setTestBusy(false)
      },
      { enableHighAccuracy: true, timeout: 10_000 },
    )
  }

  async function handleSave() {
    if (!form.name.trim()) {
      setActionError('Name is required.')
      return
    }
    const lat = Number(form.latitude)
    const lng = Number(form.longitude)
    if (!Number.isFinite(lat) || lat < -90 || lat > 90) {
      setActionError('Latitude must be between -90 and 90.')
      return
    }
    if (!Number.isFinite(lng) || lng < -180 || lng > 180) {
      setActionError('Longitude must be between -180 and 180.')
      return
    }
    const radius = Number(form.radius_m)
    if (!Number.isFinite(radius) || radius < 25 || radius > 1000) {
      setActionError('Radius must be between 25 and 1000 meters.')
      return
    }
    const grace = Number(form.grace_minutes)
    if (!Number.isFinite(grace) || grace < 0 || grace > 120) {
      setActionError('Grace minutes must be between 0 and 120.')
      return
    }

    setActionError(null)
    setBusyId(editing ? editing.id : 'create')
    try {
      const body = {
        name: form.name.trim(),
        latitude: lat,
        longitude: lng,
        radius_m: Math.round(radius),
        grace_minutes: Math.round(grace),
        default_auto_session_close_time: timeForApi(
          form.default_auto_session_close_time,
        ),
      }
      if (editing) {
        body.active = form.active
        await patchAdminStaffLocation(editing.id, body)
      } else {
        await createAdminStaffLocation(body)
      }
      closeDialog()
      await refresh()
    } catch {
      setActionError("Couldn't save the location.")
    } finally {
      setBusyId(null)
    }
  }

  async function handleDelete(loc) {
    if (
      !window.confirm(
        `Deactivate "${loc.name}"? Historical punches stay attributed to this location, but new clock-ins from these coordinates will be rejected.`,
      )
    ) {
      return
    }
    setActionError(null)
    setBusyId(loc.id)
    try {
      await deleteAdminStaffLocation(loc.id)
      await refresh()
    } catch {
      setActionError("Couldn't deactivate the location.")
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
          <Typography variant="h4">Staff locations</Typography>
          <Typography variant="body2" color="text.secondary">
            Boutique geofences. Stylists must clock in within the radius
            of an active location. Auto-close time drives the daily
            forgotten-clock-out cron; leave it blank to fall back to the
            24-hour runaway guard.
          </Typography>
        </Box>
        <Button variant="contained" onClick={openCreateDialog}>
          Add location
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
          {locations === null ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={28} />
            </Box>
          ) : locations.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No staff locations configured. Stylists will not be able to
              clock in until at least one active location exists.
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Name</TableCell>
                  <TableCell>Coordinates</TableCell>
                  <TableCell align="right">Radius</TableCell>
                  <TableCell align="right">Grace</TableCell>
                  <TableCell>Auto-close</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {locations.map((loc) => (
                  <TableRow key={loc.id}>
                    <TableCell>{loc.name}</TableCell>
                    <TableCell>
                      <Typography variant="body2" component="span">
                        {formatCoord(loc.latitude)}, {formatCoord(loc.longitude)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">{loc.radius_m}m</TableCell>
                    <TableCell align="right">{loc.grace_minutes}m</TableCell>
                    <TableCell>
                      {loc.default_auto_session_close_time
                        ? timeForInput(loc.default_auto_session_close_time)
                        : '—'}
                    </TableCell>
                    <TableCell>
                      {loc.active ? (
                        <Chip label="Active" size="small" color="success" />
                      ) : (
                        <Chip label="Inactive" size="small" variant="outlined" />
                      )}
                    </TableCell>
                    <TableCell align="right">
                      <Button
                        size="small"
                        disabled={busyId === loc.id}
                        onClick={() => openEditDialog(loc)}
                        sx={{ mr: 1 }}
                      >
                        Edit
                      </Button>
                      {loc.active && (
                        <IconButton
                          size="small"
                          color="error"
                          disabled={busyId === loc.id}
                          onClick={() => handleDelete(loc)}
                          aria-label={`Deactivate ${loc.name}`}
                        >
                          <DeleteOutlineIcon fontSize="small" />
                        </IconButton>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
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
          {editing ? 'Edit staff location' : 'Add staff location'}
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Stylists clock in only when their phone reports coordinates
            inside this radius. Use the "Use my current location" button
            from inside the boutique to seed the geofence accurately.
          </DialogContentText>
          <Stack spacing={2}>
            <TextField
              label="Name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
              fullWidth
            />
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                label="Latitude"
                type="number"
                value={form.latitude}
                onChange={(e) =>
                  setForm({ ...form, latitude: e.target.value })
                }
                inputProps={{ step: 0.0000001, min: -90, max: 90 }}
                required
                fullWidth
              />
              <TextField
                label="Longitude"
                type="number"
                value={form.longitude}
                onChange={(e) =>
                  setForm({ ...form, longitude: e.target.value })
                }
                inputProps={{ step: 0.0000001, min: -180, max: 180 }}
                required
                fullWidth
              />
            </Stack>
            <Box>
              <Button
                size="small"
                variant="outlined"
                startIcon={<MyLocationIcon />}
                disabled={gpsBusy}
                onClick={handleUseCurrentLocation}
              >
                {gpsBusy ? 'Reading location…' : 'Use my current location'}
              </Button>
              {gpsResult && (
                <Alert
                  severity={gpsResult.ok ? 'success' : 'warning'}
                  sx={{ mt: 1 }}
                >
                  {gpsResult.message}
                </Alert>
              )}
            </Box>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                label="Radius (meters)"
                type="number"
                value={form.radius_m}
                onChange={(e) =>
                  setForm({ ...form, radius_m: e.target.value })
                }
                inputProps={{ step: 1, min: 25, max: 1000 }}
                helperText="25-1000m. Smaller is stricter."
                required
                fullWidth
              />
              <TextField
                label="Grace minutes"
                type="number"
                value={form.grace_minutes}
                onChange={(e) =>
                  setForm({ ...form, grace_minutes: e.target.value })
                }
                inputProps={{ step: 1, min: 0, max: 120 }}
                helperText="Late tolerance after shift start."
                fullWidth
              />
            </Stack>
            <TextField
              label="Auto-close time (location-wide default)"
              type="time"
              value={form.default_auto_session_close_time}
              onChange={(e) =>
                setForm({
                  ...form,
                  default_auto_session_close_time: e.target.value,
                })
              }
              InputLabelProps={{ shrink: true }}
              helperText="Local time. Forgotten clock-outs are auto-closed at this time. Blank falls back to a 24h runaway guard."
              fullWidth
            />
            {editing && (
              <FormControlLabel
                control={
                  <Switch
                    checked={form.active}
                    onChange={(e) =>
                      setForm({ ...form, active: e.target.checked })
                    }
                  />
                }
                label="Active (clock-ins accepted from within the radius)"
              />
            )}
            {editing && (
              <Box>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<GpsFixedIcon />}
                  disabled={testBusy}
                  onClick={handleTestGeofence}
                >
                  {testBusy
                    ? 'Reading location and testing…'
                    : 'Test my GPS against this geofence'}
                </Button>
                {testResult && testResult.message && (
                  <Alert severity="warning" sx={{ mt: 1 }}>
                    {testResult.message}
                  </Alert>
                )}
                {testResult && testResult.message == null && (
                  <Alert
                    severity={testResult.ok ? 'success' : 'error'}
                    sx={{ mt: 1 }}
                  >
                    {testResult.ok
                      ? `Inside the geofence. ${Math.round(testResult.distance_m)}m from center, radius ${testResult.radius_m}m, GPS accuracy ±${Math.round(testResult.accuracy_m)}m.`
                      : `Outside the geofence. ${Math.round(testResult.distance_m)}m from center, radius ${testResult.radius_m}m, GPS accuracy ±${Math.round(testResult.accuracy_m)}m.`}
                  </Alert>
                )}
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: 'block', mt: 0.5 }}
                >
                  Uses the same haversine the punch gate uses. A passing
                  test means a real clock-in from these coordinates will
                  also pass.
                </Typography>
              </Box>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={closeDialog}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleSave}
            disabled={busyId === (editing ? editing.id : 'create')}
          >
            Save location
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
