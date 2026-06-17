function normalize(name) {
  return (name || '').trim().toLowerCase()
}

export function getCelebrantName(event) {
  if (!event?.participants?.length) return null
  const quince = event.participants.find(
    (p) => p.role === 'quinceanera',
  )
  return quince?.display_name || null
}

export function celebrantDiffersFromContact(event) {
  const celebrant = getCelebrantName(event)
  const contact = event?.primary_contact?.display_name
  if (!celebrant || !contact) return false
  return normalize(celebrant) !== normalize(contact)
}
