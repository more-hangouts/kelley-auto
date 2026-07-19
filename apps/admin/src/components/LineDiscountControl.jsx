import { useEffect, useState } from 'react'
import { Box, Button, IconButton, Slider, Stack, Tooltip, Typography } from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import LocalOfferOutlinedIcon from '@mui/icons-material/LocalOfferOutlined'

import { formatUSD } from '../utils/money'

// Banker-rounding mirror of the editors' shared helper. Repeated locally
// instead of imported so this component is self-contained — both the
// invoice and quote editors already have their own copies for line math.
function bankRound(value) {
  const sign = value < 0 ? -1 : 1
  const abs = Math.abs(value)
  const floor = Math.floor(abs)
  const diff = abs - floor
  if (diff > 0.5 || (diff === 0.5 && floor % 2 === 1)) {
    return sign * (floor + 1)
  }
  return sign * floor
}

function deriveInitialPercent(line) {
  const qty = parseFloat(line.quantity || '0') || 0
  const unit = Number(line.unit_price_cents || 0)
  const cents = Number(line.discount_cents || 0)
  const gross = qty * unit
  if (gross <= 0 || cents <= 0) return 0
  // Best-effort reverse: if the editor wrote `cents = round(gross * pct /
  // 100)` last time the slider was used, this gives the same integer
  // percent back. Manually entered cents that don't land on a clean
  // percent will round to the nearest integer.
  return Math.min(50, Math.max(0, Math.round((cents / gross) * 100)))
}

// Per-line discount slider. Replaces the always-visible cents input
// from before Phase 2b. Default state hides itself behind an "Apply
// discount" button; on click, expands a 0-50% slider with a live
// "$X off" preview. Slider writes `line.discount_cents` (still
// absolute cents in storage) on every change.
export default function LineDiscountControl({ line, onChange, disabled = false }) {
  const cents = Number(line.discount_cents || 0)
  const qty = parseFloat(line.quantity || '0') || 0
  const unit = Number(line.unit_price_cents || 0)

  const [expanded, setExpanded] = useState(cents > 0)
  const [percent, setPercent] = useState(() => deriveInitialPercent(line))

  // Keep the slider thumb in sync when the row is hydrated from the
  // server (re-open editor) or when discount_cents is cleared from the
  // outside (parent flips it to 0). We re-derive only when the line's
  // cents value disagrees with what our slider would currently write.
  useEffect(() => {
    if (cents === 0 && expanded === false) {
      if (percent !== 0) setPercent(0)
      return
    }
    if (cents > 0 && !expanded) {
      setExpanded(true)
    }
    const expected = bankRound((qty * unit * percent) / 100)
    if (cents !== expected) {
      setPercent(deriveInitialPercent(line))
    }
    // We intentionally only re-derive when discount_cents itself
    // changes externally; mid-drag the parent's cents matches our
    // slider so this effect bails out via the `expected` check.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cents])

  const handleApply = () => {
    setExpanded(true)
    setPercent(0)
    if (cents !== 0) onChange({ discount_cents: 0 })
  }

  const handleRemove = () => {
    setExpanded(false)
    setPercent(0)
    if (cents !== 0) onChange({ discount_cents: 0 })
  }

  const handleChange = (_event, value) => {
    const pct = Math.min(50, Math.max(0, Number(value) || 0))
    if (pct === 0) {
      setExpanded(false)
      setPercent(0)
      if (cents !== 0) onChange({ discount_cents: 0 })
      return
    }
    setPercent(pct)
    const newCents = bankRound((qty * unit * pct) / 100)
    if (newCents !== cents) onChange({ discount_cents: newCents })
  }

  if (!expanded) {
    return (
      <Box>
        <Button
          size="small"
          onClick={handleApply}
          disabled={disabled || qty <= 0 || unit <= 0}
          startIcon={<LocalOfferOutlinedIcon fontSize="small" />}
          sx={{ textTransform: 'none' }}
        >
          Apply discount
        </Button>
      </Box>
    )
  }

  return (
    <Stack
      direction={{ xs: 'column', md: 'row' }}
      spacing={1.5}
      alignItems={{ xs: 'stretch', md: 'center' }}
      sx={{
        backgroundColor: 'action.hover',
        borderRadius: 1,
        px: 1.5,
        py: 1,
      }}
    >
      <Typography variant="caption" color="text.secondary" sx={{ minWidth: 64 }}>
        Discount
      </Typography>
      <Box sx={{ flex: 1, minWidth: 180, px: 1 }}>
        <Slider
          value={percent}
          min={0}
          max={50}
          step={1}
          marks={[
            { value: 0, label: '0%' },
            { value: 25, label: '25%' },
            { value: 50, label: '50%' },
          ]}
          onChange={handleChange}
          disabled={disabled}
          aria-label="line discount percent"
          valueLabelDisplay="auto"
          valueLabelFormat={(v) => `${v}%`}
          size="small"
        />
      </Box>
      <Box sx={{ minWidth: 110, textAlign: 'right' }}>
        <Typography variant="body2" sx={{ fontWeight: 500 }}>
          {percent}% off
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {formatUSD(cents)} off
        </Typography>
      </Box>
      <Tooltip title="Remove discount">
        <span>
          <IconButton
            size="small"
            onClick={handleRemove}
            disabled={disabled}
            aria-label="remove line discount"
          >
            <CloseIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
    </Stack>
  )
}
