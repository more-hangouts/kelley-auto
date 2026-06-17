// Customer-facing labels for the Boutique Experience pill values, mirrored
// from widgets/bellas-fit-prep-tool.js so staff see the same words the
// customer picked rather than the raw enum codes.
export const STYLE_LABELS = {
  ball_gown: 'Ball gown',
  a_line: 'A-line',
  mermaid: 'Mermaid / fitted',
  two_piece: 'Two-piece',
  unsure: 'Not sure yet',
}

export const BACK_LABELS = {
  corset: 'Corset',
  zipper: 'Zipper',
  unsure: 'Not sure',
}

export const BUDGET_LABELS = {
  under_1000: 'Under $1,000',
  '1000_1500': '$1,000-$1,500',
  '1500_2000': '$1,500-$2,000',
  '2000_plus': '$2,000+',
  unsure: 'Not sure yet',
}

// "Size 8-10" if both bounds are present and differ, "Size 8" if only one
// bound or both are equal, null if nothing usable to render.
export function formatSizeRange(profile) {
  if (!profile) return null
  const lo = profile.estimated_size_low
  const hi = profile.estimated_size_high
  if (lo == null && hi == null) return null
  if (lo != null && hi != null && lo !== hi) return `Size ${lo}-${hi}`
  return `Size ${lo ?? hi}`
}
