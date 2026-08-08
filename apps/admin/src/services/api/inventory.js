import api from './client'

// Returns up to `limit` catalog rows matching `q`. The picker calls
// this on each keystroke (debounced upstream) and on open with q=''
// for the idle list. include_inactive surfaces retired rows when the
// staff toggles "include inactive." isSample = true narrows to floor
// samples (Phase 6 toggle), false hides them, undefined includes both.
export async function searchCatalog({
  q = '',
  includeInactive = false,
  isSample,
  group,
  designer,
  limit = 25,
} = {}) {
  const params = { limit }
  if (q && q.trim()) params.q = q.trim()
  if (includeInactive) params.include_inactive = true
  if (isSample === true) params.is_sample = true
  if (isSample === false) params.is_sample = false
  if (group) params.group = group
  if (designer) params.designer = designer
  const { data } = await api.get('/catalog', { params })
  return data
}

// Distinct designers + counts, for the admin Products vendor filter.
// Server-sourced so vendors past the per-request row cap still appear.
export async function listCatalogDesigners() {
  const { data } = await api.get('/catalog/designers')
  return Array.isArray(data) ? data : []
}

// Admin catalog CRUD. The list/search path above is shared with the
// editor's CatalogPicker; these two are admin-only writes used by
// the AdminCatalog page.
export async function createCatalogItem(body) {
  const { data } = await api.post('/catalog', body)
  return data
}

export async function updateCatalogItem(catalogItemId, patch) {
  const { data } = await api.patch(`/catalog/${catalogItemId}`, patch)
  return data
}

// Price decomposition for the catalog detail view: package vs base item
// and what each removable package item saves. Derived prices only — the
// backend never returns wholesale cost or the multiplier here.
export async function getCatalogPriceBreakdown(catalogItemId) {
  const { data } = await api.get(`/catalog/${catalogItemId}/price-breakdown`)
  return data
}

// ---------------------------------------------------------------------------
// Vehicles (Day 2 — Kelley Autoplex inventory)
// ---------------------------------------------------------------------------
//
// Vehicles are `catalog_items` rows with `is_vehicle=true` (migration 085).
// These wrappers reuse the same /catalog endpoints the product catalog uses
// but scope reads to the vehicle group AND re-gate on the `is_vehicle`
// discriminator client-side — per the Day 1 rule, `is_vehicle` is the only
// reliable "this is a car" signal, so a backfilled non-vehicle row that
// happens to carry category='vehicle' or a vehicle_status can never leak
// onto the vehicle surface.

// Lists vehicles. `group=vehicle` filters on category server-side (and
// applies in search mode too when `q` is set); we then filter on
// `is_vehicle` so the discriminator — not the category — is the final
// gate. `status` filters by vehicle_status client-side (the list route
// has no status param).
export async function listVehicles({
  q = '',
  includeInactive = false,
  status,
  limit = 500,
} = {}) {
  const params = { group: 'vehicle', limit }
  if (q && q.trim()) params.q = q.trim()
  if (includeInactive) params.include_inactive = true
  const { data } = await api.get('/catalog', { params })
  const rows = Array.isArray(data) ? data : []
  let vehicles = rows.filter((row) => row.is_vehicle === true)
  if (status) vehicles = vehicles.filter((row) => row.vehicle_status === status)
  return vehicles
}

// Create a vehicle. Always stamps `is_vehicle: true` so the caller can
// never forget it. The API derives internal_sku<-stock_number,
// color<-exterior_color, category='vehicle', and mirrors make->designer /
// model->style_number; callers send stock_number, exterior_color, and the
// vehicle fields only.
export async function createVehicle(body) {
  const { data } = await api.post('/catalog', { ...body, is_vehicle: true })
  return data
}

// Patch mutable vehicle fields. `is_vehicle` is intentionally never sent —
// a row's car/not-car identity is fixed at create time. The PATCH route
// does not re-mirror make->designer, so the page also threads
// designer/style_number to keep the compat search columns in sync.
export async function updateVehicle(catalogItemId, patch) {
  const { data } = await api.patch(`/catalog/${catalogItemId}`, patch)
  return data
}

// Upload one vehicle photo (multipart). Returns the full updated catalog
// item, whose `image_urls` now ends with the new origin-relative photo path.
// The vehicle must already exist (create first, then add photos).
export async function uploadVehiclePhoto(catalogItemId, file) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post(`/catalog/${catalogItemId}/photos`, form)
  return data
}

// Set or clear alt text for photos already on a vehicle. `alts` is keyed
// by the photo's URL exactly as it appears in `image_urls` — never by
// position, because the photo grid reorders and a positional write would
// land on the wrong picture. A null/blank value clears that description.
export async function updateVehiclePhotoAlts(catalogItemId, alts) {
  const { data } = await api.patch(`/catalog/${catalogItemId}/photo-alts`, { alts })
  return data
}

// Decode a VIN via NHTSA vPIC. Returns { vin (normalized), check_digit_ok,
// decoded: {year, make, model, trim, body_type, fuel_type, transmission,
// drivetrain}, error, existing_vehicle_id }. A 422 means the VIN is
// structurally invalid (bad length / I,O,Q); the caller shows detail.message.
export async function decodeVin(vin) {
  const { data } = await api.get(`/admin/vin/decode/${encodeURIComponent(vin)}`)
  return data
}

// OCR a photo of a VIN sticker/plate. Returns { found, best, candidates }.
// `best` (when found) has the same shape as decodeVin's result, so the
// caller reuses the same prefill path. Only checksum-valid reads come back.
export async function scanVin(file) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post('/admin/vin/scan', form)
  return data
}
