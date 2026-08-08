// Staff-entered lead origin — the walk-in / phone-in attribution the rep
// records at the counter (migration 104). Shared by the admin New lead
// dialog and the rep Add walk-in dialog so both surfaces offer exactly the
// same buckets; the server validates against the same list.
//
// The bucket is deliberately coarse and stable — it has to stay groupable
// for years — while the detail field carries the part that changes weekly
// ("Facebook video", "Instagram reel"). That split is why there is one
// `social_media` option rather than one per platform.
export const WALK_IN_SOURCE_OPTIONS = [
  { value: 'social_media', label: 'Social media' },
  { value: 'drive_by', label: 'Drive-by' },
  { value: 'referral', label: 'Referral' },
  { value: 'repeat_customer', label: 'Repeat customer' },
  { value: 'google_search', label: 'Google search' },
  { value: 'website', label: 'Website' },
  { value: 'other', label: 'Other' },
]

const LABELS = Object.fromEntries(
  WALK_IN_SOURCE_OPTIONS.map((o) => [o.value, o.label]),
)

// Unknown values render as the raw string rather than disappearing — a
// value the server accepted but this build has not heard of should still
// be visible to staff.
export function walkInSourceLabel(value) {
  if (!value) return null
  return LABELS[value] || value
}

// Which platform or post. Encouraged for social_media (that is the whole
// point of the bucket) and useful for referral ("sent by John"); for the
// self-evident buckets it stays optional and unobtrusive.
export const SOURCE_DETAIL_LABEL = 'Platform or post'

export const SOURCE_DETAIL_PLACEHOLDER = {
  social_media: 'Facebook video, Instagram reel, TikTok, Marketplace…',
  referral: 'Who sent them?',
  google_search: 'What did they search for?',
  website: 'Which page or vehicle?',
  other: 'How did they find us?',
}

// Contexts offered at lead capture. The wider appointment vocabulary also
// has 'existing_customer' / 'admin' / 'other', but a new lead only ever
// arrives one of these two ways — and only the in-person one records an
// arrival.
export const LEAD_CONTEXT_OPTIONS = [
  { value: 'walk_in', label: 'In person' },
  { value: 'phone_call', label: 'Phone call' },
]
