import { useEffect, useState } from 'react'
import { InputAdornment, TextField } from '@mui/material'

import { formatDollars, parseDollars } from '../utils/money'

// CurrencyInput stores integer cents but lets the user type dollars. The
// `value` prop is the canonical cents value (number or null). `onChange` is
// called with cents on every commit (blur or Enter). While the input is
// focused, the user sees their raw text so they can edit freely without
// re-formatting interference.
export default function CurrencyInput({
  value,
  onChange,
  size = 'small',
  fullWidth = false,
  disabled = false,
  placeholder = '0.00',
  label,
  helperText,
  error,
  inputProps,
  sx,
}) {
  const [draft, setDraft] = useState(formatDollars(value))
  const [focused, setFocused] = useState(false)
  const [invalid, setInvalid] = useState(false)

  // Reflect external value changes back into the input when not focused.
  useEffect(() => {
    if (!focused) {
      setDraft(formatDollars(value))
      setInvalid(false)
    }
  }, [value, focused])

  function commit(text) {
    const cents = parseDollars(text)
    if (cents === undefined) {
      setInvalid(true)
      return
    }
    setInvalid(false)
    if (cents !== value) onChange(cents)
  }

  return (
    <TextField
      size={size}
      fullWidth={fullWidth}
      disabled={disabled}
      label={label}
      placeholder={placeholder}
      helperText={invalid ? 'Enter a valid amount.' : helperText}
      error={invalid || !!error}
      value={draft}
      onChange={(e) => {
        setDraft(e.target.value)
        if (invalid) setInvalid(false)
      }}
      onFocus={() => setFocused(true)}
      onBlur={() => {
        setFocused(false)
        commit(draft)
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          commit(draft)
          e.target.blur()
        }
      }}
      InputProps={{
        startAdornment: <InputAdornment position="start">$</InputAdornment>,
      }}
      inputProps={{
        inputMode: 'decimal',
        style: { textAlign: 'right' },
        ...inputProps,
      }}
      sx={sx}
    />
  )
}
