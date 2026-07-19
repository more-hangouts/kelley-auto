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
import AddIcon from '@mui/icons-material/Add'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'

import {
  createNotificationSubscriber,
  deleteNotificationSubscriber,
  listNotificationSubscribers,
  setSubscriberActive,
  updateSubscriberSubscriptions,
} from '../services/api'

// "Who gets what" — Omnichannel Inbox Plan Part 1.
//
// A people × alert-kinds matrix over /api/admin/notification-subscribers.
// A subscriber is either a login user (has_login) or an external, email-only
// recipient created here with just a name + email. Each switch toggles one
// (subscriber, kind) subscription and saves immediately through
// PUT /{id}/subscriptions — no separate "Save" step, mirroring how the
// backend upserts a single kind at a time.

const DEFAULT_FORM = { display_name: '', email: '' }

export default function NotificationSubscribers() {
  const [subscribers, setSubscribers] = useState(null)
  const [catalog, setCatalog] = useState([])
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  // Per-cell + per-row busy keys so only the touched control spins.
  const [busyKey, setBusyKey] = useState(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [form, setForm] = useState(DEFAULT_FORM)
  const [saving, setSaving] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(null)

  async function refresh() {
    setLoadError(null)
    try {
      const data = await listNotificationSubscribers()
      setSubscribers(data.subscribers || [])
      setCatalog(data.catalog || [])
    } catch {
      setLoadError("Couldn't load notification recipients.")
      setSubscribers([])
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  async function toggleSubscription(subscriber, kind, nextEnabled) {
    setActionError(null)
    setBusyKey(`${subscriber.id}:${kind}`)
    // Optimistic: flip the cell, revert on failure.
    setSubscribers((prev) =>
      prev.map((s) =>
        s.id === subscriber.id
          ? { ...s, subscriptions: { ...s.subscriptions, [kind]: nextEnabled } }
          : s,
      ),
    )
    try {
      const updated = await updateSubscriberSubscriptions(subscriber.id, [
        { kind, enabled: nextEnabled },
      ])
      setSubscribers((prev) =>
        prev.map((s) => (s.id === subscriber.id ? updated : s)),
      )
    } catch {
      setActionError("Couldn't save that change. Reloading.")
      await refresh()
    } finally {
      setBusyKey(null)
    }
  }

  async function toggleActive(subscriber) {
    setActionError(null)
    setBusyKey(`active:${subscriber.id}`)
    try {
      const updated = await setSubscriberActive(
        subscriber.id,
        !subscriber.is_active,
      )
      setSubscribers((prev) =>
        prev.map((s) => (s.id === subscriber.id ? updated : s)),
      )
    } catch {
      setActionError("Couldn't update that person's status.")
    } finally {
      setBusyKey(null)
    }
  }

  async function handleAdd() {
    const display_name = form.display_name.trim()
    const email = form.email.trim()
    if (!display_name || !email) return
    setSaving(true)
    setActionError(null)
    try {
      const created = await createNotificationSubscriber({ display_name, email })
      setSubscribers((prev) => [...(prev || []), created])
      setDialogOpen(false)
      setForm(DEFAULT_FORM)
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      setActionError(
        code === 'subscriber_already_exists'
          ? 'Someone with that email is already a recipient.'
          : "Couldn't add that person.",
      )
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    if (!confirmDelete) return
    const target = confirmDelete
    setBusyKey(`active:${target.id}`)
    setActionError(null)
    try {
      await deleteNotificationSubscriber(target.id)
      setSubscribers((prev) => prev.filter((s) => s.id !== target.id))
      setConfirmDelete(null)
    } catch {
      setActionError("Couldn't remove that person.")
    } finally {
      setBusyKey(null)
    }
  }

  if (subscribers === null) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 6 }}>
        <CircularProgress />
      </Box>
    )
  }

  return (
    <Card>
      <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          justifyContent="space-between"
          alignItems={{ xs: 'flex-start', sm: 'center' }}
          spacing={2}
          sx={{ mb: 1 }}
        >
          <Box>
            <Typography variant="h4" gutterBottom>
              Notifications
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Choose who receives each alert. Add anyone by name and email —
              they don&apos;t need a login to get email alerts.
            </Typography>
          </Box>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => {
              setForm(DEFAULT_FORM)
              setActionError(null)
              setDialogOpen(true)
            }}
          >
            Add person
          </Button>
        </Stack>

        {loadError && <Alert severity="error" sx={{ my: 2 }}>{loadError}</Alert>}
        {actionError && (
          <Alert severity="error" sx={{ my: 2 }} onClose={() => setActionError(null)}>
            {actionError}
          </Alert>
        )}

        {subscribers.length === 0 ? (
          <Alert severity="info" sx={{ mt: 3 }}>
            No recipients yet. Add a person to start routing alerts to them.
          </Alert>
        ) : (
          <Box sx={{ overflowX: 'auto', mt: 2 }}>
            <Table size="small" sx={{ minWidth: 640 }}>
              <TableHead>
                <TableRow>
                  <TableCell sx={{ fontWeight: 600 }}>Person</TableCell>
                  {catalog.map((c) => (
                    <TableCell key={c.kind} align="center" sx={{ fontWeight: 600 }}>
                      <Tooltip title={c.description || ''} arrow>
                        <span>{c.label}</span>
                      </Tooltip>
                    </TableCell>
                  ))}
                  <TableCell align="center" sx={{ fontWeight: 600 }}>Active</TableCell>
                  <TableCell align="right" />
                </TableRow>
              </TableHead>
              <TableBody>
                {subscribers.map((s) => {
                  const dimmed = !s.is_active
                  return (
                    <TableRow key={s.id} hover sx={{ opacity: dimmed ? 0.5 : 1 }}>
                      <TableCell>
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Box>
                            <Typography variant="body2" fontWeight={600}>
                              {s.display_name}
                            </Typography>
                            <Typography variant="caption" color="text.secondary">
                              {s.email || '(no email on file)'}
                            </Typography>
                          </Box>
                          <Chip
                            size="small"
                            label={s.has_login ? 'Login' : 'Email only'}
                            color={s.has_login ? 'primary' : 'default'}
                            variant="outlined"
                          />
                        </Stack>
                      </TableCell>
                      {catalog.map((c) => {
                        const key = `${s.id}:${c.kind}`
                        return (
                          <TableCell key={c.kind} align="center">
                            {busyKey === key ? (
                              <CircularProgress size={18} />
                            ) : (
                              <Switch
                                size="small"
                                checked={Boolean(s.subscriptions?.[c.kind])}
                                disabled={!s.is_active}
                                onChange={(e) =>
                                  toggleSubscription(s, c.kind, e.target.checked)
                                }
                              />
                            )}
                          </TableCell>
                        )
                      })}
                      <TableCell align="center">
                        {busyKey === `active:${s.id}` ? (
                          <CircularProgress size={18} />
                        ) : (
                          <Switch
                            size="small"
                            checked={s.is_active}
                            onChange={() => toggleActive(s)}
                          />
                        )}
                      </TableCell>
                      <TableCell align="right">
                        <Tooltip title="Remove">
                          <IconButton
                            size="small"
                            onClick={() => setConfirmDelete(s)}
                          >
                            <DeleteOutlineIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </Box>
        )}
      </CardContent>

      {/* Add person */}
      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} fullWidth maxWidth="xs">
        <DialogTitle>Add recipient</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            They&apos;ll receive email alerts for the types you switch on. No
            login required.
          </DialogContentText>
          <Stack spacing={2}>
            <TextField
              label="Name"
              value={form.display_name}
              onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
              autoFocus
              fullWidth
            />
            <TextField
              label="Email"
              type="email"
              value={form.email}
              onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleAdd}
            disabled={saving || !form.display_name.trim() || !form.email.trim()}
          >
            {saving ? 'Adding…' : 'Add'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Confirm remove */}
      <Dialog open={Boolean(confirmDelete)} onClose={() => setConfirmDelete(null)}>
        <DialogTitle>Remove recipient?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {confirmDelete?.display_name} will stop receiving all alerts. This
            can&apos;t be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
          <Button color="error" variant="contained" onClick={handleDelete}>
            Remove
          </Button>
        </DialogActions>
      </Dialog>
    </Card>
  )
}
