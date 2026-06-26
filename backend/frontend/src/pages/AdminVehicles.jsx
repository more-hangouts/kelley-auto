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
  DialogTitle,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  TextField,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import PhotoCameraOutlinedIcon from '@mui/icons-material/PhotoCameraOutlined'

import {
  createVehicle,
  listVehicles,
  updateVehicle,
  uploadVehiclePhoto,
} from '../services/api'
import { formatDollars, formatUSD, parseDollars } from '../utils/money'

// Admin vehicle inventory, mounted at /inventory (top-level nav).
//
// Vehicles are catalog_items rows discriminated by is_vehicle=true
// (migration 085). This page is the dealership-facing surface: it lists,
// creates, and edits cars through the existing /catalog API using the
// vehicle shape. The dress/accessory catalog lives separately at
// /products (AdminCatalog) and is untouched by anything here.
//
// Create sends is_vehicle + stock_number + exterior_color + the vehicle
// fields and lets the API derive internal_sku<-stock_number,
// color<-exterior_color, category='vehicle', and mirror
// make->designer / model->style_number. Edit patches mutable fields only
// (never is_vehicle) and re-threads designer/style_number/color so the
// compat search columns stay in sync — the PATCH route does not re-mirror.

// The six statuses migration 085's CHECK allows, each with the MUI chip
// color used in the list. Keep in sync with VEHICLE_STATUS_VALUES in
// services/catalog_service.py.
const VEHICLE_STATUSES = [
  { value: 'available', label: 'Available', color: 'success' },
  { value: 'pending', label: 'Pending', color: 'warning' },
  { value: 'sold', label: 'Sold', color: 'default' },
  { value: 'delivered', label: 'Delivered', color: 'info' },
  { value: 'wholesale', label: 'Wholesale', color: 'secondary' },
  { value: 'hidden', label: 'Hidden', color: 'default' },
]

function statusMeta(value) {
  return (
    VEHICLE_STATUSES.find((s) => s.value === value) || {
      value,
      label: value || '—',
      color: 'default',
    }
  )
}

// Friendly copy for the domain error codes the catalog service raises on
// a vehicle write (see _CATALOG_ERROR_STATUS in api/routers/catalog.py).
const ERROR_MESSAGES = {
  vehicle_vin_invalid: 'VIN must be exactly 17 characters.',
  vehicle_year_invalid: 'Year must be a whole number.',
  vehicle_year_out_of_range: 'Year is outside the allowed range.',
  vehicle_mileage_invalid: 'Mileage must be a non-negative whole number.',
  vehicle_status_invalid: 'That status is not allowed.',
  vehicle_features_invalid: 'Features must be a list of text values.',
  unit_price_cents_negative: 'Price must be a non-negative amount.',
  unit_price_cents_invalid: 'Price must be a valid amount.',
  catalog_field_required: 'A required field is missing.',
  image_urls_invalid: 'Photo URLs must be a list of text values.',
}

function extractApiError(err) {
  const status = err?.response?.status
  const detail = err?.response?.data?.detail
  if (status === 409) return 'That VIN or stock number is already in use.'
  if (typeof detail === 'string') {
    return ERROR_MESSAGES[detail] || "Couldn't save the vehicle."
  }
  if (detail && typeof detail === 'object' && !Array.isArray(detail) && detail.code) {
    return ERROR_MESSAGES[detail.code] || `Couldn't save: ${detail.code}.`
  }
  // FastAPI request-validation errors come back as a list of {loc, msg}.
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0]
    return first?.msg ? `Couldn't save: ${first.msg}` : "Couldn't save the vehicle."
  }
  return "Couldn't save the vehicle."
}

const MAX_VEHICLE_YEAR = new Date().getFullYear() + 1
const MIN_VEHICLE_YEAR = 1980

const DEFAULT_FORM = {
  stock_number: '',
  vin: '',
  year: '',
  make: '',
  model: '',
  trim: '',
  mileage: '',
  price_dollars: '',
  exterior_color: '',
  interior_color: '',
  transmission: '',
  fuel_type: '',
  body_type: '',
  drivetrain: '',
  condition: '',
  vehicle_status: 'available',
  description_text: '',
  carfax_url: '',
  video_url: '',
  image_urls: [],
  features: [],
  active: true,
}

function formFromRow(row) {
  return {
    stock_number: row.stock_number || '',
    vin: row.vin || '',
    year: row.year ?? '',
    make: row.make || '',
    model: row.model || '',
    trim: row.trim || '',
    mileage: row.mileage ?? '',
    price_dollars: formatDollars(row.unit_price_cents),
    exterior_color: row.exterior_color || '',
    interior_color: row.interior_color || '',
    transmission: row.transmission || '',
    fuel_type: row.fuel_type || '',
    body_type: row.body_type || '',
    drivetrain: row.drivetrain || '',
    condition: row.condition || '',
    vehicle_status: row.vehicle_status || 'available',
    description_text: row.description_text || '',
    carfax_url: row.carfax_url || '',
    video_url: row.video_url || '',
    image_urls: [...(row.image_urls || [])],
    features: [...(row.features_json || [])],
    active: row.active !== false,
  }
}

// Parse an optional integer field. Empty -> null (not set); non-digits ->
// ok:false so the caller can show a friendly error before hitting the API.
function parseIntField(value) {
  const trimmed = String(value ?? '').trim()
  if (trimmed === '') return { value: null, ok: true }
  if (!/^\d+$/.test(trimmed)) return { value: null, ok: false }
  return { value: Number(trimmed), ok: true }
}

// Build the create/patch body from the form, validating client-side first
// so the obvious mistakes never round-trip. Returns { body } or { error }.
function buildPayload(form, { isEdit }) {
  const stock = form.stock_number.trim()
  if (!stock) return { error: 'Stock number is required.' }

  const exterior = form.exterior_color.trim()
  if (!exterior) return { error: 'Exterior color is required.' }

  const vin = form.vin.trim()
  if (vin && vin.length !== 17) {
    return { error: 'VIN must be exactly 17 characters (or left blank).' }
  }

  const cents = parseDollars(form.price_dollars)
  if (cents === undefined) {
    return { error: 'Price must be a valid dollar amount (or left blank).' }
  }

  const yearRes = parseIntField(form.year)
  if (!yearRes.ok) return { error: 'Year must be a whole number.' }
  if (
    yearRes.value !== null &&
    (yearRes.value < MIN_VEHICLE_YEAR || yearRes.value > MAX_VEHICLE_YEAR)
  ) {
    return {
      error: `Year must be between ${MIN_VEHICLE_YEAR} and ${MAX_VEHICLE_YEAR}.`,
    }
  }

  const mileageRes = parseIntField(form.mileage)
  if (!mileageRes.ok) return { error: 'Mileage must be a whole number.' }

  const make = form.make.trim() || null
  const model = form.model.trim() || null
  const images = form.image_urls.map((u) => u.trim()).filter(Boolean)
  const features = form.features.map((f) => f.trim()).filter(Boolean)

  const body = {
    stock_number: stock,
    exterior_color: exterior,
    vin: vin || null,
    year: yearRes.value,
    make,
    model,
    trim: form.trim.trim() || null,
    mileage: mileageRes.value,
    unit_price_cents: cents,
    interior_color: form.interior_color.trim() || null,
    transmission: form.transmission.trim() || null,
    fuel_type: form.fuel_type.trim() || null,
    body_type: form.body_type.trim() || null,
    drivetrain: form.drivetrain.trim() || null,
    condition: form.condition.trim() || null,
    vehicle_status: form.vehicle_status,
    description_text: form.description_text.trim() || null,
    carfax_url: form.carfax_url.trim() || null,
    video_url: form.video_url.trim() || null,
    image_urls: images,
    features_json: features,
    active: form.active,
  }

  if (isEdit) {
    // PATCH does not re-mirror make->designer/model->style_number or
    // exterior_color->color the way create does, so thread the compat
    // search columns ourselves to keep them aligned with the edits.
    body.designer = make
    body.style_number = model
    body.color = exterior
  }

  return { body }
}

// Editor for an ordered list of free-text strings (photo URLs, features).
// `reorder` adds up/down controls and flags the first item as the public
// thumbnail — order is meaningful for image_urls (first = public thumb).
const PHOTO_API_ORIGIN = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')

// Stored vehicle photos are origin-relative (`/api/public/media/...`) so the
// DB stays host-independent. Build an absolute src for <img> previews in the
// admin (a different origin from the API); external URLs pass through.
function photoSrc(url) {
  if (!url) return ''
  if (/^https?:\/\//.test(url)) return url
  return `${PHOTO_API_ORIGIN}${url}`
}

function StringListEditor({ label, values, onChange, placeholder, helperText, reorder }) {
  const update = (idx, val) => {
    const next = [...values]
    next[idx] = val
    onChange(next)
  }
  const add = () => onChange([...values, ''])
  const remove = (idx) => onChange(values.filter((_, i) => i !== idx))
  const move = (idx, dir) => {
    const j = idx + dir
    if (j < 0 || j >= values.length) return
    const next = [...values]
    ;[next[idx], next[j]] = [next[j], next[idx]]
    onChange(next)
  }

  return (
    <Box>
      <Typography variant="subtitle2" gutterBottom>
        {label}
      </Typography>
      <Stack spacing={1}>
        {values.length === 0 && (
          <Typography variant="caption" color="text.secondary">
            None yet.
          </Typography>
        )}
        {values.map((val, idx) => (
          <Stack key={idx} direction="row" spacing={1} alignItems="center">
            {reorder && (
              <Stack>
                <IconButton
                  size="small"
                  onClick={() => move(idx, -1)}
                  disabled={idx === 0}
                  aria-label="Move up"
                >
                  <ArrowUpwardIcon fontSize="inherit" />
                </IconButton>
                <IconButton
                  size="small"
                  onClick={() => move(idx, 1)}
                  disabled={idx === values.length - 1}
                  aria-label="Move down"
                >
                  <ArrowDownwardIcon fontSize="inherit" />
                </IconButton>
              </Stack>
            )}
            <TextField
              size="small"
              fullWidth
              value={val}
              placeholder={placeholder}
              onChange={(e) => update(idx, e.target.value)}
            />
            {reorder && idx === 0 && (
              <Chip size="small" color="primary" variant="outlined" label="Thumbnail" />
            )}
            <IconButton size="small" onClick={() => remove(idx)} aria-label="Remove">
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </Stack>
        ))}
        <Button size="small" startIcon={<AddIcon />} onClick={add} sx={{ alignSelf: 'flex-start' }}>
          Add
        </Button>
      </Stack>
      {helperText && (
        <Typography variant="caption" color="text.secondary">
          {helperText}
        </Typography>
      )}
    </Box>
  )
}

// Sortable list columns. Each carries a sort accessor; numeric columns
// compare by subtraction, the rest by localeCompare. Null/empty values
// sort last in both directions so the arrow never buries real data.
const COLUMNS = [
  { id: 'photo', label: '', sortable: false },
  {
    id: 'vehicle',
    label: 'Vehicle',
    getValue: (r) => [r.year, r.make, r.model, r.trim].filter(Boolean).join(' '),
  },
  { id: 'stock_number', label: 'Stock #', getValue: (r) => r.stock_number || '' },
  { id: 'vin', label: 'VIN', getValue: (r) => r.vin || '' },
  { id: 'color', label: 'Ext / Int', getValue: (r) => r.exterior_color || '' },
  { id: 'mileage', label: 'Mileage', align: 'right', numeric: true, getValue: (r) => r.mileage },
  { id: 'price', label: 'Price', align: 'right', numeric: true, getValue: (r) => r.unit_price_cents },
  { id: 'status', label: 'Status', getValue: (r) => r.vehicle_status || '' },
  { id: 'active', label: 'Active', sortable: false },
]

function sortRows(rows, orderBy, order) {
  const col = orderBy && COLUMNS.find((c) => c.id === orderBy)
  if (!col || !col.getValue) return rows
  const dir = order === 'asc' ? 1 : -1
  return [...rows].sort((ra, rb) => {
    const a = col.getValue(ra)
    const b = col.getValue(rb)
    const aNil = a === null || a === undefined || a === ''
    const bNil = b === null || b === undefined || b === ''
    if (aNil && bNil) return 0
    if (aNil) return 1
    if (bNil) return -1
    const base = col.numeric ? a - b : String(a).localeCompare(String(b))
    return base * dir
  })
}

function vehicleTitle(row) {
  const ymm = [row.year, row.make, row.model].filter(Boolean).join(' ')
  return ymm || row.stock_number || `Vehicle #${row.id}`
}

const RESULT_LIMIT = 500

export default function AdminVehicles() {
  const [items, setItems] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [makeFilter, setMakeFilter] = useState('')
  const [yearFilter, setYearFilter] = useState('')
  const [priceMin, setPriceMin] = useState('')
  const [priceMax, setPriceMax] = useState('')
  const [includeInactive, setIncludeInactive] = useState(false)
  const [orderBy, setOrderBy] = useState(null)
  const [order, setOrder] = useState('asc')

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(DEFAULT_FORM)
  const [actionError, setActionError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [uploadingPhoto, setUploadingPhoto] = useState(false)

  async function refresh() {
    setLoadError(null)
    try {
      const data = await listVehicles({
        q: query,
        includeInactive,
        status: statusFilter || undefined,
        limit: RESULT_LIMIT,
      })
      setItems(data)
    } catch {
      setLoadError("Couldn't load inventory.")
      setItems([])
    }
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeInactive, statusFilter])

  // Debounce the search box so each keystroke doesn't fire a request.
  useEffect(() => {
    const handle = setTimeout(refresh, 250)
    return () => clearTimeout(handle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])

  const rows = useMemo(() => items || [], [items])

  // Facets derive from the loaded rows so an option never hides itself.
  const makeOptions = useMemo(() => {
    const set = new Set()
    rows.forEach((r) => r.make && set.add(r.make))
    return Array.from(set).sort((a, b) => a.localeCompare(b))
  }, [rows])

  const yearOptions = useMemo(() => {
    const set = new Set()
    rows.forEach((r) => r.year != null && set.add(r.year))
    return Array.from(set).sort((a, b) => b - a)
  }, [rows])

  const filteredRows = useMemo(() => {
    const min = parseDollars(priceMin)
    const max = parseDollars(priceMax)
    return rows.filter((r) => {
      if (makeFilter && r.make !== makeFilter) return false
      if (yearFilter && String(r.year) !== String(yearFilter)) return false
      if (min != null && min !== undefined) {
        if (r.unit_price_cents == null || r.unit_price_cents < min) return false
      }
      if (max != null && max !== undefined) {
        if (r.unit_price_cents == null || r.unit_price_cents > max) return false
      }
      return true
    })
  }, [rows, makeFilter, yearFilter, priceMin, priceMax])

  const sortedRows = useMemo(
    () => sortRows(filteredRows, orderBy, order),
    [filteredRows, orderBy, order],
  )

  const anyFilterActive =
    statusFilter !== '' ||
    makeFilter !== '' ||
    yearFilter !== '' ||
    priceMin !== '' ||
    priceMax !== ''

  function clearFilters() {
    setStatusFilter('')
    setMakeFilter('')
    setYearFilter('')
    setPriceMin('')
    setPriceMax('')
  }

  function handleSort(colId) {
    if (orderBy === colId) {
      setOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'))
    } else {
      setOrderBy(colId)
      setOrder('asc')
    }
  }

  function openCreate() {
    setEditing(null)
    setForm(DEFAULT_FORM)
    setActionError(null)
    setDialogOpen(true)
  }

  function openEdit(row) {
    setEditing(row)
    setForm(formFromRow(row))
    setActionError(null)
    setDialogOpen(true)
  }

  async function handleSave() {
    setActionError(null)
    const { body, error } = buildPayload(form, { isEdit: !!editing })
    if (error) {
      setActionError(error)
      return
    }
    setSaving(true)
    try {
      if (editing) {
        await updateVehicle(editing.id, body)
      } else {
        await createVehicle(body)
      }
      setDialogOpen(false)
      refresh()
    } catch (err) {
      setActionError(extractApiError(err))
    } finally {
      setSaving(false)
    }
  }

  // Upload appends to the row's image_urls and persists immediately (the
  // server is the source of truth), so the form mirrors the returned list.
  // Requires an existing vehicle — there is no id to attach to before create.
  async function handlePhotoUpload(e) {
    const files = Array.from(e.target.files || [])
    e.target.value = '' // allow re-selecting the same file
    if (!files.length || !editing) return
    setActionError(null)
    setUploadingPhoto(true)
    try {
      let latest = null
      for (const file of files) {
        latest = await uploadVehiclePhoto(editing.id, file)
      }
      if (latest) setForm((f) => ({ ...f, image_urls: latest.image_urls || [] }))
    } catch (err) {
      setActionError(extractApiError(err))
    } finally {
      setUploadingPhoto(false)
    }
  }

  const dialogTitle = editing ? 'Edit vehicle' : 'Add vehicle'

  return (
    <Box>
      <Card>
        <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            justifyContent="space-between"
            alignItems={{ sm: 'center' }}
            spacing={2}
            sx={{ mb: 2 }}
          >
            <Box>
              <Typography variant="h4">Inventory</Typography>
              <Typography variant="body2" color="text.secondary">
                Vehicles for sale. Prices entered here pre-fill deal quotes and invoices.
              </Typography>
            </Box>
            <Button variant="contained" onClick={openCreate}>
              Add vehicle
            </Button>
          </Stack>

          <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ mb: 2 }}>
            <TextField
              size="small"
              label="Search"
              placeholder="Stock #, VIN, make, model, color..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              sx={{ flex: 1 }}
            />
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={includeInactive}
                  onChange={(e) => setIncludeInactive(e.target.checked)}
                />
              }
              label="Include inactive"
            />
          </Stack>

          <Stack
            direction={{ xs: 'column', md: 'row' }}
            spacing={1.5}
            alignItems={{ md: 'center' }}
            flexWrap="wrap"
            useFlexGap
            sx={{ mb: 2 }}
          >
            <FormControl size="small" sx={{ minWidth: 150 }}>
              <InputLabel>Status</InputLabel>
              <Select
                label="Status"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <MenuItem value="">All statuses</MenuItem>
                {VEHICLE_STATUSES.map((s) => (
                  <MenuItem key={s.value} value={s.value}>
                    {s.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <InputLabel>Make</InputLabel>
              <Select label="Make" value={makeFilter} onChange={(e) => setMakeFilter(e.target.value)}>
                <MenuItem value="">All makes</MenuItem>
                {makeOptions.map((m) => (
                  <MenuItem key={m} value={m}>
                    {m}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 120 }}>
              <InputLabel>Year</InputLabel>
              <Select label="Year" value={yearFilter} onChange={(e) => setYearFilter(e.target.value)}>
                <MenuItem value="">All years</MenuItem>
                {yearOptions.map((y) => (
                  <MenuItem key={y} value={y}>
                    {y}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <TextField
              size="small"
              label="Min price"
              value={priceMin}
              onChange={(e) => setPriceMin(e.target.value)}
              inputMode="decimal"
              sx={{ width: 120 }}
            />
            <TextField
              size="small"
              label="Max price"
              value={priceMax}
              onChange={(e) => setPriceMax(e.target.value)}
              inputMode="decimal"
              sx={{ width: 120 }}
            />
            {anyFilterActive && (
              <Button size="small" onClick={clearFilters}>
                Clear filters
              </Button>
            )}
          </Stack>

          {loadError && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {loadError}
            </Alert>
          )}

          {items !== null && rows.length >= RESULT_LIMIT && (
            <Alert severity="info" sx={{ mb: 2 }}>
              Showing the first {RESULT_LIMIT} vehicles. Narrow with search or filters to see more.
            </Alert>
          )}

          {items === null ? (
            <Box sx={{ p: 4, textAlign: 'center' }}>
              <CircularProgress size={20} />
            </Box>
          ) : rows.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
              No vehicles yet. Click “Add vehicle” to create your first listing.
            </Typography>
          ) : filteredRows.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
              No vehicles match these filters.{' '}
              <Button size="small" onClick={clearFilters}>
                Clear filters
              </Button>
            </Typography>
          ) : (
            <>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                {filteredRows.length} {filteredRows.length === 1 ? 'vehicle' : 'vehicles'}.
              </Typography>
              <TableContainer sx={{ maxHeight: 'calc(100vh - 360px)' }}>
                <Table
                  size="small"
                  stickyHeader
                  sx={{
                    '& .MuiTableHead-root .MuiTableCell-root': {
                      fontWeight: 700,
                      fontSize: '0.7rem',
                      letterSpacing: '0.05em',
                      textTransform: 'uppercase',
                      color: 'text.primary',
                      bgcolor: 'grey.100',
                      borderBottom: '2px solid',
                      borderColor: 'divider',
                      py: 1.25,
                      whiteSpace: 'nowrap',
                    },
                  }}
                >
                  <TableHead>
                    <TableRow>
                      {COLUMNS.map((col) => (
                        <TableCell
                          key={col.id}
                          align={col.align || 'left'}
                          sortDirection={orderBy === col.id ? order : false}
                        >
                          {col.sortable === false || !col.getValue ? (
                            col.label
                          ) : (
                            <TableSortLabel
                              active={orderBy === col.id}
                              direction={orderBy === col.id ? order : 'asc'}
                              onClick={() => handleSort(col.id)}
                            >
                              {col.label}
                            </TableSortLabel>
                          )}
                        </TableCell>
                      ))}
                      <TableCell align="right">Actions</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {sortedRows.map((row) => {
                      const meta = statusMeta(row.vehicle_status)
                      const thumb = row.image_urls?.[0]
                      return (
                        <TableRow key={row.id} hover>
                          <TableCell>
                            <Box
                              sx={{
                                width: 44,
                                height: 58,
                                borderRadius: 1,
                                bgcolor: 'grey.100',
                                backgroundImage: thumb ? `url(${thumb})` : 'none',
                                backgroundPosition: 'center',
                                backgroundSize: 'cover',
                                backgroundRepeat: 'no-repeat',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                color: 'text.disabled',
                                flexShrink: 0,
                              }}
                            >
                              {!thumb && (
                                <Typography variant="caption" sx={{ fontSize: 9 }}>
                                  No photo
                                </Typography>
                              )}
                            </Box>
                          </TableCell>
                          <TableCell>
                            <Typography variant="body2" fontWeight={600}>
                              {vehicleTitle(row)}
                            </Typography>
                            {row.trim && (
                              <Typography variant="caption" color="text.secondary">
                                {row.trim}
                              </Typography>
                            )}
                          </TableCell>
                          <TableCell sx={{ fontFamily: 'monospace' }}>
                            {row.stock_number || '—'}
                          </TableCell>
                          <TableCell sx={{ fontFamily: 'monospace', color: 'text.secondary' }}>
                            {row.vin || '—'}
                          </TableCell>
                          <TableCell>
                            <Typography variant="body2">{row.exterior_color || '—'}</Typography>
                            {row.interior_color && (
                              <Typography variant="caption" color="text.secondary">
                                {row.interior_color}
                              </Typography>
                            )}
                          </TableCell>
                          <TableCell align="right">
                            {row.mileage == null
                              ? '—'
                              : `${row.mileage.toLocaleString('en-US')} mi`}
                          </TableCell>
                          <TableCell align="right">{formatUSD(row.unit_price_cents)}</TableCell>
                          <TableCell>
                            <Chip size="small" color={meta.color} label={meta.label} />
                          </TableCell>
                          <TableCell>
                            {row.active === false ? (
                              <Chip size="small" color="warning" label="Inactive" />
                            ) : (
                              <Chip size="small" variant="outlined" label="Active" />
                            )}
                          </TableCell>
                          <TableCell align="right">
                            <Button size="small" onClick={() => openEdit(row)}>
                              Edit
                            </Button>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </TableContainer>
            </>
          )}
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>{dialogTitle}</DialogTitle>
        <DialogContent>
          <Stack spacing={2.5} sx={{ mt: 0.5 }}>
            {actionError && <Alert severity="error">{actionError}</Alert>}

            <Typography variant="subtitle2" color="text.secondary">
              Identification
            </Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                label="Stock number"
                value={form.stock_number}
                onChange={(e) => setForm({ ...form, stock_number: e.target.value })}
                required
                helperText="Internal stock number staff use to find the car."
                sx={{ flex: 1 }}
              />
              <TextField
                label="VIN"
                value={form.vin}
                onChange={(e) => setForm({ ...form, vin: e.target.value })}
                helperText="17 characters. Leave blank if not captured yet."
                sx={{ flex: 1 }}
              />
            </Stack>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <FormControl fullWidth sx={{ flex: 1 }}>
                <InputLabel>Status</InputLabel>
                <Select
                  label="Status"
                  value={form.vehicle_status}
                  onChange={(e) => setForm({ ...form, vehicle_status: e.target.value })}
                >
                  {VEHICLE_STATUSES.map((s) => (
                    <MenuItem key={s.value} value={s.value}>
                      {s.label}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <Box sx={{ flex: 1, display: 'flex', alignItems: 'center' }}>
                <FormControlLabel
                  control={
                    <Switch
                      checked={form.active}
                      onChange={(e) => setForm({ ...form, active: e.target.checked })}
                    />
                  }
                  label="Active"
                />
              </Box>
            </Stack>

            <Divider />
            <Typography variant="subtitle2" color="text.secondary">
              Vehicle
            </Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                label="Year"
                value={form.year}
                onChange={(e) => setForm({ ...form, year: e.target.value })}
                inputMode="numeric"
                sx={{ flex: 1 }}
              />
              <TextField
                label="Make"
                value={form.make}
                onChange={(e) => setForm({ ...form, make: e.target.value })}
                sx={{ flex: 1 }}
              />
              <TextField
                label="Model"
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
                sx={{ flex: 1 }}
              />
            </Stack>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                label="Trim"
                value={form.trim}
                onChange={(e) => setForm({ ...form, trim: e.target.value })}
                sx={{ flex: 1 }}
              />
              <TextField
                label="Mileage"
                value={form.mileage}
                onChange={(e) => setForm({ ...form, mileage: e.target.value })}
                inputMode="numeric"
                sx={{ flex: 1 }}
              />
              <TextField
                label="Condition"
                value={form.condition}
                onChange={(e) => setForm({ ...form, condition: e.target.value })}
                placeholder="used, new, certified"
                sx={{ flex: 1 }}
              />
            </Stack>

            <Divider />
            <Typography variant="subtitle2" color="text.secondary">
              Pricing
            </Typography>
            <TextField
              label="Cash price (USD)"
              value={form.price_dollars}
              onChange={(e) => setForm({ ...form, price_dollars: e.target.value })}
              helperText="Pre-fills deal quotes and invoices. Leave blank for no listed price."
              inputMode="decimal"
              sx={{ maxWidth: { sm: 260 } }}
            />

            <Divider />
            <Typography variant="subtitle2" color="text.secondary">
              Appearance & specs
            </Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                label="Exterior color"
                value={form.exterior_color}
                onChange={(e) => setForm({ ...form, exterior_color: e.target.value })}
                required
                sx={{ flex: 1 }}
              />
              <TextField
                label="Interior color"
                value={form.interior_color}
                onChange={(e) => setForm({ ...form, interior_color: e.target.value })}
                sx={{ flex: 1 }}
              />
            </Stack>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                label="Body type"
                value={form.body_type}
                onChange={(e) => setForm({ ...form, body_type: e.target.value })}
                placeholder="Sedan, SUV, Truck"
                sx={{ flex: 1 }}
              />
              <TextField
                label="Transmission"
                value={form.transmission}
                onChange={(e) => setForm({ ...form, transmission: e.target.value })}
                placeholder="Automatic, Manual"
                sx={{ flex: 1 }}
              />
            </Stack>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                label="Fuel type"
                value={form.fuel_type}
                onChange={(e) => setForm({ ...form, fuel_type: e.target.value })}
                placeholder="Gas, Hybrid, Electric"
                sx={{ flex: 1 }}
              />
              <TextField
                label="Drivetrain"
                value={form.drivetrain}
                onChange={(e) => setForm({ ...form, drivetrain: e.target.value })}
                placeholder="FWD, AWD, RWD, 4WD"
                sx={{ flex: 1 }}
              />
            </Stack>

            <Divider />
            <Typography variant="subtitle2" color="text.secondary">
              Details
            </Typography>
            <TextField
              label="Description"
              value={form.description_text}
              onChange={(e) => setForm({ ...form, description_text: e.target.value })}
              multiline
              minRows={2}
            />
            <StringListEditor
              label="Features"
              values={form.features}
              onChange={(features) => setForm({ ...form, features })}
              placeholder="e.g. Backup Camera"
            />

            <Divider />
            <Typography variant="subtitle2" color="text.secondary">
              Media
            </Typography>
            <Box>
              <Stack
                direction="row"
                alignItems="center"
                justifyContent="space-between"
                sx={{ mb: 1 }}
              >
                <Typography variant="subtitle2">Photos</Typography>
                <Button
                  component="label"
                  size="small"
                  variant="outlined"
                  startIcon={<PhotoCameraOutlinedIcon />}
                  disabled={!editing || uploadingPhoto}
                >
                  {uploadingPhoto ? 'Uploading…' : 'Upload'}
                  <input
                    hidden
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    multiple
                    onChange={handlePhotoUpload}
                  />
                </Button>
              </Stack>
              {!editing && (
                <Typography variant="caption" color="text.secondary">
                  Save the vehicle first, then re-open it to upload photos.
                </Typography>
              )}
              {form.image_urls.length > 0 && (
                <Stack
                  direction="row"
                  sx={{ mt: 1, flexWrap: 'wrap', gap: 1 }}
                >
                  {form.image_urls.map((u, idx) => (
                    <Box
                      key={idx}
                      sx={{ position: 'relative', width: 84, height: 84 }}
                    >
                      <Box
                        component="img"
                        src={photoSrc(u)}
                        alt={`Photo ${idx + 1}`}
                        sx={{
                          width: 84,
                          height: 84,
                          objectFit: 'cover',
                          borderRadius: 1,
                          border: '1px solid',
                          borderColor: 'divider',
                          bgcolor: 'action.hover',
                        }}
                      />
                      {idx === 0 && (
                        <Chip
                          size="small"
                          label="Thumb"
                          color="primary"
                          sx={{
                            position: 'absolute',
                            bottom: 2,
                            left: 2,
                            height: 18,
                            fontSize: 10,
                          }}
                        />
                      )}
                    </Box>
                  ))}
                </Stack>
              )}
            </Box>
            <StringListEditor
              label="Photo URLs"
              values={form.image_urls}
              onChange={(image_urls) => setForm({ ...form, image_urls })}
              placeholder="https://…/photo.jpg"
              helperText="Uploaded photos appear here. The first is the public thumbnail — use the arrows to reorder. You can also paste external URLs."
              reorder
            />
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                label="Carfax URL"
                value={form.carfax_url}
                onChange={(e) => setForm({ ...form, carfax_url: e.target.value })}
                sx={{ flex: 1 }}
              />
              <TextField
                label="Video URL"
                value={form.video_url}
                onChange={(e) => setForm({ ...form, video_url: e.target.value })}
                sx={{ flex: 1 }}
              />
            </Stack>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)} disabled={saving}>
            Cancel
          </Button>
          <Button variant="contained" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
