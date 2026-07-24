import { Box, CircularProgress } from '@mui/material'

// Suspense fallback for lazily-loaded route pages. Deliberately minimal:
// a centered spinner with no instructional text. `flex: 1` + `minHeight: 0`
// makes it fill whatever slot it is dropped into (the DashboardLayout
// <Outlet /> region, or a full-height top-level route) without forcing
// its own height, so the surrounding shell chrome never shifts or resizes
// while a page chunk loads.
export default function RouteFallback() {
  return (
    <Box
      role="status"
      aria-live="polite"
      aria-busy="true"
      sx={{
        flex: 1,
        minHeight: 0,
        alignSelf: 'stretch',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        p: 4,
      }}
    >
      <CircularProgress color="primary" />
    </Box>
  )
}
