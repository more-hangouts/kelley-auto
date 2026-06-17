import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { createContact, getContact, updateContact } from '../services/api'

const FIELDS = ['first_name', 'last_name', 'display_name', 'email', 'phone', 'notes']

function emptyForm(initialName = '') {
  return {
    first_name: '',
    last_name: '',
    display_name: initialName,
    email: '',
    phone: '',
    notes: '',
  }
}

function toForm(contact) {
  if (!contact) return emptyForm()
  return {
    first_name: contact.first_name || '',
    last_name: contact.last_name || '',
    display_name: contact.display_name || '',
    email: contact.email || '',
    phone: contact.phone || '',
    notes: contact.notes || '',
  }
}

function diffPatch(form, original) {
  const patch = {}
  for (const key of FIELDS) {
    const next = form[key]
    const prev = original[key]
    if (next === prev) continue
    if (key === 'display_name') {
      patch[key] = next.trim() || null
    } else if (next === '') {
      patch[key] = null
    } else {
      patch[key] = next
    }
  }
  return patch
}

function describeError(err) {
  const status = err?.response?.status
  const detail = err?.response?.data?.detail
  if (status === 409 && detail?.code === 'phone_collision') {
    const id = detail.conflict_contact_id
    return id != null
      ? `Phone is already in use by contact #${id}. Merge tooling lands in a later release.`
      : 'Phone is already in use by another contact.'
  }
  if (status === 422 && detail === 'display_name_required') {
    return 'Display name cannot be empty.'
  }
  if (status === 404) return 'Contact not found.'
  if (typeof detail === 'string') return detail
  return err?.message || 'Failed to save contact.'
}

export default function ContactEditDialog({
  open,
  contactId,
  mode = 'edit',
  initialName = '',
  onClose,
  onSaved,
}) {
  const queryClient = useQueryClient()
  const isCreate = mode === 'create'
  const [form, setForm] = useState(() => emptyForm(initialName))
  const [error, setError] = useState(null)

  const enabled = !isCreate && open && Number.isFinite(contactId)

  const { data: contact, isLoading } = useQuery({
    queryKey: ['contact', contactId],
    queryFn: () => getContact(contactId),
    enabled,
  })

  const original = useMemo(() => toForm(contact), [contact])

  useEffect(() => {
    if (!open) return
    if (isCreate) {
      setForm(emptyForm(initialName))
      setError(null)
    } else if (contact) {
      setForm(toForm(contact))
      setError(null)
    }
  }, [open, isCreate, initialName, contact])

  const save = useMutation({
    mutationFn: () => {
      if (isCreate) {
        const body = {}
        for (const key of FIELDS) {
          const v = (form[key] || '').trim()
          if (v) body[key] = v
        }
        return createContact(body)
      }
      return updateContact(contactId, diffPatch(form, original))
    },
    onSuccess: (saved) => {
      if (isCreate) {
        // POST response shape is { contact, was_new }.
        queryClient.setQueryData(['contact', saved.contact.id], saved.contact)
      } else {
        queryClient.setQueryData(['contact', contactId], saved)
      }
      queryClient.invalidateQueries({ queryKey: ['events', 'board'] })
      // Anything keyed under ['event', n] for events that point at this
      // contact shows a stale subtitle / Phase A caption otherwise.
      queryClient.invalidateQueries({ queryKey: ['event'] })
      onSaved?.(saved)
      onClose?.()
    },
    onError: (err) => setError(describeError(err)),
  })

  const dirty = useMemo(
    () =>
      isCreate
        ? FIELDS.some((k) => (form[k] || '').trim() !== '')
        : Object.keys(diffPatch(form, original)).length > 0,
    [form, original, isCreate],
  )

  function handleField(key) {
    return (e) => setForm((f) => ({ ...f, [key]: e.target.value }))
  }

  function handleSubmit(e) {
    e.preventDefault()
    if (!dirty || save.isPending) return
    setError(null)
    save.mutate()
  }

  return (
    <Dialog
      open={open}
      onClose={save.isPending ? undefined : onClose}
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle>{isCreate ? 'Create contact' : 'Edit contact'}</DialogTitle>
      <DialogContent dividers>
        {isLoading && !isCreate ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress size={24} />
          </Box>
        ) : (
          <Box component="form" onSubmit={handleSubmit} noValidate>
            <Stack spacing={2}>
              {error && <Alert severity="error">{error}</Alert>}

              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <TextField
                  fullWidth
                  label="First name"
                  value={form.first_name}
                  onChange={handleField('first_name')}
                  size="small"
                />
                <TextField
                  fullWidth
                  label="Last name"
                  value={form.last_name}
                  onChange={handleField('last_name')}
                  size="small"
                />
              </Stack>

              <TextField
                fullWidth
                label="Display name"
                value={form.display_name}
                onChange={handleField('display_name')}
                size="small"
                helperText="Required. If you leave this unchanged, first/last name edits auto-update it on save."
              />

              <TextField
                fullWidth
                label="Email"
                value={form.email}
                onChange={handleField('email')}
                size="small"
                type="email"
              />

              <TextField
                fullWidth
                label="Phone"
                value={form.phone}
                onChange={handleField('phone')}
                size="small"
              />

              <TextField
                fullWidth
                label="Notes"
                value={form.notes}
                onChange={handleField('notes')}
                size="small"
                multiline
                minRows={2}
              />

              {contact?.alternate_celebrants?.length > 0 && (
                <Box>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: 'block', mb: 0.5 }}
                  >
                    Other celebrant names seen on this contact
                  </Typography>
                  <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                    {contact.alternate_celebrants.map((name) => (
                      <Chip key={name} size="small" label={name} variant="outlined" />
                    ))}
                  </Stack>
                </Box>
              )}

              {contact && (
                <Typography variant="caption" color="text.secondary">
                  Linked to {contact.event_count} event
                  {contact.event_count === 1 ? '' : 's'} ·{' '}
                  {contact.appointment_count} appointment
                  {contact.appointment_count === 1 ? '' : 's'}
                </Typography>
              )}
            </Stack>
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={save.isPending}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={!dirty || save.isPending || (isLoading && !isCreate)}
        >
          {save.isPending
            ? isCreate
              ? 'Creating…'
              : 'Saving…'
            : isCreate
              ? 'Create'
              : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
