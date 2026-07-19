// Cross-app domain enums, kept in one place so the admin SPA, the
// storefront, and (by mirror) the Python API agree on the same string
// values. These are the canonical wire values already used across the
// codebase; this package is the intended single source of truth.
//
// NOTE (Phase 2): this package is scaffolded but not yet imported by the
// apps. Wiring the frontends to consume it is deferred to a later phase
// to avoid broad refactoring during the monorepo move. The Python API
// remains the runtime authority for validation.

// crm_event.event_type — the deals-engine workflow discriminator.
// 'vehicle_sale' is the default for all new records; 'quinceanera' is
// retained only so historical Bella's-era rows stay readable.
export const EVENT_TYPES = Object.freeze(['vehicle_sale', 'quinceanera']);

// Omnichannel inbox conversation channels.
export const INBOX_CHANNELS = Object.freeze([
  'sms',
  'facebook',
  'instagram',
  'web_chat',
]);
