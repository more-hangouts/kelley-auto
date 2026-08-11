// The walk-in intake sheet, as data.
//
// Kelley's reps worked off a printed form for years: salesperson, customer
// name and phone, then five questions asked in order while the customer stood
// at the counter. This module is that sheet — the option lists, the empty
// form, the "can we save yet" rule, and the payload shape — shared by the
// admin New lead dialog and the rep Add walk-in dialog so the two surfaces
// can never ask the same question two different ways.
//
// Every option value here is a slug that matches a CHECK constraint from
// migration 109 (and WALK_IN_SOURCE_OPTIONS in ./leadOrigin for the origin
// bucket). Labels are display copy and can be reworded freely; values cannot,
// because reports group on them.

export const BUDGET_OPTIONS = [
  'Under $1k',
  '$1k–$2k',
  '$2k–$4k',
  '$4k–$6k',
  '$6k+',
]

// Question 3 on the sheet. Four buckets, rendered as pills rather than a
// dropdown — a rep mid-conversation should be able to hit the answer in one
// tap without a menu opening over the customer's name.
export const VEHICLE_TYPE_OPTIONS = [
  { value: 'car', label: 'Car' },
  { value: 'suv', label: 'SUV' },
  { value: 'minivan', label: 'Minivan' },
  { value: 'truck_work_van', label: 'Truck / work van' },
]

// Question 5. The sheet's wording was "National Lender (BANK) or in-house
// financing?", so the labels keep the bank parenthetical — it is how the reps
// say it out loud.
export const FINANCING_OPTIONS = [
  { value: 'national_lender', label: 'National lender (bank)' },
  { value: 'in_house', label: 'In-house financing' },
  { value: 'cash', label: 'Paying cash' },
]

const VEHICLE_TYPE_LABELS = Object.fromEntries(
  VEHICLE_TYPE_OPTIONS.map((o) => [o.value, o.label]),
)
const FINANCING_LABELS = Object.fromEntries(
  FINANCING_OPTIONS.map((o) => [o.value, o.label]),
)

// Unknown values render as the raw slug rather than disappearing — a value
// the server accepted but this build has not heard of should still be
// visible. Same rule as walkInSourceLabel in ./leadOrigin.
export function vehicleTypeLabel(value) {
  if (!value) return null
  return VEHICLE_TYPE_LABELS[value] || value
}

export function financingLabel(value) {
  if (!value) return null
  return FINANCING_LABELS[value] || value
}

export function emptyWalkInLeadForm() {
  return {
    // Set only when staff pick a search result. The server still dedupes on
    // phone, so this is a UI convenience (it back-fills the name), not an
    // identity the payload carries.
    pickedContactId: null,
    pickedDisplayName: '',
    first_name: '',
    last_name: '',
    phone: '',
    email: '',
    // Off by default: the person at the counter is the buyer in nearly every
    // walk-in. The checkbox exists for co-signers and parent-buys-for-kid.
    buyer_is_different: false,
    buyer_first_name: '',
    buyer_last_name: '',
    // In person, not phone — this dialog is opened for a walk-in far more
    // often than for a call, and pre-selecting saves a tap on the common path.
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

/**
 * Who the deal is for. Defaults to the person standing at the counter; only
 * diverges when staff explicitly tick "buyer is a different person".
 *
 * Falls back to splitting the picked contact's display name so choosing an
 * existing customer and saving immediately still names the buyer.
 */
export function walkInBuyerName(form) {
  if (form.buyer_is_different) {
    return {
      first: (form.buyer_first_name || '').trim(),
      last: trimOrNull(form.buyer_last_name),
    }
  }
  const first = (form.first_name || '').trim()
  const last = trimOrNull(form.last_name)
  if (first || last) return { first, last }

  const picked = (form.pickedDisplayName || '').trim().split(/\s+/).filter(Boolean)
  return {
    first: picked[0] || '',
    last: picked.length > 1 ? picked.slice(1).join(' ') : null,
  }
}

/**
 * Name + phone is the whole gate. Everything below it on the sheet is a
 * conversation aid: a rep who gets pulled away mid-question must still be
 * able to save what they have, because a half-filled lead in the CRM beats a
 * complete one on a sticky note.
 */
export function canSaveWalkInLead(form) {
  const buyer = walkInBuyerName(form)
  const hasName = Boolean(
    trimOrNull(form.first_name) ||
      trimOrNull(form.last_name) ||
      trimOrNull(form.pickedDisplayName),
  )
  return hasName && Boolean(buyer.first) && Boolean(trimOrNull(form.phone))
}

/**
 * Build the POST body for /api/walk-in-leads and /api/sales/walk-ins.
 *
 * Two things are deliberately null rather than staff-supplied:
 *
 *   - `display_name` — the server composes it from first + last for a new
 *     contact and never touches an existing one. Asking staff for an
 *     "optional override" was pure system jargon at the counter.
 *   - `event_name` — the server names the deal after the buyer. Staff should
 *     not have to invent a title for a conversation they are still having.
 *
 * `event_date` stays null too: that column is the Bella's-era party date and
 * a vehicle deal has no equivalent.
 *
 * `owner_user_id` is deliberately null as well. Lead ownership at Kelley is
 * the admin staff's, and the server already resolves it to whoever filed the
 * lead. The salesperson picked in the form is commission credit, which rides
 * in `sales_credit_user_id` and never moves ownership — see migration 110.
 */
export function buildWalkInLeadPayload(form, { salesCreditUserId = null } = {}) {
  const buyer = walkInBuyerName(form)
  return {
    contact: {
      first_name: trimOrNull(form.first_name),
      last_name: trimOrNull(form.last_name),
      display_name: null,
      email: trimOrNull(form.email),
      phone: (form.phone || '').trim(),
    },
    event: {
      celebrant_first_name: buyer.first,
      celebrant_last_name: buyer.last,
      event_name: null,
      event_date: null,
      owner_user_id: null,
      walk_in_source: trimOrNull(form.walk_in_source),
      walk_in_source_detail: trimOrNull(form.walk_in_source_detail),
      sales_credit_user_id: salesCreditUserId,
    },
    enrichment: {
      budget_range: trimOrNull(form.budget_range),
      notes: trimOrNull(form.notes),
      // Migration 109 — real columns, not prose folded into notes. These are
      // the reporting axes ("how many walk-ins wanted in-house financing?"),
      // which is the whole reason they stopped living in the notes blob.
      current_vehicle: trimOrNull(form.current_vehicle),
      desired_vehicle_type: trimOrNull(form.desired_vehicle_type),
      financing_preference: trimOrNull(form.financing_preference),
    },
    booking_context: form.booking_context,
  }
}

/**
 * Server error codes → something a salesperson can act on. Shared by both
 * dialogs; the sales dialog layers its assignment-specific codes on top.
 */
export function describeWalkInLeadError(err) {
  const status = err?.response?.status
  const detail = err?.response?.data?.detail
  const messages = {
    invalid_phone:
      'That phone number doesn’t look right. Use a 10-digit number like (210) 555-0142.',
    phone_required: 'Phone number is required.',
    contact_name_required: 'Enter the customer’s name.',
    celebrant_first_name_required: 'Enter the buyer’s first name.',
    invalid_walk_in_source: 'Pick one of the options for how they heard about us.',
    walk_in_source_detail_too_long: 'Shorten that to 200 characters or less.',
    current_vehicle_too_long:
      'Shorten what they’re driving to 120 characters or less.',
    invalid_desired_vehicle_type:
      'That vehicle type isn’t recognized. Reload the page and try again.',
    invalid_financing_preference:
      'That financing option isn’t recognized. Reload the page and try again.',
    invalid_sales_credit_user_id:
      'That salesperson is no longer active. Pick someone else.',
  }
  if (status === 422 && messages[detail]) return messages[detail]
  if (status === 401 || status === 403) {
    return 'You don’t have permission to create leads.'
  }
  if (typeof detail === 'string') return detail
  return err?.message || 'Could not save the lead. Try again.'
}
