import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'

// Phase 7: stacked order-level discounts. Replaces the single Discount
// selector with an array editor that supports multiple presets + custom
// rows on the same invoice/quote. Combined cap is 50%; the server
// rejects with `combined_discount_too_high` if exceeded.
//
// Each row in `value` is one of:
//
//   { kind: 'preset', preset_id: 'moonlight', label: 'Moonlight Ballroom', percent: 10 }
//   { kind: 'custom', percent: '5' }
//
// `label` on preset rows is the snapshotted label hydrated from the
// server. The editor never mutates it — staff are picking the LIVE
// preset id; the snapshot happens server-side at write time.

const COMBINED_CAP = 50

export default function OrderDiscountsControl({
  value,
  onChange,
  presets,
  disabled = false,
}) {
  const rows = value || []
  const activePresets = (presets || []).filter((p) => p.active)

  const combinedPct = rows.reduce(
    (sum, row) => sum + (Number(row.percent) || 0),
    0,
  )
  const overCap = combinedPct > COMBINED_CAP

  const updateRow = (idx, patch) => {
    onChange(rows.map((row, i) => (i === idx ? { ...row, ...patch } : row)))
  }

  const removeRow = (idx) => {
    onChange(rows.filter((_, i) => i !== idx))
  }

  const addPreset = () => {
    // Default the new row to the first active preset that's not already
    // on the stack so picking from the dropdown is the common path.
    const used = new Set(rows.filter((r) => r.kind === 'preset').map((r) => r.preset_id))
    const candidate = activePresets.find((p) => !used.has(p.id)) || activePresets[0]
    if (candidate) {
      onChange([
        ...rows,
        {
          kind: 'preset',
          preset_id: candidate.id,
          label: candidate.label,
          percent: Number(candidate.percent),
        },
      ])
    } else {
      onChange([...rows, { kind: 'custom', percent: '' }])
    }
  }

  const onPresetChange = (idx, presetId) => {
    if (presetId === '__custom__') {
      updateRow(idx, {
        kind: 'custom',
        preset_id: undefined,
        label: 'Custom',
        percent: '',
      })
      return
    }
    const preset = activePresets.find((p) => p.id === presetId)
    if (!preset) return
    updateRow(idx, {
      kind: 'preset',
      preset_id: presetId,
      label: preset.label,
      percent: Number(preset.percent),
    })
  }

  return (
    <Stack spacing={1}>
      {rows.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No discount applied.
        </Typography>
      ) : (
        rows.map((row, idx) => (
          <Stack
            key={idx}
            direction={{ xs: 'column', sm: 'row' }}
            spacing={1}
            alignItems={{ xs: 'stretch', sm: 'center' }}
          >
            <TextField
              select
              size="small"
              label="Discount"
              value={row.kind === 'custom' ? '__custom__' : row.preset_id}
              onChange={(e) => onPresetChange(idx, e.target.value)}
              disabled={disabled}
              sx={{ minWidth: 220 }}
            >
              {activePresets.map((p) => (
                <MenuItem key={p.id} value={p.id}>
                  {p.label} ({p.percent}%)
                </MenuItem>
              ))}
              {/* Surface a preset that's referenced but no longer
                  active so re-saving an inherited record does not
                  break the dropdown. */}
              {row.kind === 'preset' &&
                row.preset_id &&
                !activePresets.some((p) => p.id === row.preset_id) && (
                  <MenuItem value={row.preset_id}>
                    {row.label} ({row.percent}%) (inactive)
                  </MenuItem>
                )}
              <MenuItem value="__custom__">Custom %</MenuItem>
            </TextField>
            {row.kind === 'custom' && (
              <TextField
                size="small"
                label="Percent"
                value={row.percent === undefined ? '' : String(row.percent)}
                onChange={(e) =>
                  updateRow(idx, { percent: e.target.value })
                }
                disabled={disabled}
                sx={{ width: 140 }}
                inputProps={{ inputMode: 'decimal' }}
                InputProps={{
                  endAdornment: <Typography variant="body2">%</Typography>,
                }}
              />
            )}
            {row.kind === 'custom' && (
              <TextField
                size="small"
                label="Label"
                value={row.label || ''}
                onChange={(e) => updateRow(idx, { label: e.target.value })}
                disabled={disabled}
                placeholder="Custom"
                sx={{ flex: 1, minWidth: 120 }}
              />
            )}
            <Box sx={{ flexGrow: 1 }} />
            <Tooltip title="Remove this discount">
              <span>
                <IconButton
                  size="small"
                  onClick={() => removeRow(idx)}
                  disabled={disabled}
                  aria-label="remove discount"
                >
                  <DeleteOutlineIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        ))
      )}
      <Stack
        direction="row"
        spacing={2}
        alignItems="center"
        sx={{ pt: 0.5 }}
      >
        <Button
          size="small"
          startIcon={<AddIcon />}
          onClick={addPreset}
          disabled={disabled}
        >
          Add discount
        </Button>
        {rows.length > 0 && (
          <Typography
            variant="caption"
            color={overCap ? 'error.main' : 'text.secondary'}
          >
            Combined: {combinedPct}% / {COMBINED_CAP}%
          </Typography>
        )}
      </Stack>
    </Stack>
  )
}
