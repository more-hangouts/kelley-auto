#!/usr/bin/env node
// Export-surface guard for services/api.
//
// Statically parses every module under src/services/api (client + domain
// modules, excluding the barrel) and asserts the union of named exports —
// plus the default export — exactly equals src/services/api/EXPORTS.frozen.
//
// Runs with plain Node (no bundler, no test framework, no deps). Wire it
// into CI or run manually:  node scripts/check-api-exports.mjs
//
// A non-zero exit means the public API surface drifted from the frozen
// Phase 4 baseline of 227 named exports + 1 default. If the drift is
// intentional, update EXPORTS.frozen in the same commit.

import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const apiDir = join(here, '..', 'src', 'services', 'api')

const frozen = new Set(
  readFileSync(join(apiDir, 'EXPORTS.frozen'), 'utf8')
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#')),
)

const named = new Set()
let defaultCount = 0

// Matches `export function NAME`, `export async function NAME`,
// `export const NAME`, and `export { default, isSalesSubdomain } from './client'`.
const declRe = /^export\s+(?:async\s+)?(?:function|const|let|class)\s+([A-Za-z0-9_$]+)/
const braceRe = /^export\s+\{([^}]*)\}/

for (const file of readdirSync(apiDir)) {
  if (!file.endsWith('.js') || file === 'index.js') continue
  const src = readFileSync(join(apiDir, file), 'utf8')
  for (const line of src.split('\n')) {
    const d = declRe.exec(line)
    if (d) {
      named.add(d[1])
      continue
    }
    const b = braceRe.exec(line)
    if (b) {
      for (const raw of b[1].split(',')) {
        const name = raw.trim().split(/\s+as\s+/).pop().trim()
        if (name === 'default') defaultCount++
        else if (name) named.add(name)
      }
    }
    if (/^export\s+default\b/.test(line)) defaultCount++
  }
}

const missing = [...frozen].filter((n) => !named.has(n)).sort()
const extra = [...named].filter((n) => !frozen.has(n)).sort()

let ok = true
if (defaultCount !== 1) {
  console.error(`✗ expected exactly 1 default export (the axios instance), found ${defaultCount}`)
  ok = false
}
if (missing.length) {
  console.error(`✗ ${missing.length} frozen export(s) missing from modules:\n  ${missing.join('\n  ')}`)
  ok = false
}
if (extra.length) {
  console.error(`✗ ${extra.length} unexpected export(s) not in EXPORTS.frozen:\n  ${extra.join('\n  ')}`)
  ok = false
}

if (!ok) process.exit(1)
console.log(`✓ services/api export surface intact: ${named.size} named + 1 default (matches EXPORTS.frozen)`)
