import { useEffect, useMemo, useState } from 'react'
import {
  Autocomplete,
  Box,
  CircularProgress,
  FormControlLabel,
  Stack,
  Switch,
  Tab,
  Tabs,
  TextField,
  Typography,
} from '@mui/material'

import { searchCatalog } from '../services/api'

// UI category buckets that map onto the catalog_items.category enum
// via the API's `group=` param. Kept in lockstep with CATEGORY_GROUPS
// in services/catalog_service.py.
const GROUP_TABS = [
  { value: '', label: 'All' },
  { value: 'dress', label: 'Dresses' },
  { value: 'accessory', label: 'Accessories' },
  { value: 'addon', label: 'Alterations' },
]

// Catalog SKU obfuscation Phase 3 line-item picker.
//
// The picker is the staff entry point for attaching a catalog row to
// an invoice/quote line. It shows the real internal_sku as the
// primary identifier so staff search and reorder using the vendor's
// actual SKU; the public BVX-NNNNN code lives as small secondary
// text so staff can still answer "what code is on this customer's
// invoice?"
//
// Internal_sku and the rest of the catalog identifiers never leak to
// customer surfaces by Phase 4 construction; this component is the
// only place those fields are typed/visible at the line-edit level
// and runs only inside the staff-authenticated drawer.

const DEBOUNCE_MS = 200

export default function CatalogPicker({ value, onChange, disabled }) {
  // `value` is either null (non-catalog line) or a catalog snapshot
  // object {id, internal_sku, public_code, designer, style_number,
  // color, house_name, category, product_title}. The picker hands
  // back the same shape (or null) so the editor never has to know
  // about catalog row internals.
  //
  // The Autocomplete is fully controlled on both `value` and
  // `inputValue`. `inputValue` mirrors the selected row's
  // ``internal_sku`` whenever a value is set so the picker shows
  // ``MORI-4080000-...`` after a selection or after the editor
  // hydrates a saved line. While the dropdown is open the user
  // freely overwrites the input with their search query; on
  // selection or blur, MUI fires `onInputChange` with reason
  // ``"reset"`` to push the picked option's label into the input.
  const [input, setInput] = useState(() => value?.internal_sku || '')
  const [debounced, setDebounced] = useState('')
  const [includeInactive, setIncludeInactive] = useState(false)
  // Phase 6 sample filter: undefined = both (default), true = floor
  // samples only, false = non-samples only. Staff use this to answer
  // "do we have one of these on the floor?" without opening another
  // tool.
  const [sampleFilter, setSampleFilter] = useState(undefined)
  // UI bucket: '' (all), 'dress', 'accessory', 'addon'. Default to
  // 'all' so existing picker behavior is unchanged for a staff
  // member who hasn't tapped a tab; the tabs are a way to narrow
  // when staff already know what they're inserting.
  const [group, setGroup] = useState('')
  const [options, setOptions] = useState([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)

  // Sync the input with the externally-controlled `value` whenever it
  // changes. This is what makes a catalog-backed line that was just
  // loaded from the server display its internal_sku in the picker
  // input on first paint, instead of staying blank until the user
  // clicks the field.
  useEffect(() => {
    setInput(value?.internal_sku || '')
  }, [value])

  // Debounce the user's keystrokes so each character does not fire a
  // search call. 200ms is short enough that the picker still feels
  // live but long enough to avoid one round-trip per keystroke when
  // staff paste a full SKU.
  useEffect(() => {
    const handle = setTimeout(() => setDebounced(input), DEBOUNCE_MS)
    return () => clearTimeout(handle)
  }, [input])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    // When the dropdown is open and the input still equals the picked
    // option's label (typical right after selection), treat it as the
    // idle list so we don't hammer the API with `q=MORI-XXX` and only
    // surface the row that's already selected.
    const queryTerm =
      value && debounced === value.internal_sku ? '' : debounced
    searchCatalog({
      q: queryTerm,
      includeInactive,
      isSample: sampleFilter,
      group: group || undefined,
      limit: 25,
    })
      .then((rows) => {
        if (cancelled) return
        setOptions(rows)
      })
      .catch(() => {
        if (cancelled) return
        setOptions([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, debounced, includeInactive, sampleFilter, group, value])

  // When the editor reloads a saved catalog-backed line, `value` is
  // already a snapshot and there's nothing to fetch. Keep that
  // selection visible without forcing a re-search.
  const stableValue = useMemo(() => value || null, [value])

  return (
    <Stack spacing={1}>
      <Tabs
        value={group}
        onChange={(_, next) => setGroup(next)}
        variant="scrollable"
        scrollButtons="auto"
        sx={{
          minHeight: 32,
          '& .MuiTab-root': { minHeight: 32, py: 0.5, fontSize: '0.75rem' },
        }}
      >
        {GROUP_TABS.map((t) => (
          <Tab key={t.value || 'all'} value={t.value} label={t.label} disabled={disabled} />
        ))}
      </Tabs>
      <Autocomplete
        size="small"
        disabled={disabled}
        open={open}
        onOpen={() => setOpen(true)}
        onClose={() => setOpen(false)}
        value={stableValue}
        // The picker is single-select. `value` is the snapshot or null.
        onChange={(_, next) => onChange(next)}
        inputValue={input}
        onInputChange={(_, next) => {
          // Honor every reason MUI emits, including ``"reset"`` (fired
          // on selection, on clear, and when the input loses focus
          // without a selection). Reset is what pushes the picked
          // row's ``internal_sku`` into the input so staff see the
          // selected SKU instead of their stale search term.
          setInput(next)
        }}
        options={options}
        loading={loading}
        // The Autocomplete needs a stable key for option vs value
        // identity comparison; without this it warns "value not in
        // options" on every reload.
        isOptionEqualToValue={(opt, val) => opt?.id === val?.id}
        getOptionLabel={(opt) => (opt ? opt.internal_sku : '')}
        filterOptions={(x) => x}
        renderInput={(params) => (
          <TextField
            {...params}
            label="Catalog item"
            placeholder="Type SKU, BVX code, designer, color..."
            InputProps={{
              ...params.InputProps,
              endAdornment: (
                <>
                  {loading ? <CircularProgress color="inherit" size={16} /> : null}
                  {params.InputProps.endAdornment}
                </>
              ),
            }}
          />
        )}
        renderOption={(props, opt) => (
          <Box component="li" {...props} key={opt.id}>
            <Stack sx={{ width: '100%' }}>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {opt.internal_sku}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {opt.public_code}
                </Typography>
                {opt.is_sample && (
                  <Typography variant="caption" color="success.main">
                    sample
                  </Typography>
                )}
                {opt.active === false && (
                  <Typography variant="caption" color="warning.main">
                    inactive
                  </Typography>
                )}
              </Stack>
              <Typography variant="caption" color="text.secondary">
                {[
                  opt.product_title,
                  opt.color,
                  opt.designer,
                  opt.style_number,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </Typography>
            </Stack>
          </Box>
        )}
        sx={{ minWidth: 280 }}
      />
      <Stack direction="row" spacing={2} sx={{ flexWrap: 'wrap' }}>
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={includeInactive}
              onChange={(e) => setIncludeInactive(e.target.checked)}
              disabled={disabled}
            />
          }
          label={
            <Typography variant="caption" color="text.secondary">
              Include inactive
            </Typography>
          }
        />
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={sampleFilter === true}
              onChange={(e) =>
                // Three-state toggle compressed to two: off = both,
                // on = floor samples only. The "non-samples only"
                // case is rare in the picker (you usually want to see
                // samples or everything) so we don't expose it here;
                // admin browsing has the dedicated `is_sample=false`
                // query for that.
                setSampleFilter(e.target.checked ? true : undefined)
              }
              disabled={disabled}
            />
          }
          label={
            <Typography variant="caption" color="text.secondary">
              Floor samples only
            </Typography>
          }
        />
      </Stack>
    </Stack>
  )
}
