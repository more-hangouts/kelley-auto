import { Box, CircularProgress } from '@mui/material'

// Suspense fallback for lazily-loaded route pages. Deliberately minimal:
// a centered spinner with no instructional text. It is a self-contained
// flex box with its own minHeight (a generous slice of the viewport), so
// it centers the spinner whether it is dropped into DashboardLayout's
// block-level <main> region or rendered as a full-page top-level route —
// it does not depend on the parent being a flex container. It never grows
// past its content, so the surrounding shell chrome never shifts or
// resizes while a page chunk loads.
export default function RouteFallback() {
  return (
    <Box
      role="status"
      aria-live="polite"
      aria-busy="true"
      sx={{
        width: '100%',
        minHeight: '40vh',
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
