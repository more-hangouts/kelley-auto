// Barrel: re-exports the shared client + every domain API module.
// Preserves the historical `services/api` import specifier and the
// default axios instance identity. Do NOT import this barrel from any
// module inside services/api/ (cycle/TDZ risk).
export { default, isSalesSubdomain } from './client'
export * from './auth'
export * from './core'
export * from './contacts'
export * from './voice'
export * from './deals'
export * from './booking'
export * from './analytics'
export * from './documents'
export * from './billing'
export * from './businessProfile'
export * from './dashboard'
export * from './inventory'
export * from './sales'
export * from './staff'
export * from './attendance'
export * from './scheduling'
export * from './messaging'
