// Money helpers used by every cents-in/dollars-out surface (invoice editor,
// invoice list, business profile defaults, future portal). Centralized here
// so rounding and formatting decisions stay consistent with the backend's
// `Decimal + ROUND_HALF_EVEN` rule.

const USD = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
})

// Format integer cents → "$1,234.56". Null and undefined render as an em
// dash placeholder; the editor uses this for read-only displays.
export function formatUSD(cents) {
  if (cents == null || Number.isNaN(cents)) return '—'
  return USD.format(cents / 100)
}

// Format without the currency symbol — useful inside an input that already
// has a leading "$" decoration.
export function formatDollars(cents) {
  if (cents == null || Number.isNaN(cents)) return ''
  return (cents / 100).toFixed(2)
}

// Parse a user-typed dollar string into integer cents.
//   - returns null on empty input
//   - returns undefined when the input is not a valid currency value
//   - banker's rounding doesn't apply at the input boundary; we use
//     standard half-away-from-zero for typed values since the user is the
//     source of truth and we don't want $1.005 to silently become $1.00.
export function parseDollars(input) {
  if (input == null) return null
  const cleaned = String(input).replace(/[$,\s]/g, '')
  if (cleaned === '') return null
  if (!/^-?\d+(\.\d{1,2})?$/.test(cleaned)) return undefined
  const dollars = parseFloat(cleaned)
  if (!Number.isFinite(dollars)) return undefined
  return Math.round(dollars * 100)
}

// Parse a tax rate string like "8.25%" or "0.0825" into a Decimal-friendly
// string in [0, 1). Returns undefined on invalid input.
export function parseTaxRate(input) {
  if (input == null) return null
  const raw = String(input).trim()
  if (raw === '') return null
  const hasPercent = raw.endsWith('%')
  const numeric = raw.replace('%', '').trim()
  if (!/^\d+(\.\d+)?$/.test(numeric)) return undefined
  const value = parseFloat(numeric)
  if (!Number.isFinite(value) || value < 0) return undefined
  // "8.25%" or bare "8.25" → 8.25%. Bare values < 1 ("0.0825") are decimal ratios.
  const ratio = (hasPercent || value >= 1) ? value / 100 : value
  if (ratio >= 1) return undefined
  return ratio.toFixed(5)
}

// Format a decimal tax_rate ("0.08250") as a percent display ("8.25%").
export function formatTaxRate(rate) {
  if (rate == null) return '0%'
  const num = typeof rate === 'string' ? parseFloat(rate) : rate
  if (!Number.isFinite(num) || num === 0) return '0%'
  return `${(num * 100).toFixed(2).replace(/\.?0+$/, '')}%`
}

// Same as formatTaxRate but without the trailing "%" — for inputs that
// render the % glyph outside the field rather than inside the value.
export function formatTaxRateForInput(rate) {
  if (rate == null) return '0'
  const num = typeof rate === 'string' ? parseFloat(rate) : rate
  if (!Number.isFinite(num) || num === 0) return '0'
  return (num * 100).toFixed(2).replace(/\.?0+$/, '')
}
