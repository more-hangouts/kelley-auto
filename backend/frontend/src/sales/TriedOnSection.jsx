import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Autocomplete,
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
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import ThumbDownAltOutlinedIcon from '@mui/icons-material/ThumbDownAltOutlined'
import ThumbUpAltOutlinedIcon from '@mui/icons-material/ThumbUpAltOutlined'

import {
  salesAddTriedOn,
  salesDeleteTriedOn,
  salesListTriedOn,
  salesPatchTriedOn,
  searchCatalogForSales,
} from '../services/api'
import { isAttendanceGateError, attendanceGateMessage } from './attendanceGate'

function catalogLabel(item) {
  if (!item) return ''
  const bits = [item.public_code]
  if (item.product_title) bits.push(item.product_title)
  if (item.color) bits.push(item.color)
  return bits.join(' · ')
}

export default function TriedOnSection({ appointmentId, hasEvent, onArrivePrompt }) {
  const [data, setData] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [adderOpen, setAdderOpen] = useState(false)
  const [refreshTick, setRefreshTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoadError(null)
    salesListTriedOn(appointmentId)
      .then((d) => {
        if (!cancelled) setData(d)
      })
      .catch(() => {
        if (cancelled) return
        setLoadError('Could not load the try-on list.')
      })
    return () => {
      cancelled = true
    }
  }, [appointmentId, refreshTick])

  const items = data?.items || []

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          sx={{ mb: 1 }}
        >
          <Typography variant="overline" color="text.secondary">
            Tried on
          </Typography>
          <Button
            size="small"
            variant="contained"
            onClick={() => setAdderOpen(true)}
            disabled={!hasEvent}
          >
            Add dress
          </Button>
        </Stack>

        {loadError && (
          <Alert severity="error" sx={{ mb: 1 }}>
            {loadError}
          </Alert>
        )}

        {!hasEvent && (
          <Alert
            severity="info"
            sx={{ mb: 1 }}
            action={
              onArrivePrompt && (
                <Button color="inherit" size="small" onClick={onArrivePrompt}>
                  Mark arrived
                </Button>
              )
            }
          >
            The try-on log starts once the customer is checked in. Tap
            Arrived above to start it.
          </Alert>
        )}

        {data === null && !loadError ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress size={20} />
          </Box>
        ) : items.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            Nothing logged yet.
          </Typography>
        ) : (
          <Stack divider={<Box sx={{ borderTop: '1px solid', borderColor: 'divider' }} />}>
            {items.map((item) => (
              <TriedOnRow
                key={item.id}
                row={item}
                disabled={!hasEvent}
                onChanged={() => setRefreshTick((n) => n + 1)}
              />
            ))}
          </Stack>
        )}
      </CardContent>

      <AddTriedOnDialog
        open={adderOpen}
        appointmentId={appointmentId}
        onClose={() => setAdderOpen(false)}
        onAdded={() => {
          setAdderOpen(false)
          setRefreshTick((n) => n + 1)
        }}
        existing={items}
      />
    </Card>
  )
}

function TriedOnRow({ row, disabled, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [draftSize, setDraftSize] = useState(row.size_label || '')
  const [draftNotes, setDraftNotes] = useState(row.notes || '')

  // Reconcile local drafts when the parent re-renders this row from
  // a refreshed list. Without this, edits would feel "stuck" after a
  // sibling row triggers a refetch.
  useEffect(() => {
    setDraftSize(row.size_label || '')
    setDraftNotes(row.notes || '')
  }, [row.id, row.size_label, row.notes])

  async function patch(fields) {
    setBusy(true)
    setError(null)
    try {
      await salesPatchTriedOn(row.id, fields)
      onChanged()
    } catch (err) {
      if (isAttendanceGateError(err)) {
        setError(attendanceGateMessage())
        return
      }
      const detail = err?.response?.data?.detail
      setError(
        detail === 'event_required'
          ? 'Mark this appointment as arrived first.'
          : detail === 'duplicate_tried_on'
            ? 'Another row already covers that size for this dress.'
            : 'Could not update.',
      )
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete() {
    setBusy(true)
    setError(null)
    try {
      await salesDeleteTriedOn(row.id)
      onChanged()
    } catch (err) {
      if (isAttendanceGateError(err)) {
        setError(attendanceGateMessage())
      } else {
        setError('Could not remove this row.')
      }
      setBusy(false)
    }
  }

  function handleLikeToggle(_e, val) {
    // val: 'liked' | 'disliked' | null. Map to row.liked.
    const next = val === 'liked' ? true : val === 'disliked' ? false : null
    if (next === row.liked) return
    patch({ liked: next })
  }

  function handleSizeBlur() {
    const trimmed = draftSize.trim()
    const next = trimmed || null
    if ((next || null) === (row.size_label || null)) return
    patch({ size_label: next })
  }

  function handleNotesBlur() {
    const next = draftNotes.trim() || null
    if ((next || '') === (row.notes || '')) return
    patch({ notes: next })
  }

  const item = row.catalog_item
  const liked = row.liked
  const toggleValue = liked === true ? 'liked' : liked === false ? 'disliked' : null

  return (
    <Stack
      direction={{ xs: 'column', sm: 'row' }}
      spacing={1.5}
      sx={{ py: 1.5 }}
      alignItems={{ xs: 'stretch', sm: 'flex-start' }}
    >
      <Box
        sx={{
          width: { xs: '100%', sm: 96 },
          minWidth: { sm: 96 },
          aspectRatio: '3 / 4',
          bgcolor: 'grey.100',
          borderRadius: 1,
          backgroundImage: item?.image_urls?.[0]
            ? `url(${item.image_urls[0]})`
            : 'none',
          backgroundSize: 'cover',
          backgroundPosition: 'center',
        }}
      />
      <Stack flex={1} spacing={0.75}>
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 500 }}>
              {item?.public_code || `#${row.catalog_item_id}`}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {[item?.product_title, item?.color].filter(Boolean).join(' · ') ||
                '—'}
            </Typography>
          </Box>
          <Tooltip title="Remove">
            <span>
              <IconButton
                size="small"
                onClick={handleDelete}
                disabled={busy || disabled}
                aria-label="Remove tried-on"
              >
                <DeleteOutlineIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </Stack>

        <Stack direction="row" spacing={1} alignItems="center">
          <TextField
            size="small"
            label="Size"
            value={draftSize}
            onChange={(e) => setDraftSize(e.target.value)}
            onBlur={handleSizeBlur}
            disabled={busy || disabled}
            sx={{ width: 110 }}
          />
          <ToggleButtonGroup
            size="small"
            value={toggleValue}
            exclusive
            onChange={handleLikeToggle}
            disabled={busy || disabled}
            aria-label="like state"
          >
            <ToggleButton value="liked" aria-label="liked">
              <ThumbUpAltOutlinedIcon fontSize="small" />
            </ToggleButton>
            <ToggleButton value="disliked" aria-label="disliked">
              <ThumbDownAltOutlinedIcon fontSize="small" />
            </ToggleButton>
          </ToggleButtonGroup>
        </Stack>

        <TextField
          size="small"
          fullWidth
          placeholder="Notes (optional)"
          value={draftNotes}
          onChange={(e) => setDraftNotes(e.target.value)}
          onBlur={handleNotesBlur}
          disabled={busy || disabled}
          multiline
        />

        {error && (
          <Typography variant="caption" color="error">
            {error}
          </Typography>
        )}
      </Stack>
    </Stack>
  )
}

function AddTriedOnDialog({ open, appointmentId, onClose, onAdded, existing }) {
  const [search, setSearch] = useState('')
  const [options, setOptions] = useState([])
  const [searching, setSearching] = useState(false)
  const [selected, setSelected] = useState(null)
  const [size, setSize] = useState('')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const debounceRef = useRef(null)

  // Reset on open/close so re-opening doesn't show prior state.
  useEffect(() => {
    if (open) {
      setSearch('')
      setOptions([])
      setSelected(null)
      setSize('')
      setNotes('')
      setError(null)
    }
  }, [open])

  // Debounced search — picker re-uses the existing /api/catalog list.
  useEffect(() => {
    if (!open) return
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setSearching(true)
      try {
        const rows = await searchCatalogForSales({ q: search, limit: 25 })
        setOptions(rows || [])
      } catch {
        setOptions([])
      } finally {
        setSearching(false)
      }
    }, 200)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [search, open])

  const existingKeys = useMemo(
    () =>
      new Set(
        (existing || []).map(
          (i) => `${i.catalog_item_id}|${(i.size_label || '').toLowerCase()}`,
        ),
      ),
    [existing],
  )

  async function handleSubmit() {
    if (!selected || submitting) return
    const dupKey = `${selected.id}|${size.trim().toLowerCase()}`
    if (existingKeys.has(dupKey)) {
      setError('That dress and size are already in the list.')
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      await salesAddTriedOn(appointmentId, {
        catalog_item_id: selected.id,
        size_label: size.trim() || null,
        notes: notes.trim() || null,
      })
      onAdded()
    } catch (err) {
      if (isAttendanceGateError(err)) {
        setError(attendanceGateMessage())
        return
      }
      const detail = err?.response?.data?.detail
      setError(
        detail === 'event_required'
          ? 'Mark this appointment as arrived first.'
          : detail === 'duplicate_tried_on'
            ? 'That dress and size are already logged.'
            : detail === 'catalog_item_inactive'
              ? 'That catalog item is inactive.'
              : 'Could not add this dress.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add a dress to the try-on log</DialogTitle>
      <DialogContent>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Stack spacing={2} sx={{ mt: 1 }}>
          <Autocomplete
            options={options}
            value={selected}
            onChange={(_e, val) => setSelected(val)}
            inputValue={search}
            onInputChange={(_e, val) => setSearch(val)}
            getOptionLabel={catalogLabel}
            isOptionEqualToValue={(a, b) => a?.id === b?.id}
            loading={searching}
            filterOptions={(x) => x}
            renderInput={(params) => (
              <TextField
                {...params}
                label="Search catalog"
                placeholder="Public code, designer, color, style…"
                autoFocus
              />
            )}
            renderOption={(props, option) => (
              <li {...props} key={option.id}>
                <Stack>
                  <Typography variant="body2">
                    {option.public_code} · {option.product_title || option.color}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {[option.designer, option.style_number, option.color]
                      .filter(Boolean)
                      .join(' · ')}
                  </Typography>
                </Stack>
              </li>
            )}
          />

          {selected && (
            <Stack direction="row" spacing={1} flexWrap="wrap">
              <Chip label={selected.public_code} size="small" />
              {selected.color && (
                <Chip label={selected.color} size="small" variant="outlined" />
              )}
              {selected.is_sample && (
                <Chip
                  label="Floor sample"
                  size="small"
                  color="success"
                  variant="outlined"
                />
              )}
            </Stack>
          )}

          <TextField
            label="Size (optional)"
            size="small"
            value={size}
            onChange={(e) => setSize(e.target.value)}
            placeholder="e.g. 10, 12, S/M"
            sx={{ maxWidth: 220 }}
          />
          <TextField
            label="Notes (optional)"
            size="small"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            multiline
            minRows={2}
            placeholder="Anything the next stylist should know."
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        <Button
          onClick={handleSubmit}
          variant="contained"
          disabled={!selected || submitting}
        >
          {submitting ? <CircularProgress size={20} /> : 'Add to list'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
