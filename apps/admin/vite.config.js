import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Group node_modules into a few stable vendor chunks so the eager shell and
// the lazy route chunks share cached vendor code instead of duplicating it.
// Matching is by exact package name (extracted after the last `node_modules/`)
// rather than a naive `id.includes('react')`, which would also catch
// `react-is`, `@emotion/react`, and `@tanstack/react-query` and scatter the
// React runtime across chunk boundaries. Path handling is separator-safe and
// works with pnpm's nested `node_modules/.pnpm/...` layout.
function manualChunks(id) {
  if (!id.includes('node_modules')) return undefined // never trap app source

  const norm = id.split('\\').join('/')
  const after = norm.slice(norm.lastIndexOf('node_modules/') + 'node_modules/'.length)
  const pkg = after.startsWith('@')
    ? after.split('/').slice(0, 2).join('/')
    : after.split('/')[0]

  // Scoped packages first: '@emotion/react' / '@tanstack/react-query' contain
  // the substring 'react' and must not fall through to the react-vendor test.
  if (pkg.startsWith('@mui/') || pkg.startsWith('@emotion/')) return 'mui'
  // Group the whole TanStack Query family (react-query + its query-core
  // runtime dep) so the query chunk is self-contained rather than leaving
  // ~30 KB of query-core stranded in the generic vendor chunk.
  if (pkg.startsWith('@tanstack/')) return 'query'
  if (pkg.startsWith('@dnd-kit/')) return 'dnd'
  // Keep the whole React runtime (react + react-dom + scheduler) and the
  // router (react-router + its @remix-run/router core) in one chunk so init
  // order can never break across a chunk boundary.
  if (
    ['react', 'react-dom', 'scheduler', 'react-router', 'react-router-dom', '@remix-run/router'].includes(pkg)
  ) {
    return 'react-vendor'
  }
  return 'vendor' // axios, dayjs, and everything else in node_modules
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '127.0.0.1',
  },
  build: {
    // Manifest enables build-time bundle analysis; it does not affect the
    // runtime bundle behavior.
    manifest: true,
    rollupOptions: {
      output: {
        manualChunks,
      },
    },
  },
})
