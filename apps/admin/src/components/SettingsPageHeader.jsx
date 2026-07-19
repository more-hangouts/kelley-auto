import { Box, Breadcrumbs, IconButton, Link, Tooltip, Typography } from '@mui/material'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import NavigateNextIcon from '@mui/icons-material/NavigateNext'
import { Link as RouterLink } from 'react-router-dom'

// Shared header for any page that lives under /settings. Renders a
// back arrow that points at the previous breadcrumb and a clickable
// breadcrumb trail. The current page is always the last crumb and is
// not rendered as a link.
//
// Usage:
//   <SettingsPageHeader
//     crumbs={[
//       { label: 'Settings', to: '/settings' },
//       { label: 'Staff management', to: '/settings/staff' },
//       { label: 'Schedule & time off' },
//     ]}
//   />
export default function SettingsPageHeader({ crumbs }) {
  if (!Array.isArray(crumbs) || crumbs.length === 0) return null
  const parent = crumbs.length > 1 ? crumbs[crumbs.length - 2] : null

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2.5 }}>
      {parent?.to ? (
        <Tooltip title={`Back to ${parent.label}`} arrow>
          <IconButton
            component={RouterLink}
            to={parent.to}
            size="small"
            aria-label={`Back to ${parent.label}`}
            sx={{ color: 'text.secondary' }}
          >
            <ArrowBackIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      ) : null}
      <Breadcrumbs
        separator={<NavigateNextIcon fontSize="small" />}
        sx={{ '& .MuiBreadcrumbs-separator': { mx: 0.5 } }}
      >
        {crumbs.map((crumb, idx) => {
          const isLast = idx === crumbs.length - 1
          if (isLast || !crumb.to) {
            return (
              <Typography
                key={`${crumb.label}-${idx}`}
                variant="body2"
                sx={{ color: isLast ? 'text.primary' : 'text.secondary', fontWeight: isLast ? 600 : 400 }}
              >
                {crumb.label}
              </Typography>
            )
          }
          return (
            <Link
              key={`${crumb.label}-${idx}`}
              component={RouterLink}
              to={crumb.to}
              underline="hover"
              variant="body2"
              sx={{ color: 'text.secondary' }}
            >
              {crumb.label}
            </Link>
          )
        })}
      </Breadcrumbs>
    </Box>
  )
}
