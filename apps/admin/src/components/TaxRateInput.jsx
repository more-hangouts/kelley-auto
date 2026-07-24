import { useEffect, useState } from 'react'
import { Box, TextField, Typography } from '@mui/material'
import { formatTaxRateForInput, parseTaxRate } from '../utils/money'

// Tax-rate input that holds a draft string while the user types and
// only commits to the parent on blur. The parent receives a canonical
// decimal-ratio string (e.g. "0.08250"); the field shows the percent
// number ("8.25") with the % glyph rendered outside the box.
export default function TaxRateInput({
  value,
  onChange,
  disabled,
  size = 'small',
  label,
  placeholder,
  sx,
  inputSx,
}) {
  const [draft, setDraft] = useState(formatTaxRateForInput(value))

  // Re-sync draft whenever the prop value changes externally (e.g. a
  // quote loads, addLine seeds a default). React skips this effect when
  // the string is unchanged, so mid-typing rerenders don't clobber the
  // user's input.
  useEffect(() => {
    setDraft(formatTaxRateForInput(value))
  }, [value])

  const commit = () => {
    if (draft.trim() === '') {
      if (value !== '0') onChange('0')
      setDraft('0')
      return
    }
    const parsed = parseTaxRate(draft)
    if (parsed === undefined) {
      setDraft(formatTaxRateForInput(value))
      return
    }
    if (parsed !== value) onChange(parsed)
    setDraft(formatTaxRateForInput(parsed))
  }

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0, ...sx }}>
      <TextField
        size={size}
        label={label}
        placeholder={placeholder}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        disabled={disabled}
        sx={{ flex: 1, minWidth: 0, ...inputSx }}
        InputLabelProps={label ? { shrink: true } : undefined}
      />
      <Typography variant="body2" color="text.secondary">
        %
      </Typography>
    </Box>
  )
}
