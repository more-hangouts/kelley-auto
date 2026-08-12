export const BUDGET_OPTIONS = [
  'Under $1k',
  '$1k-$2k',
  '$2k-$4k',
  '$4k-$6k',
  '$6k+',
  'Not sure yet',
]

export const VEHICLE_TYPE_OPTIONS = [
  'Car',
  'SUV',
  'Minivan',
  'Truck / Work Van',
]

export const FINANCING_OPTIONS = [
  'National lender',
  'In-house financing',
  'Cash',
  'Not sure yet',
]

export function emptyWalkInLeadForm() {
  return {
    pickedContactId: null,
    pickedDisplayName: '',
    first_name: '',
    last_name: '',
    phone: '',
    email: '',
    buyer_is_different: false,
    buyer_first_name: '',
    buyer_last_name: '',
    sales_credit_user_id: '',
    booking_context: 'walk_in',
    walk_in_source: '',
    walk_in_source_detail: '',
    current_vehicle: '',
    desired_vehicle_type: '',
    budget_range: '',
    financing_preference: '',
    notes: '',
  }
}

export function trimOrNull(value) {
  const t = (value || '').trim()
  return t === '' ? null : t
}

export function defaultDealName(first, last) {
  const base = (first || '').trim()
  if (!base) return ''
  const surname = (last || '').trim()
  return `${surname ? `${base} ${surname}` : base} - Lead`
}

export function composeWalkInNotes(form) {
  const lines = [
    ['Currently driving', form.current_vehicle],
    ['Looking for', form.desired_vehicle_type],
    ['Financing preference', form.financing_preference],
  ]
    .map(([label, value]) => {
      const cleaned = trimOrNull(value)
      return cleaned ? `${label}: ${cleaned}` : null
    })
    .filter(Boolean)

  const note = trimOrNull(form.notes)
  if (note) {
    if (lines.length) lines.push('')
    lines.push(note)
  }

  return lines.length ? lines.join('\n') : null
}

export function walkInBuyerName(form) {
  if (form.buyer_is_different) {
    return {
      first: (form.buyer_first_name || '').trim(),
      last: trimOrNull(form.buyer_last_name),
    }
  }
  return {
    first:
      (form.first_name || '').trim() ||
      (form.pickedDisplayName || '').trim().split(/\s+/)[0] ||
      '',
    last: trimOrNull(form.last_name),
  }
}

export function canSaveWalkInLead(form) {
  const hasCustomerName = Boolean(
    trimOrNull(form.first_name) ||
      trimOrNull(form.last_name) ||
      trimOrNull(form.pickedDisplayName),
  )
  const hasBuyerName = form.buyer_is_different
    ? Boolean(trimOrNull(form.buyer_first_name))
    : hasCustomerName
  return hasCustomerName && hasBuyerName && Boolean(trimOrNull(form.phone))
}
