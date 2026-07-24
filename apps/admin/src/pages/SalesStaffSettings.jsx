import { useEffect, useMemo, useState } from 'react'
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
  Divider,
  FormControlLabel,
  IconButton,
  InputAdornment,
  MenuItem,
  Snackbar,
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
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import PersonRemoveOutlinedIcon from '@mui/icons-material/PersonRemoveOutlined'
import RestoreFromTrashOutlinedIcon from '@mui/icons-material/RestoreFromTrashOutlined'
import { Link as RouterLink } from 'react-router-dom'

import AttendanceReview from './AttendanceReview'
import { useAuth } from '../contexts/AuthContext'
import {
  archiveSalesStaff,
  changeOwnAdminPassword,
  clearSalesPin,
  createSalesStaff,
  listSalesStaff,
  mintSalesPin,
  patchSalesStaff,
  restoreSalesStaff,
  sendStaffPasswordReset,
  unlockSalesStaff,
} from '../services/api'

const ROLE_OPTIONS = [
  { value: 'admin', label: 'Admin' },
  { value: 'sales', label: 'Sales' },
  { value: 'user', label: 'Staff' },
]

const ROLE_LABELS = Object.fromEntries(
  ROLE_OPTIONS.map((opt) => [opt.value, opt.label]),
)

function formatTimestamp(value) {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

function formatWage(value) {
  if (value == null) return '—'
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  return `$${num.toFixed(2)}/hr`
}

function formatCommission(value) {
  if (value == null) return '—'
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  // 0.075 → "7.5%". Round to 2 decimal places of percentage so float
  // wobble (0.075 * 100 = 7.5000000000…) doesn't show in the cell.
  const pct = Math.round(num * 10000) / 100
  return `${pct}%`
}

function emptyForm() {
  return {
    full_name: '',
    username: '',
    email: '',
    role: 'sales',
    is_active: true,
    // Held as strings while the user types so partial input doesn't
    // collapse to NaN; converted to numbers on submit.
    hourly_wage_dollars: '',
    commission_rate_percent: '',
  }
}

function formFromRow(row) {
  return {
    full_name: row.full_name || '',
    username: row.username || '',
    email: row.email || '',
    role: row.role || 'sales',
    is_active: Boolean(row.is_active),
    hourly_wage_dollars:
      row.hourly_wage == null ? '' : String(Number(row.hourly_wage).toFixed(2)),
    commission_rate_percent:
      row.commission_rate == null
        ? ''
        : String(Math.round(row.commission_rate * 10000) / 100),
  }
}

function parseNullableNumber(value) {
  const trimmed = String(value ?? '').trim()
  if (trimmed === '') return { ok: true, value: null }
  const num = Number(trimmed)
  if (!Number.isFinite(num)) return { ok: false, value: null }
  return { ok: true, value: num }
}

export default function SalesStaffSettings() {
  const { user: currentUser } = useAuth()
  const [staff, setStaff] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [pinDialog, setPinDialog] = useState(null)
  const [passwordDialog, setPasswordDialog] = useState(null)
  const [editDialog, setEditDialog] = useState(null)
  const [snack, setSnack] = useState(null)
  const [view, setView] = useState('active') // 'active' | 'archived'
  const [archiveDialog, setArchiveDialog] = useState(null)
  const [busyId, setBusyId] = useState(null)

  async function refresh() {
    setLoadError(null)
    try {
      const rows = await listSalesStaff({ archived: view === 'archived' })
      setStaff(rows)
      // If the edit dialog is open, refresh the live `user` snapshot
      // so PIN status chips after a reset/clear reflect reality.
      setEditDialog((d) => {
        if (!d || d.mode !== 'edit' || !d.user) return d
        const fresh = rows.find((r) => r.id === d.user.id)
        return fresh ? { ...d, user: fresh } : d
      })
    } catch (err) {
      setLoadError(
        err?.response?.data?.detail || 'Could not load staff profiles.',
      )
      setStaff([])
    }
  }

  useEffect(() => {
    setStaff(null)
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view])

  async function confirmArchive() {
    if (!archiveDialog) return
    setArchiveDialog((d) => ({ ...d, busy: true, error: null }))
    try {
      await archiveSalesStaff(archiveDialog.row.id, {
        reason: archiveDialog.reason.trim() || undefined,
      })
      setArchiveDialog(null)
      setSnack({ severity: 'success', message: 'Staff member archived.' })
      refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      setArchiveDialog((d) => ({
        ...d,
        busy: false,
        error:
          code === 'last_active_admin'
            ? "Can't archive the last active admin."
            : code === 'cannot_archive_self'
              ? "You can't archive your own account."
              : 'Could not archive this staff member.',
      }))
    }
  }

  async function restoreRow(row) {
    setBusyId(row.id)
    setActionError(null)
    try {
      await restoreSalesStaff(row.id)
      setSnack({ severity: 'success', message: 'Staff member restored.' })
      refresh()
    } catch {
      setActionError('Could not restore this staff member.')
    } finally {
      setBusyId(null)
    }
  }

  function openCreate() {
    setActionError(null)
    setEditDialog({
      mode: 'create',
      user: null,
      form: emptyForm(),
      saving: false,
      pinBusy: false,
      formError: null,
      resetBusy: false,
    })
  }

  function openEdit(row) {
    setActionError(null)
    setEditDialog({
      mode: 'edit',
      user: row,
      form: formFromRow(row),
      saving: false,
      pinBusy: false,
      formError: null,
      resetBusy: false,
    })
  }

  function closeEdit() {
    setEditDialog((d) =>
      d?.saving || d?.pinBusy || d?.resetBusy ? d : null,
    )
  }

  function updateForm(patch) {
    setEditDialog((d) =>
      d
        ? { ...d, form: { ...d.form, ...patch }, formError: null }
        : d,
    )
  }

  function validateForm(form, { isCreate }) {
    if (!form.username.trim()) return 'Username is required.'
    if (isCreate && !form.email.trim()) {
      return 'Email is required when creating a new profile.'
    }
    if (!ROLE_LABELS[form.role]) return 'Pick a role.'
    const wage = parseNullableNumber(form.hourly_wage_dollars)
    if (!wage.ok) return 'Hourly wage must be a number (or blank).'
    if (wage.value != null && wage.value < 0) {
      return 'Hourly wage cannot be negative.'
    }
    const pct = parseNullableNumber(form.commission_rate_percent)
    if (!pct.ok) return 'Commission must be a number (or blank).'
    if (pct.value != null && (pct.value < 0 || pct.value > 100)) {
      return 'Commission must be between 0 and 100%.'
    }
    return null
  }

  function compensationPayload(form) {
    const wage = parseNullableNumber(form.hourly_wage_dollars)
    const pct = parseNullableNumber(form.commission_rate_percent)
    return {
      hourly_wage: wage.value,
      commission_rate: pct.value == null ? null : pct.value / 100,
    }
  }

  function explainBackendError(detail) {
    if (!detail) return 'Could not save the profile.'
    if (typeof detail === 'string') {
      if (detail === 'username_taken') return 'That username is already in use.'
      if (detail === 'email_taken') return 'That email is already in use.'
      if (detail === 'staff_user_not_found') return 'Profile no longer exists.'
      return detail
    }
    const code = detail?.code
    if (code === 'invalid_hourly_wage') return 'Hourly wage must be 0 or more.'
    if (code === 'invalid_commission_rate') {
      return 'Commission rate must be between 0 and 100%.'
    }
    if (code === 'invalid_role') return 'That role is not allowed.'
    if (code === 'nothing_to_update') return 'No changes to save.'
    return 'Could not save the profile.'
  }

  function openPasswordDialog() {
    setPasswordDialog({
      current_password: '',
      new_password: '',
      confirm_password: '',
      saving: false,
      error: null,
    })
  }

  function updatePasswordDialog(patch) {
    setPasswordDialog((d) => (d ? { ...d, ...patch, error: null } : d))
  }

  function closePasswordDialog() {
    setPasswordDialog((d) => (d?.saving ? d : null))
  }

  function explainPasswordError(detail) {
    if (detail === 'current_password_incorrect') {
      return 'Current password is incorrect.'
    }
    if (Array.isArray(detail)) {
      return 'New password must be at least 12 characters.'
    }
    return 'Could not change your password.'
  }

  async function handleChangePassword() {
    if (!passwordDialog) return
    const currentPassword = passwordDialog.current_password
    const newPassword = passwordDialog.new_password
    const confirmPassword = passwordDialog.confirm_password
    if (!currentPassword) {
      setPasswordDialog((d) =>
        d ? { ...d, error: 'Current password is required.' } : d,
      )
      return
    }
    if (newPassword.length < 12) {
      setPasswordDialog((d) =>
        d ? { ...d, error: 'New password must be at least 12 characters.' } : d,
      )
      return
    }
    if (newPassword !== confirmPassword) {
      setPasswordDialog((d) =>
        d ? { ...d, error: 'New passwords do not match.' } : d,
      )
      return
    }
    setPasswordDialog((d) => (d ? { ...d, saving: true, error: null } : d))
    try {
      await changeOwnAdminPassword(currentPassword, newPassword)
      setPasswordDialog(null)
      setSnack({ severity: 'success', message: 'Password changed.' })
    } catch (err) {
      setPasswordDialog((d) =>
        d
          ? {
              ...d,
              saving: false,
              error: explainPasswordError(err?.response?.data?.detail),
            }
          : d,
      )
    }
  }

  async function handleSendPasswordReset() {
    if (!editDialog?.user) return
    setEditDialog((d) => (d ? { ...d, resetBusy: true, formError: null } : d))
    try {
      await sendStaffPasswordReset(editDialog.user.id)
      setSnack({
        severity: 'success',
        message: `Reset email sent to ${editDialog.user.email}.`,
      })
    } catch (err) {
      const detail = err?.response?.data?.detail
      let message = 'Could not send the reset email.'
      if (detail === 'target_not_admin') {
        message = 'Password reset links can only be sent to admin users.'
      } else if (detail === 'target_user_inactive') {
        message = 'Activate this admin before sending a reset link.'
      }
      setEditDialog((d) => (d ? { ...d, formError: message } : d))
    } finally {
      setEditDialog((d) => (d ? { ...d, resetBusy: false } : d))
    }
  }

  async function handleSaveProfile() {
    if (!editDialog) return
    const form = editDialog.form
    const isCreate = editDialog.mode === 'create'
    const formError = validateForm(form, { isCreate })
    if (formError) {
      setEditDialog((d) => (d ? { ...d, formError } : d))
      return
    }
    setEditDialog((d) => (d ? { ...d, saving: true, formError: null } : d))
    try {
      if (isCreate) {
        const body = {
          username: form.username.trim(),
          email: form.email.trim(),
          full_name: form.full_name.trim() || null,
          role: form.role,
          ...compensationPayload(form),
        }
        const row = await createSalesStaff(body)
        // Preserve the legacy "create + mint PIN" UX for sales users
        // so the manager can hand over a PIN immediately. Admin / staff
        // roles don't get a PIN — they log in by password.
        if (row.role === 'sales') {
          const minted = await mintSalesPin(row.id)
          setPinDialog({
            pin: minted.pin,
            username: minted.user.username,
            full_name: minted.user.full_name,
          })
        }
        await refresh()
        setEditDialog(null)
      } else {
        const userId = editDialog.user.id
        const body = {
          username: form.username.trim(),
          email: form.email.trim(),
          full_name: form.full_name.trim() || null,
          role: form.role,
          is_active: form.is_active,
          ...compensationPayload(form),
        }
        await patchSalesStaff(userId, body)
        await refresh()
        setEditDialog(null)
      }
    } catch (err) {
      const message = explainBackendError(err?.response?.data?.detail)
      setEditDialog((d) =>
        d ? { ...d, saving: false, formError: message } : d,
      )
    }
  }

  async function handleResetPinFromDialog() {
    if (!editDialog?.user) return
    setEditDialog((d) => (d ? { ...d, pinBusy: true } : d))
    try {
      const minted = await mintSalesPin(editDialog.user.id)
      await refresh()
      setPinDialog({
        pin: minted.pin,
        username: minted.user.username,
        full_name: minted.user.full_name,
      })
    } catch {
      setEditDialog((d) =>
        d ? { ...d, pinBusy: false, formError: 'Could not mint a new PIN.' } : d,
      )
      return
    }
    setEditDialog((d) => (d ? { ...d, pinBusy: false } : d))
  }

  async function handleClearPinFromDialog() {
    if (!editDialog?.user) return
    const name = editDialog.user.full_name || editDialog.user.username
    if (
      !window.confirm(
        `Clear PIN for ${name}? They will not be able to sign in until you mint a new PIN.`,
      )
    ) {
      return
    }
    setEditDialog((d) => (d ? { ...d, pinBusy: true } : d))
    try {
      await clearSalesPin(editDialog.user.id)
      await refresh()
    } catch {
      setEditDialog((d) =>
        d ? { ...d, pinBusy: false, formError: 'Could not clear the PIN.' } : d,
      )
      return
    }
    setEditDialog((d) => (d ? { ...d, pinBusy: false } : d))
  }

  async function handleUnlockFromDialog() {
    if (!editDialog?.user) return
    setEditDialog((d) => (d ? { ...d, pinBusy: true } : d))
    try {
      await unlockSalesStaff(editDialog.user.id)
      await refresh()
    } catch {
      setEditDialog((d) =>
        d
          ? { ...d, pinBusy: false, formError: 'Could not unlock the account.' }
          : d,
      )
      return
    }
    setEditDialog((d) => (d ? { ...d, pinBusy: false } : d))
  }

  const sortedStaff = useMemo(() => {
    if (!Array.isArray(staff)) return staff
    // Backend already orders by full_name nulls last; the second sort
    // here is defensive against newly-created rows landing out of
    // place after a refresh.
    return [...staff].sort((a, b) => {
      const an = a.full_name || a.username || ''
      const bn = b.full_name || b.username || ''
      return an.localeCompare(bn)
    })
  }, [staff])

  return (
    <Stack spacing={3}>
      <Card>
        <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            justifyContent="space-between"
            alignItems={{ xs: 'flex-start', sm: 'center' }}
            spacing={2}
            sx={{ mb: 3 }}
          >
            <Box>
              <Typography variant="h4">Staff profiles</Typography>
              <Typography variant="body2" color="text.secondary">
                Manage names, roles, compensation, and PIN access for everyone
                on the team.
              </Typography>
            </Box>
            {view === 'active' && (
              <Button variant="contained" onClick={openCreate}>
                Add staff profile
              </Button>
            )}
          </Stack>

          <Tabs
            value={view}
            onChange={(_, v) => v && setView(v)}
            sx={{ mb: 2 }}
          >
            <Tab value="active" label="Active" />
            <Tab value="archived" label="Archived" />
          </Tabs>

          {loadError && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {loadError}
            </Alert>
          )}
          {actionError && (
            <Alert
              severity="error"
              sx={{ mb: 2 }}
              onClose={() => setActionError(null)}
            >
              {actionError}
            </Alert>
          )}

          {staff === null ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
              <CircularProgress />
            </Box>
          ) : sortedStaff.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              {view === 'archived'
                ? 'No archived staff.'
                : 'No staff profiles yet. Add one to get started.'}
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Name</TableCell>
                  <TableCell>Username</TableCell>
                  <TableCell>Role</TableCell>
                  <TableCell>PIN status</TableCell>
                  <TableCell>Last sign-in</TableCell>
                  <TableCell align="right">Wage</TableCell>
                  <TableCell align="right">Commission</TableCell>
                  <TableCell align="right">Status</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {sortedStaff.map((row) => (
                  <TableRow key={row.id} hover>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {row.full_name || '—'}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary">
                        {row.username}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={ROLE_LABELS[row.role] || row.role}
                        size="small"
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell>
                      {row.role !== 'sales' ? (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                        >
                          —
                        </Typography>
                      ) : (
                        <Stack direction="row" spacing={0.5}>
                          {row.has_pin ? (
                            <Chip
                              label="PIN set"
                              size="small"
                              color="success"
                              variant="outlined"
                            />
                          ) : (
                            <Chip
                              label="No PIN"
                              size="small"
                              variant="outlined"
                            />
                          )}
                          {row.force_pin_change && (
                            <Chip
                              label="Must change"
                              size="small"
                              color="warning"
                              variant="outlined"
                            />
                          )}
                          {row.pin_locked && (
                            <Chip
                              label="Locked"
                              size="small"
                              color="error"
                              variant="outlined"
                            />
                          )}
                        </Stack>
                      )}
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary">
                        {formatTimestamp(row.last_login || row.last_pin_used_at)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">{formatWage(row.hourly_wage)}</TableCell>
                    <TableCell align="right">
                      {formatCommission(row.commission_rate)}
                    </TableCell>
                    <TableCell align="right">
                      {row.is_active ? (
                        <Chip
                          label="Active"
                          size="small"
                          color="success"
                          variant="outlined"
                        />
                      ) : (
                        <Chip
                          label="Inactive"
                          size="small"
                          variant="outlined"
                        />
                      )}
                    </TableCell>
                    <TableCell align="right">
                      {view === 'archived' ? (
                        <Button
                          size="small"
                          variant="outlined"
                          startIcon={<RestoreFromTrashOutlinedIcon />}
                          disabled={busyId === row.id}
                          onClick={() => restoreRow(row)}
                        >
                          Restore
                        </Button>
                      ) : (
                        <Stack
                          direction="row"
                          spacing={0.5}
                          justifyContent="flex-end"
                        >
                          <Tooltip title="Edit staff profile" arrow>
                            <IconButton
                              size="small"
                              onClick={() => openEdit(row)}
                            >
                              <EditOutlinedIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                          {row.id !== currentUser?.id && (
                            <Tooltip title="Archive (remove from roster)" arrow>
                              <IconButton
                                size="small"
                                color="error"
                                onClick={() =>
                                  setArchiveDialog({
                                    row,
                                    reason: '',
                                    busy: false,
                                    error: null,
                                  })
                                }
                              >
                                <PersonRemoveOutlinedIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          )}
                        </Stack>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>

        <Dialog
          open={editDialog !== null}
          onClose={closeEdit}
          maxWidth="sm"
          fullWidth
        >
          <DialogTitle>
            {editDialog?.mode === 'create'
              ? 'Add staff profile'
              : `Edit ${editDialog?.user?.full_name || editDialog?.user?.username || 'staff'}`}
          </DialogTitle>
          <DialogContent>
            {editDialog?.mode === 'create' && (
              <DialogContentText sx={{ mb: 2 }}>
                Create the profile, then PIN access opens up for sales staff.
                A one-time PIN is minted for sales roles so you can hand it
                over right away; admins sign in with a password.
              </DialogContentText>
            )}
            {editDialog?.formError && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {editDialog.formError}
              </Alert>
            )}
            {editDialog && (
              <Stack spacing={3} sx={{ mt: 0.5 }}>
                <Box>
                  <Typography
                    variant="subtitle2"
                    sx={{ mb: 1.25, color: 'text.primary' }}
                  >
                    Basic info
                  </Typography>
                  <Stack spacing={2}>
                    <TextField
                      label="Full name"
                      size="small"
                      fullWidth
                      value={editDialog.form.full_name}
                      onChange={(e) =>
                        updateForm({ full_name: e.target.value })
                      }
                      disabled={editDialog.saving}
                    />
                    <TextField
                      label="Username"
                      size="small"
                      fullWidth
                      required
                      value={editDialog.form.username}
                      onChange={(e) =>
                        updateForm({ username: e.target.value })
                      }
                      helperText="Sales staff type this at sign-in. Letters, numbers, dots, underscores."
                      disabled={editDialog.saving}
                    />
                    <TextField
                      label="Email"
                      type="email"
                      size="small"
                      fullWidth
                      required={editDialog.mode === 'create'}
                      value={editDialog.form.email}
                      onChange={(e) =>
                        updateForm({ email: e.target.value })
                      }
                      disabled={editDialog.saving}
                    />
                    <Stack
                      direction={{ xs: 'column', sm: 'row' }}
                      spacing={2}
                    >
                      <TextField
                        select
                        label="Role"
                        size="small"
                        fullWidth
                        value={editDialog.form.role}
                        onChange={(e) =>
                          updateForm({ role: e.target.value })
                        }
                        disabled={editDialog.saving}
                      >
                        {ROLE_OPTIONS.map((opt) => (
                          <MenuItem key={opt.value} value={opt.value}>
                            {opt.label}
                          </MenuItem>
                        ))}
                      </TextField>
                      {editDialog.mode === 'edit' && (
                        <FormControlLabel
                          control={
                            <Switch
                              checked={editDialog.form.is_active}
                              onChange={(e) =>
                                updateForm({ is_active: e.target.checked })
                              }
                              disabled={editDialog.saving}
                            />
                          }
                          label="Active"
                        />
                      )}
                    </Stack>
                  </Stack>
                </Box>

                <Box>
                  <Typography
                    variant="subtitle2"
                    sx={{ mb: 1.25, color: 'text.primary' }}
                  >
                    Compensation
                  </Typography>
                  <Stack
                    direction={{ xs: 'column', sm: 'row' }}
                    spacing={2}
                  >
                    <TextField
                      label="Hourly wage"
                      type="number"
                      size="small"
                      fullWidth
                      value={editDialog.form.hourly_wage_dollars}
                      onChange={(e) =>
                        updateForm({ hourly_wage_dollars: e.target.value })
                      }
                      InputProps={{
                        startAdornment: (
                          <InputAdornment position="start">$</InputAdornment>
                        ),
                        endAdornment: (
                          <InputAdornment position="end">/hr</InputAdornment>
                        ),
                      }}
                      inputProps={{ min: 0, step: 0.25 }}
                      helperText="Leave blank if not paid hourly."
                      disabled={editDialog.saving}
                    />
                    <TextField
                      label="Commission"
                      type="number"
                      size="small"
                      fullWidth
                      value={editDialog.form.commission_rate_percent}
                      onChange={(e) =>
                        updateForm({
                          commission_rate_percent: e.target.value,
                        })
                      }
                      InputProps={{
                        endAdornment: (
                          <InputAdornment position="end">%</InputAdornment>
                        ),
                      }}
                      inputProps={{ min: 0, max: 100, step: 0.1 }}
                      helperText="Stored as a fraction (7.5% = 0.075)."
                      disabled={editDialog.saving}
                    />
                  </Stack>
                </Box>

                {editDialog.mode === 'edit' && (
                  <Box>
                    <Typography
                      variant="subtitle2"
                      sx={{ mb: 1.25, color: 'text.primary' }}
                    >
                      Access &amp; security
                    </Typography>
                    {editDialog.form.role !== 'sales' ? (
                      <Stack spacing={1.5} alignItems="flex-start">
                        <Typography
                          variant="body2"
                          color="text.secondary"
                        >
                          {ROLE_LABELS[editDialog.form.role] || 'Non-sales'}{' '}
                          users sign in with a password — PINs are not used.
                        </Typography>
                        {currentUser?.id === editDialog.user.id ? (
                          <Button
                            variant="outlined"
                            size="small"
                            onClick={openPasswordDialog}
                            disabled={editDialog.saving || editDialog.resetBusy}
                          >
                            Change Password
                          </Button>
                        ) : editDialog.form.role === 'admin' ? (
                          <Button
                            variant="outlined"
                            size="small"
                            onClick={handleSendPasswordReset}
                            disabled={editDialog.saving || editDialog.resetBusy}
                          >
                            {editDialog.resetBusy ? (
                              <CircularProgress size={18} />
                            ) : (
                              'Send Password Reset Link'
                            )}
                          </Button>
                        ) : null}
                      </Stack>
                    ) : (
                      <Stack spacing={1.5}>
                        <Stack
                          direction="row"
                          spacing={1}
                          alignItems="center"
                        >
                          {editDialog.user.has_pin ? (
                            <Chip
                              label="PIN set"
                              size="small"
                              color="success"
                              variant="outlined"
                            />
                          ) : (
                            <Chip
                              label="No PIN"
                              size="small"
                              variant="outlined"
                            />
                          )}
                          {editDialog.user.force_pin_change && (
                            <Chip
                              label="Must change at next sign-in"
                              size="small"
                              color="warning"
                              variant="outlined"
                            />
                          )}
                          {editDialog.user.pin_locked && (
                            <Chip
                              label="Locked"
                              size="small"
                              color="error"
                              variant="outlined"
                            />
                          )}
                        </Stack>
                        <Stack
                          direction={{ xs: 'column', sm: 'row' }}
                          spacing={1}
                        >
                          <Button
                            variant="outlined"
                            size="small"
                            onClick={handleResetPinFromDialog}
                            disabled={
                              editDialog.saving || editDialog.pinBusy
                            }
                          >
                            {editDialog.user.has_pin
                              ? 'Reset PIN'
                              : 'Set PIN'}
                          </Button>
                          {editDialog.user.has_pin && (
                            <Button
                              variant="outlined"
                              color="error"
                              size="small"
                              onClick={handleClearPinFromDialog}
                              disabled={
                                editDialog.saving || editDialog.pinBusy
                              }
                            >
                              Clear PIN
                            </Button>
                          )}
                          {editDialog.user.pin_locked && (
                            <Button
                              variant="outlined"
                              size="small"
                              onClick={handleUnlockFromDialog}
                              disabled={
                                editDialog.saving || editDialog.pinBusy
                              }
                            >
                              Clear lockout
                            </Button>
                          )}
                          <Button
                            size="small"
                            component={RouterLink}
                            to={`/settings/staff/profiles/${editDialog.user.id}/schedule`}
                          >
                            Open weekly schedule
                          </Button>
                        </Stack>
                      </Stack>
                    )}
                  </Box>
                )}
              </Stack>
            )}
          </DialogContent>
          <DialogActions>
            <Button
              onClick={closeEdit}
              disabled={editDialog?.saving || editDialog?.resetBusy}
            >
              Cancel
            </Button>
            <Button
              variant="contained"
              onClick={handleSaveProfile}
              disabled={
                editDialog?.saving || editDialog?.pinBusy || editDialog?.resetBusy
              }
            >
              {editDialog?.saving ? (
                <CircularProgress size={20} />
              ) : editDialog?.mode === 'create' ? (
                'Create staff profile'
              ) : (
                'Save profile'
              )}
            </Button>
          </DialogActions>
        </Dialog>

        {/* PIN reveal dialog — shown ONCE */}
        <Dialog
          open={pinDialog !== null}
          onClose={() => setPinDialog(null)}
          maxWidth="xs"
          fullWidth
        >
          <DialogTitle>One-time PIN</DialogTitle>
          <DialogContent>
            <DialogContentText sx={{ mb: 2 }}>
              Hand this PIN to{' '}
              <strong>{pinDialog?.full_name || pinDialog?.username}</strong>.
              The PIN won't be shown again. They'll be required to choose
              their own PIN at first sign-in.
            </DialogContentText>
            <Box
              sx={{
                p: 2,
                border: '1px dashed',
                borderColor: 'divider',
                borderRadius: 1,
                textAlign: 'center',
              }}
            >
              <Typography
                variant="h3"
                sx={{
                  letterSpacing: '0.4em',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {pinDialog?.pin}
              </Typography>
            </Box>
            <Divider sx={{ my: 2 }} />
            <Typography variant="caption" color="text.secondary">
              Username: {pinDialog?.username}
            </Typography>
          </DialogContent>
          <DialogActions>
            <Button variant="contained" onClick={() => setPinDialog(null)}>
              Done
            </Button>
          </DialogActions>
        </Dialog>

        <Dialog
          open={passwordDialog !== null}
          onClose={closePasswordDialog}
          maxWidth="xs"
          fullWidth
        >
          <DialogTitle>Change Password</DialogTitle>
          <DialogContent>
            {passwordDialog?.error && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {passwordDialog.error}
              </Alert>
            )}
            <Stack spacing={2} sx={{ mt: 0.5 }}>
              <TextField
                label="Current Password"
                type="password"
                size="small"
                fullWidth
                value={passwordDialog?.current_password || ''}
                onChange={(e) =>
                  updatePasswordDialog({ current_password: e.target.value })
                }
                disabled={passwordDialog?.saving}
                autoComplete="current-password"
              />
              <TextField
                label="New Password"
                type="password"
                size="small"
                fullWidth
                value={passwordDialog?.new_password || ''}
                onChange={(e) =>
                  updatePasswordDialog({ new_password: e.target.value })
                }
                disabled={passwordDialog?.saving}
                autoComplete="new-password"
              />
              <TextField
                label="Confirm New Password"
                type="password"
                size="small"
                fullWidth
                value={passwordDialog?.confirm_password || ''}
                onChange={(e) =>
                  updatePasswordDialog({ confirm_password: e.target.value })
                }
                disabled={passwordDialog?.saving}
                autoComplete="new-password"
              />
            </Stack>
          </DialogContent>
          <DialogActions>
            <Button
              onClick={closePasswordDialog}
              disabled={passwordDialog?.saving}
            >
              Cancel
            </Button>
            <Button
              variant="contained"
              onClick={handleChangePassword}
              disabled={passwordDialog?.saving}
            >
              {passwordDialog?.saving ? (
                <CircularProgress size={20} />
              ) : (
                'Save Password'
              )}
            </Button>
          </DialogActions>
        </Dialog>
      </Card>

      <Box>
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          justifyContent="space-between"
          alignItems={{ xs: 'flex-start', sm: 'center' }}
          spacing={1}
          sx={{ mb: 1 }}
        >
          <Typography variant="h5">Today on the floor</Typography>
          <Button
            component={RouterLink}
            to="/settings/staff/attendance"
            size="small"
            variant="outlined"
          >
            Full attendance review
          </Button>
        </Stack>
        <AttendanceReview mode="today_panel" />
      </Box>
      <Dialog
        open={archiveDialog !== null}
        onClose={() =>
          archiveDialog?.busy ? null : setArchiveDialog(null)
        }
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>Archive staff member</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            {archiveDialog?.row?.full_name ||
              archiveDialog?.row?.username}{' '}
            will be removed from the active roster and can no longer sign
            in or be scheduled. Their history is kept, and you can restore
            them anytime from the Archived tab.
          </DialogContentText>
          <TextField
            label="Reason (optional)"
            value={archiveDialog?.reason || ''}
            onChange={(e) =>
              setArchiveDialog((d) => ({ ...d, reason: e.target.value }))
            }
            size="small"
            fullWidth
            multiline
            minRows={2}
            inputProps={{ maxLength: 500 }}
            disabled={archiveDialog?.busy}
          />
          {archiveDialog?.error && (
            <Alert severity="error" sx={{ mt: 2 }}>
              {archiveDialog.error}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => setArchiveDialog(null)}
            disabled={archiveDialog?.busy}
          >
            Cancel
          </Button>
          <Button
            color="error"
            variant="contained"
            onClick={confirmArchive}
            disabled={archiveDialog?.busy}
          >
            Archive
          </Button>
        </DialogActions>
      </Dialog>
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
    </Stack>
  )
}
