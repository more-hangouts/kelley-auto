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
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Tabs,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material'

import {
  createCatalogItem,
  listCatalogDesigners,
  searchCatalog,
  updateCatalogItem,
} from '../services/api'
import CatalogDetailModal from '../components/CatalogDetailModal'
// Admin catalog (products) page, mounted at /products (top-level nav).
//
// Staff think in three buckets — dresses, accessories, alterations — but
// the underlying catalog_items.category enum still uses the granular
// values quince_gown / bridal_gown / formal_gown / accessory / service
// because the customer-copy renderer in catalog_service falls back to
// the category label when a row has no house_name. This page maps the
// three UI buckets onto those enum values via the `group=` query
// param and a Quince/Bridal/Formal sub-selector when the user is
// creating a Dress.

const GROUP_TABS = [
  { value: 'dress', label: 'Dresses' },
  { value: 'accessory', label: 'Accessories' },
  { value: 'addon', label: 'Alterations' },
  { value: '', label: 'All' },
]

const DRESS_SUBTYPES = [
  { value: 'quince_gown', label: 'Quince' },
  { value: 'bridal_gown', label: 'Bridal' },
  { value: 'formal_gown', label: 'Formal' },
]

const CATEGORY_DISPLAY = {
  quince_gown: 'Quince gown',
  bridal_gown: 'Bridal gown',
  formal_gown: 'Formal gown',
  accessory: 'Accessory',
  service: 'Alteration',
}

const VIEW_MODES = [
  { value: 'browse', label: 'Gallery' },
  { value: 'table', label: 'List' },
]

// Color-family classifier. Free-text color names (often compound, e.g.
// "Black/Red Rose", "Mint/Blush") are matched against keyword sets, so
// one row can belong to several families — and SHOULD: a "Mint/Blush"
// gown is findable under both Greens and Pinks. This map is the single
// place to fix a misclassification; later the scraper can persist a
// real color_family so we stop re-deriving on the client.
const COLOR_FAMILIES = [
  { id: 'white', label: 'White / Ivory', keywords: ['white', 'ivory', 'diamond', 'pearl', 'snow', 'cream'] },
  // NOTE: bare 'rose' is intentionally excluded — it collides with
  // floral/appliqué names ("Black/Red Rose") and "Rose Gold" (a
  // metallic), producing false Pinks. Real rose-pinks read as blush/
  // pink/mauve in practice.
  { id: 'pink', label: 'Pinks', keywords: ['pink', 'blush', 'orchid', 'fuchsia', 'magenta', 'mauve'] },
  { id: 'red', label: 'Reds', keywords: ['red', 'crimson', 'wine', 'burgundy', 'scarlet', 'cranberry'] },
  { id: 'purple', label: 'Purples', keywords: ['purple', 'lavender', 'lilac', 'plum', 'eggplant', 'violet', 'royal'] },
  { id: 'blue', label: 'Blues', keywords: ['blue', 'navy', 'sky', 'teal', 'aqua', 'turquoise', 'sapphire'] },
  { id: 'green', label: 'Greens', keywords: ['green', 'sage', 'mint', 'moss', 'emerald', 'olive', 'forest'] },
  { id: 'yellow', label: 'Yellows', keywords: ['yellow', 'champagne', 'lemon', 'canary', 'butter'] },
  { id: 'neutral', label: 'Neutrals', keywords: ['nude', 'beige', 'taupe', 'tan', 'sand', 'mocha', 'brown', 'camel'] },
  { id: 'metallic', label: 'Metallics', keywords: ['gold', 'silver', 'bronze', 'copper', 'metallic', 'platinum', 'pewter'] },
  { id: 'black', label: 'Black / Gray', keywords: ['black', 'charcoal', 'gray', 'grey', 'onyx', 'smoke'] },
]

function colorFamiliesFor(row) {
  const text = (row.color || '').toLowerCase()
  if (!text) return []
  // Whole-word match (slashes/spaces are word boundaries) so a keyword
  // never matches inside an unrelated word — e.g. 'tan' in "titanium".
  return COLOR_FAMILIES.filter((fam) =>
    fam.keywords.some((kw) => new RegExp(`\\b${kw}\\b`).test(text)),
  ).map((fam) => fam.id)
}

// Table-mode columns. Each carries a sort accessor so the header
// doubles as a sort button (TableSortLabel below). `numeric` columns
// compare by subtraction; the rest by localeCompare. The Actions
// column is intentionally absent — there's nothing meaningful to sort
// it by. Status sorts by a derived rank so inactive/sample rows
// cluster together: active+sample highest, inactive lowest.
const TABLE_COLUMNS = [
  { id: 'internal_sku', label: 'Internal SKU', getValue: (r) => r.internal_sku },
  { id: 'public_code', label: 'BVX code', getValue: (r) => r.public_code },
  {
    id: 'title',
    label: 'Title / Description',
    getValue: (r) => r.product_title || r.house_name || '',
  },
  {
    id: 'designer',
    label: 'Designer · Style',
    getValue: (r) => [r.designer, r.style_number].filter(Boolean).join(' '),
  },
  { id: 'color', label: 'Color', getValue: (r) => r.color || '' },
  {
    id: 'category',
    label: 'Category',
    getValue: (r) => CATEGORY_DISPLAY[r.category] || r.category || '',
  },
  {
    id: 'price',
    label: 'Price',
    align: 'right',
    numeric: true,
    getValue: (r) => r.unit_price_cents,
  },
  {
    id: 'status',
    label: 'Status',
    numeric: true,
    getValue: (r) => (r.active === false ? 0 : 2) + (r.is_sample ? 1 : 0),
  },
]

function sortCatalogRows(rows, orderBy, order) {
  const col = orderBy && TABLE_COLUMNS.find((c) => c.id === orderBy)
  if (!col) return rows
  const dir = order === 'asc' ? 1 : -1
  return [...rows].sort((ra, rb) => {
    const a = col.getValue(ra)
    const b = col.getValue(rb)
    // Empty/null values sort last in BOTH directions, so the arrow
    // never buries real data under a wall of blanks.
    const aNil = a === null || a === undefined || a === ''
    const bNil = b === null || b === undefined || b === ''
    if (aNil && bNil) return 0
    if (aNil) return 1
    if (bNil) return -1
    const base = col.numeric ? a - b : String(a).localeCompare(String(b))
    return base * dir
  })
}

const DEFAULT_FORM = {
  internal_sku: '',
  bucket: 'dress',
  dress_subtype: 'quince_gown',
  product_title: '',
  designer: '',
  style_number: '',
  color: '',
  house_name: '',
  description_text: '',
  unit_price_dollars: '',
  is_sample: false,
  active: true,
}

function centsToDollars(cents) {
  if (cents === null || cents === undefined) return ''
  return (cents / 100).toFixed(2)
}

function dollarsToCents(value) {
  // Empty string -> null (no price). Anything non-numeric is rejected
  // upstream by the API's ge=0 guard; this helper only does the unit
  // conversion. Strip $ and commas so a paste from a price sheet still
  // round-trips correctly.
  const trimmed = String(value).trim().replace(/[$,]/g, '')
  if (!trimmed) return null
  const parsed = Number(trimmed)
  if (!Number.isFinite(parsed) || parsed < 0) return NaN
  return Math.round(parsed * 100)
}

function categoryFromForm(form) {
  if (form.bucket === 'dress') return form.dress_subtype
  if (form.bucket === 'accessory') return 'accessory'
  if (form.bucket === 'addon') return 'service'
  return form.dress_subtype
}

function bucketFromCategory(category) {
  if (category === 'accessory') return 'accessory'
  if (category === 'service') return 'addon'
  return 'dress'
}

function dressSubtypeFromCategory(category) {
  if (category === 'bridal_gown' || category === 'formal_gown') return category
  return 'quince_gown'
}

function styleGroupKey(row) {
  const designer = (row.designer || '').trim().toLowerCase()
  const style = (row.style_number || '').trim().toLowerCase()
  if (designer || style) return `${designer}|${style}`

  const title = (row.product_title || row.house_name || '').trim().toLowerCase()
  const category = (row.category || '').trim().toLowerCase()
  return `${title}|${category}|${row.id}`
}

function styleGroupTitle(row) {
  return row.product_title || row.house_name || row.style_number || row.internal_sku
}

function variantLabel(row) {
  return row.color || row.internal_sku || row.public_code
}

function groupCatalogRows(rows) {
  const groups = new Map()

  rows.forEach((row) => {
    const key = styleGroupKey(row)
    const group = groups.get(key) || {
      key,
      primary: row,
      variants: [],
    }

    group.variants.push(row)
    if (!group.primary?.image_urls?.length && row.image_urls?.length) {
      group.primary = row
    }
    groups.set(key, group)
  })

  return Array.from(groups.values()).map((group) => ({
    ...group,
    variants: group.variants.sort((a, b) =>
      variantLabel(a).localeCompare(variantLabel(b)),
    ),
  }))
}

export default function AdminCatalog() {
  const [items, setItems] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [tab, setTab] = useState('dress')
  const [query, setQuery] = useState('')
  const [includeInactive, setIncludeInactive] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  // All color variants of the style currently open in the editor, so the
  // dialog can offer a color dropdown to switch which variant is edited.
  const [editingVariants, setEditingVariants] = useState([])
  const [detailGroup, setDetailGroup] = useState(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [form, setForm] = useState(DEFAULT_FORM)
  const [saving, setSaving] = useState(false)
  const [viewMode, setViewMode] = useState('browse')
  const [orderBy, setOrderBy] = useState(null)
  const [order, setOrder] = useState('asc')
  const [colorFamilyFilter, setColorFamilyFilter] = useState([])
  const [samplesOnly, setSamplesOnly] = useState(false)
  const [vendor, setVendor] = useState('')
  const [vendorOptions, setVendorOptions] = useState([])

  // The catalog can exceed the API's per-request cap, so vendor is a
  // SERVER-side filter (each vendor fits under the cap on its own) and
  // the vendor list comes from the DB, not the loaded page.
  const RESULT_LIMIT = 500

  async function refresh() {
    setLoadError(null)
    try {
      const data = await searchCatalog({
        q: query,
        group: tab || undefined,
        designer: vendor || undefined,
        includeInactive,
        limit: RESULT_LIMIT,
      })
      setItems(Array.isArray(data) ? data : [])
    } catch {
      setLoadError("Couldn't load catalog.")
      setItems([])
    }
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, includeInactive, vendor])

  useEffect(() => {
    listCatalogDesigners()
      .then(setVendorOptions)
      .catch(() => setVendorOptions([]))
  }, [])

  // Debounce the search input so each keystroke doesn't fire a list.
  useEffect(() => {
    const handle = setTimeout(refresh, 250)
    return () => clearTimeout(handle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])

  function openCreate() {
    setEditing(null)
    setEditingVariants([])
    setForm({
      ...DEFAULT_FORM,
      bucket: tab && tab !== '' ? tab : 'dress',
    })
    setActionError(null)
    setDialogOpen(true)
  }

  function openDetail(group) {
    setDetailGroup(group)
    setDetailOpen(true)
  }

  function handleDetailEdit(row) {
    setDetailOpen(false)
    openEdit(row, detailGroup?.variants)
  }

  function formFromRow(row) {
    return {
      internal_sku: row.internal_sku,
      bucket: bucketFromCategory(row.category),
      dress_subtype: dressSubtypeFromCategory(row.category),
      product_title: row.product_title || '',
      designer: row.designer || '',
      style_number: row.style_number || '',
      color: row.color || '',
      house_name: row.house_name || '',
      description_text: row.description_text || '',
      unit_price_dollars: centsToDollars(row.unit_price_cents),
      is_sample: !!row.is_sample,
      active: row.active !== false,
    }
  }

  // Open the editor on one color row. `variants` (the style's full color
  // set) powers the color dropdown so staff can switch which color they're
  // editing — e.g. to mark a specific color as the floor sample — without
  // closing the dialog and clicking a different card chip.
  function openEdit(row, variants = null) {
    setEditing(row)
    setEditingVariants(variants && variants.length ? variants : [row])
    setForm(formFromRow(row))
    setActionError(null)
    setDialogOpen(true)
  }

  // Switch the editor to a different color of the same style. Loads that
  // variant's values (incl. its floor-sample flag) and retargets Save.
  function selectEditVariant(variantId) {
    const variant = editingVariants.find((v) => v.id === variantId)
    if (!variant) return
    setEditing(variant)
    setForm(formFromRow(variant))
    setActionError(null)
  }

  async function handleSave() {
    setActionError(null)
    const cents = dollarsToCents(form.unit_price_dollars)
    if (Number.isNaN(cents)) {
      setActionError('Price must be a non-negative number.')
      return
    }
    if (!form.color.trim()) {
      setActionError('Color is required.')
      return
    }
    const category = categoryFromForm(form)
    setSaving(true)
    try {
      if (editing) {
        await updateCatalogItem(editing.id, {
          category,
          product_title: form.product_title || null,
          designer: form.designer || null,
          style_number: form.style_number || null,
          color: form.color,
          house_name: form.house_name || null,
          description_text: form.description_text || null,
          unit_price_cents: cents,
          is_sample: form.is_sample,
          active: form.active,
        })
      } else {
        if (!form.internal_sku.trim()) {
          setActionError('Internal SKU is required.')
          setSaving(false)
          return
        }
        await createCatalogItem({
          internal_sku: form.internal_sku.trim(),
          color: form.color,
          category,
          product_title: form.product_title || null,
          designer: form.designer || null,
          style_number: form.style_number || null,
          house_name: form.house_name || null,
          description_text: form.description_text || null,
          unit_price_cents: cents,
          is_sample: form.is_sample,
          active: form.active,
        })
      }
      setDialogOpen(false)
      refresh()
    } catch (err) {
      const detail = err?.response?.data?.detail
      if (err?.response?.status === 409) {
        setActionError('That internal SKU is already in use.')
      } else if (typeof detail === 'string') {
        setActionError(detail)
      } else if (detail?.code) {
        setActionError(`Couldn't save: ${detail.code}.`)
      } else {
        setActionError("Couldn't save the product.")
      }
    } finally {
      setSaving(false)
    }
  }

  const rows = useMemo(() => items || [], [items])

  // Facets derive from the unfiltered rows so a chip never hides itself
  // by being the only thing keeping its own option on screen. Designer
  // chips only appear once there's more than one vendor to choose from.
  const familyChips = useMemo(() => {
    const present = new Set()
    rows.forEach((row) => colorFamiliesFor(row).forEach((f) => present.add(f)))
    return COLOR_FAMILIES.filter(
      (f) => present.has(f.id) || colorFamilyFilter.includes(f.id),
    )
  }, [rows, colorFamilyFilter])

  const filteredRows = useMemo(() => {
    return rows.filter((row) => {
      if (samplesOnly && !row.is_sample) return false
      if (colorFamilyFilter.length) {
        const fams = colorFamiliesFor(row)
        if (!fams.some((f) => colorFamilyFilter.includes(f))) return false
      }
      return true
    })
  }, [rows, samplesOnly, colorFamilyFilter])

  const styleGroups = useMemo(() => groupCatalogRows(filteredRows), [filteredRows])
  const sortedRows = useMemo(
    () => sortCatalogRows(filteredRows, orderBy, order),
    [filteredRows, orderBy, order],
  )

  const anyFilterActive =
    colorFamilyFilter.length > 0 || vendor !== '' || samplesOnly

  function toggleInArray(value, setter) {
    setter((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value],
    )
  }

  function clearFilters() {
    setColorFamilyFilter([])
    setVendor('')
    setSamplesOnly(false)
  }

  function handleSort(colId) {
    if (orderBy === colId) {
      setOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'))
    } else {
      setOrderBy(colId)
      setOrder('asc')
    }
  }
  const isDress = form.bucket === 'dress'
  const dialogTitle = editing ? 'Edit product' : 'Add product'

  const headerNote = useMemo(() => {
    switch (tab) {
      case 'dress':
        return 'Gowns by Quince / Bridal / Formal.'
      case 'accessory':
        return 'Accessories like veils, jewelry, headpieces.'
      case 'addon':
        return 'Alteration and tailoring services.'
      default:
        return 'Every product across all buckets.'
    }
  }, [tab])

  return (
    <Box>
      <Card>
      <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ sm: 'center' }} spacing={2} sx={{ mb: 2 }}>
          <Box>
            <Typography variant="h4">Products</Typography>
            <Typography variant="body2" color="text.secondary">
              {headerNote} Prices entered here pre-fill quote and invoice lines.
            </Typography>
          </Box>
          <Button variant="contained" onClick={openCreate}>
            Add product
          </Button>
        </Stack>

        <Tabs
          value={tab}
          onChange={(_, next) => setTab(next)}
          sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
        >
          {GROUP_TABS.map((t) => (
            <Tab key={t.value || 'all'} value={t.value} label={t.label} />
          ))}
        </Tabs>

        <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ mb: 2 }}>
          <TextField
            size="small"
            label="Search"
            placeholder="SKU, BVX code, designer, color, title..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            sx={{ flex: 1 }}
          />
          <Stack direction="row" spacing={1.5} alignItems="center" justifyContent={{ xs: 'space-between', md: 'flex-end' }}>
            <ToggleButtonGroup
              exclusive
              size="small"
              value={viewMode}
              onChange={(_, next) => {
                if (next) setViewMode(next)
              }}
              aria-label="Catalog view"
            >
              {VIEW_MODES.map((mode) => (
                <ToggleButton key={mode.value} value={mode.value}>
                  {mode.label}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={includeInactive}
                  onChange={(e) => setIncludeInactive(e.target.checked)}
                />
              }
              label="Include inactive"
              sx={{ mr: 0 }}
            />
          </Stack>
        </Stack>

        {loadError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {loadError}
          </Alert>
        )}

        {(rows.length > 0 || vendorOptions.length > 1) && (
          <Stack spacing={1} sx={{ mb: 2 }}>
            {familyChips.length > 0 && (
              <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
                <Typography variant="caption" color="text.secondary" sx={{ width: 56, flexShrink: 0 }}>
                  Color
                </Typography>
                {familyChips.map((fam) => {
                  const active = colorFamilyFilter.includes(fam.id)
                  return (
                    <Chip
                      key={fam.id}
                      size="small"
                      label={fam.label}
                      color={active ? 'primary' : 'default'}
                      variant={active ? 'filled' : 'outlined'}
                      onClick={() => toggleInArray(fam.id, setColorFamilyFilter)}
                    />
                  )
                })}
              </Stack>
            )}
            {vendorOptions.length > 1 && (
              <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
                <Typography variant="caption" color="text.secondary" sx={{ width: 56, flexShrink: 0 }}>
                  Vendor
                </Typography>
                {vendorOptions.map((opt) => {
                  const active = vendor === opt.designer
                  return (
                    <Chip
                      key={opt.designer}
                      size="small"
                      label={`${opt.designer} (${opt.count})`}
                      color={active ? 'primary' : 'default'}
                      variant={active ? 'filled' : 'outlined'}
                      onClick={() => setVendor(active ? '' : opt.designer)}
                    />
                  )
                })}
              </Stack>
            )}
            <Stack direction="row" spacing={1.5} alignItems="center">
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={samplesOnly}
                    onChange={(e) => setSamplesOnly(e.target.checked)}
                  />
                }
                label="Floor samples only"
              />
              {anyFilterActive && (
                <Button size="small" onClick={clearFilters}>
                  Clear filters
                </Button>
              )}
            </Stack>
          </Stack>
        )}

        {items !== null && rows.length >= RESULT_LIMIT && !vendor && (
          <Alert severity="info" sx={{ mb: 2 }}>
            Showing the first {RESULT_LIMIT} products. Pick a vendor to see all of
            that vendor's products.
          </Alert>
        )}

        {items === null ? (
          <Box sx={{ p: 4, textAlign: 'center' }}>
            <CircularProgress size={20} />
          </Box>
        ) : rows.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            No products match.
          </Typography>
        ) : filteredRows.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            No products match these filters. <Button size="small" onClick={clearFilters}>Clear filters</Button>
          </Typography>
        ) : viewMode === 'browse' ? (
          <Stack spacing={1.5}>
            <Typography variant="body2" color="text.secondary">
              Showing {styleGroups.length} styles from {filteredRows.length} product rows.
            </Typography>
            <Box
              sx={{
                display: 'grid',
                gridTemplateColumns: {
                  xs: '1fr',
                  sm: 'repeat(2, minmax(0, 1fr))',
                  lg: 'repeat(3, minmax(0, 1fr))',
                },
                gap: 2,
              }}
            >
              {styleGroups.map((group) => {
                const row = group.primary
                const imageUrl = row.image_urls?.[0]
                const colors = group.variants.map((variant) => variant.color).filter(Boolean)
                const priceValues = group.variants
                  .map((variant) => variant.unit_price_cents)
                  .filter((price) => price !== null && price !== undefined)
                const hasInactive = group.variants.some((variant) => variant.active === false)
                const hasSample = group.variants.some((variant) => variant.is_sample)
                const priceLabel = priceValues.length
                  ? `$${centsToDollars(Math.min(...priceValues))}`
                  : 'No price'

                return (
                  <Box
                    key={group.key}
                    sx={{
                      border: 1,
                      borderColor: 'divider',
                      borderRadius: 1,
                      overflow: 'hidden',
                      bgcolor: 'background.paper',
                    }}
                  >
                    <Box
                      onClick={() => openDetail(group)}
                      role="button"
                      aria-label={`View ${styleGroupTitle(row)}`}
                      sx={{
                        aspectRatio: '4 / 5',
                        bgcolor: 'grey.100',
                        backgroundImage: imageUrl ? `url(${imageUrl})` : 'none',
                        backgroundPosition: 'center',
                        backgroundRepeat: 'no-repeat',
                        backgroundSize: 'cover',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        color: 'text.secondary',
                        cursor: 'pointer',
                        transition: 'opacity 120ms ease',
                        '&:hover': { opacity: 0.92 },
                      }}
                    >
                      {!imageUrl && (
                        <Typography variant="caption">No image</Typography>
                      )}
                    </Box>
                    <Stack spacing={1.25} sx={{ p: 1.75 }}>
                      <Box>
                        <Stack direction="row" spacing={1} justifyContent="space-between" alignItems="flex-start">
                          <Box
                            onClick={() => openDetail(group)}
                            sx={{ minWidth: 0, cursor: 'pointer' }}
                          >
                            <Typography
                              variant="subtitle1"
                              fontWeight={700}
                              noWrap
                              sx={{ '&:hover': { textDecoration: 'underline' } }}
                            >
                              {styleGroupTitle(row)}
                            </Typography>
                            <Typography variant="body2" color="text.secondary" noWrap>
                              {[row.designer, row.style_number].filter(Boolean).join(' · ') || row.public_code}
                            </Typography>
                          </Box>
                          <Typography variant="body2" fontWeight={700} sx={{ whiteSpace: 'nowrap' }}>
                            {priceLabel}
                          </Typography>
                        </Stack>
                        <Typography variant="caption" color="text.secondary">
                          {CATEGORY_DISPLAY[row.category] || row.category}
                        </Typography>
                      </Box>

                      <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                        {group.variants.slice(0, 5).map((variant) => (
                          <Chip
                            key={variant.id}
                            size="small"
                            label={variant.color || variant.internal_sku}
                            onClick={() => openEdit(variant, group.variants)}
                          />
                        ))}
                        {colors.length > 5 && (
                          <Chip size="small" variant="outlined" label={`+${colors.length - 5} more`} />
                        )}
                      </Stack>

                      <Stack direction="row" spacing={0.75} alignItems="center" justifyContent="space-between">
                        <Stack direction="row" spacing={0.5}>
                          {hasInactive && <Chip size="small" color="warning" label="inactive" />}
                          {hasSample && <Chip size="small" color="success" label="sample" />}
                        </Stack>
                        <Button size="small" onClick={() => openEdit(row, group.variants)}>
                          Edit
                        </Button>
                      </Stack>
                    </Stack>
                  </Box>
                )
              })}
            </Box>
          </Stack>
        ) : (
          <TableContainer sx={{ maxHeight: 'calc(100vh - 320px)' }}>
          <Table
            size="small"
            stickyHeader
            sx={{
              // Phase 1 header polish: a defined, sticky header so the
              // column labels stay legible while scrolling long lists.
              // bgcolor on head cells is required for stickyHeader —
              // without it, scrolling rows show through the header.
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
                {TABLE_COLUMNS.map((col) => (
                  <TableCell
                    key={col.id}
                    align={col.align || 'left'}
                    sortDirection={orderBy === col.id ? order : false}
                  >
                    <TableSortLabel
                      active={orderBy === col.id}
                      direction={orderBy === col.id ? order : 'asc'}
                      onClick={() => handleSort(col.id)}
                    >
                      {col.label}
                    </TableSortLabel>
                  </TableCell>
                ))}
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {sortedRows.map((row) => (
                <TableRow key={row.id} hover>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{row.internal_sku}</TableCell>
                  <TableCell sx={{ fontFamily: 'monospace', color: 'text.secondary' }}>{row.public_code}</TableCell>
                  <TableCell>
                    <Typography variant="body2">{row.product_title || row.house_name || '—'}</Typography>
                    {row.description_text && (
                      <Typography variant="caption" color="text.secondary">
                        {row.description_text.slice(0, 80)}
                        {row.description_text.length > 80 ? '...' : ''}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    {[row.designer, row.style_number].filter(Boolean).join(' · ') || '—'}
                  </TableCell>
                  <TableCell>{row.color || '—'}</TableCell>
                  <TableCell>{CATEGORY_DISPLAY[row.category] || row.category}</TableCell>
                  <TableCell align="right">
                    {row.unit_price_cents === null || row.unit_price_cents === undefined
                      ? <Typography variant="caption" color="text.secondary">no price</Typography>
                      : `$${centsToDollars(row.unit_price_cents)}`}
                  </TableCell>
                  <TableCell>
                    <Stack direction="row" spacing={0.5}>
                      {row.active === false && <Chip size="small" color="warning" label="inactive" />}
                      {row.is_sample && <Chip size="small" color="success" label="sample" />}
                    </Stack>
                  </TableCell>
                  <TableCell align="right">
                    <Button size="small" onClick={() => openEdit(row)}>Edit</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </TableContainer>
        )}
      </CardContent>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{dialogTitle}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 0.5 }}>
            {actionError && <Alert severity="error">{actionError}</Alert>}
            <TextField
              label="Internal SKU"
              value={form.internal_sku}
              onChange={(e) => setForm({ ...form, internal_sku: e.target.value })}
              disabled={!!editing}
              helperText={editing ? 'Internal SKU is immutable once created.' : 'The vendor SKU staff use to search and reorder.'}
              required
            />
            <FormControl fullWidth>
              <InputLabel>Type</InputLabel>
              <Select
                label="Type"
                value={form.bucket}
                onChange={(e) => setForm({ ...form, bucket: e.target.value })}
              >
                <MenuItem value="dress">Dress</MenuItem>
                <MenuItem value="accessory">Accessory</MenuItem>
                <MenuItem value="addon">Alteration</MenuItem>
              </Select>
            </FormControl>
            {isDress && (
              <FormControl fullWidth>
                <InputLabel>Dress kind</InputLabel>
                <Select
                  label="Dress kind"
                  value={form.dress_subtype}
                  onChange={(e) => setForm({ ...form, dress_subtype: e.target.value })}
                >
                  {DRESS_SUBTYPES.map((s) => (
                    <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
            <TextField
              label="Product title"
              value={form.product_title}
              onChange={(e) => setForm({ ...form, product_title: e.target.value })}
            />
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                label="Designer"
                value={form.designer}
                onChange={(e) => setForm({ ...form, designer: e.target.value })}
                sx={{ flex: 1 }}
              />
              <TextField
                label="Style number"
                value={form.style_number}
                onChange={(e) => setForm({ ...form, style_number: e.target.value })}
                sx={{ flex: 1 }}
              />
            </Stack>
            {editing && editingVariants.length > 1 && (
              <FormControl fullWidth>
                <InputLabel>Select color to edit</InputLabel>
                <Select
                  label="Select color to edit"
                  value={editing.id}
                  onChange={(e) => selectEditVariant(e.target.value)}
                >
                  {editingVariants.map((v) => {
                    const isFloorSample =
                      v.id === editing.id ? form.is_sample : v.is_sample
                    return (
                      <MenuItem key={v.id} value={v.id}>
                        {v.color || v.internal_sku}
                        {isFloorSample ? ' — floor sample' : ''}
                      </MenuItem>
                    )
                  })}
                </Select>
              </FormControl>
            )}
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                label="Color"
                value={form.color}
                onChange={(e) => setForm({ ...form, color: e.target.value })}
                required
                sx={{ flex: 1 }}
              />
              <TextField
                label="House name"
                value={form.house_name}
                onChange={(e) => setForm({ ...form, house_name: e.target.value })}
                sx={{ flex: 1 }}
              />
            </Stack>
            <TextField
              label="Description"
              value={form.description_text}
              onChange={(e) => setForm({ ...form, description_text: e.target.value })}
              multiline
              minRows={2}
            />
            <TextField
              label="Price (USD)"
              value={form.unit_price_dollars}
              onChange={(e) => setForm({ ...form, unit_price_dollars: e.target.value })}
              helperText="Pre-fills quote and invoice lines. Leave blank for no default price."
              inputMode="decimal"
            />
            <Stack direction="row" spacing={3}>
              <FormControlLabel
                control={
                  <Switch
                    checked={form.active}
                    onChange={(e) => setForm({ ...form, active: e.target.checked })}
                  />
                }
                label="Active"
              />
              {isDress && (
                <FormControlLabel
                  control={
                    <Switch
                      checked={form.is_sample}
                      onChange={(e) => setForm({ ...form, is_sample: e.target.checked })}
                    />
                  }
                  label="Floor sample"
                />
              )}
            </Stack>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)} disabled={saving}>Cancel</Button>
          <Button variant="contained" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>
      <CatalogDetailModal
        group={detailGroup}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        onEdit={handleDetailEdit}
      />
      </Card>
    </Box>
  )
}
