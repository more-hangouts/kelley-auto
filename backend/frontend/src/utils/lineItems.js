// Catalog SKU obfuscation Phase 3 — shared line-item form helpers.
//
// Both InvoiceEditor and QuoteEditor build the same line-item shape:
// either a catalog-backed line (`catalog` snapshot set, customer copy
// derived) or a non-catalog line (`public_description` is the
// customer-facing text). These helpers keep the hydrate / serialize /
// validation logic in one place so the two editors cannot drift apart.

const CATEGORY_LABELS = {
  quince_gown: 'Quince gown',
  bridal_gown: 'Bridal gown',
  formal_gown: 'Formal gown',
  accessory: 'Accessory',
  service: 'Service',
}

// Mirrors `services/catalog_service.customer_line_description`. The
// preview is staff-only (it never ships to a customer surface), but
// it shows the editor user exactly what the customer will see at
// render time so they don't accidentally double-write the description.
export function customerLineDescription(catalog, sizeLabel) {
  if (!catalog) return ''
  const label = catalog.house_name || CATEGORY_LABELS[catalog.category] || 'Item'
  const parts = [label, catalog.color]
  if (sizeLabel) parts.push(`Size ${sizeLabel}`)
  return parts.join(' / ')
}

export function emptyLine({ tax_rate = '0', tax_name = null } = {}) {
  return {
    description: '',
    quantity: '1',
    unit_price_cents: 0,
    discount_cents: 0,
    tax_rate,
    tax_name,
    kind: 'product',
    notes: null,
    // Phase 3 catalog-aware fields.
    catalog_item_id: null,
    catalog: null,
    size_label: '',
    public_description: '',
    internal_notes: '',
  }
}

export function normalizeQuantityInput(value) {
  return String(value || '').replace(/\D/g, '')
}

// Hydrate a form line from a server `LineItemResponse`. Server
// responses carry both legacy (`description`/`notes`) and Phase 2
// (`public_description`/`internal_notes`/`catalog`) fields. The form
// state always uses the new field names; we preserve `description`
// and `notes` purely so the API request body can keep mirroring them
// for back-compat until Phase 4's render swap.
export function hydrateLineFromInvoice(li) {
  return {
    id: li.id,
    description: li.description || '',
    quantity: String(li.quantity),
    unit_price_cents: li.unit_price_cents,
    discount_cents: li.discount_cents,
    tax_rate: String(li.tax_rate),
    tax_name: li.tax_name,
    kind: li.kind,
    notes: li.notes,
    catalog_item_id: li.catalog_item_id ?? null,
    catalog: li.catalog || null,
    size_label: li.size_label || '',
    public_description: li.public_description || '',
    internal_notes: li.internal_notes || '',
  }
}

// Serialize a form line back into the request body the invoices /
// quotes routers accept. The router still accepts the legacy
// description/notes pair for back-compat with non-picker calls; for
// catalog-backed lines we strip those entirely, since Phase 2's
// service rejects them with code `catalog_line_legacy_text`.
export function serializeLineForApi(line, sortOrder) {
  const base = {
    quantity: line.quantity,
    unit_price_cents: line.unit_price_cents,
    discount_cents: line.discount_cents,
    tax_rate: line.tax_rate,
    tax_name: line.tax_name,
    kind: line.kind,
    sort_order: sortOrder,
    internal_notes: line.internal_notes || null,
  }
  if (line.catalog_item_id) {
    return {
      ...base,
      catalog_item_id: line.catalog_item_id,
      size_label: line.size_label || null,
      // Catalog-backed lines must NOT carry public_description /
      // description / notes. Phase 2 service rejects them.
    }
  }
  // Non-catalog line. Send public_description (preferred) and keep
  // a description mirror so the response body matches the field the
  // service stored on the row. The service will normalize this to
  // public_description regardless.
  const publicCopy = line.public_description || line.description || ''
  return {
    ...base,
    public_description: publicCopy || null,
    description: publicCopy || null,
  }
}

// Returns the catalog identifier substring that leaks into `value`,
// or null when no catalog row is matched or no leak is present.
// Used by the editor to surface a non-blocking warning before the
// API rejection arrives. Mirrors the server-side
// `assert_no_catalog_leak` rules.
export function detectCatalogLeak(catalog, value) {
  if (!catalog || !value) return null
  const haystack = value.toLowerCase()
  for (const key of ['internal_sku', 'designer', 'style_number']) {
    const ident = catalog[key]
    if (ident && haystack.includes(String(ident).toLowerCase())) {
      return { kind: key, value: ident }
    }
  }
  return null
}
