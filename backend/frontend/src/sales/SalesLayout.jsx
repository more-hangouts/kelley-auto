import { useState } from 'react'
import {
  AppBar,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Snackbar,
  Stack,
  Toolbar,
  Tooltip,
  Typography,
} from '@mui/material'
import AccessTimeIcon from '@mui/icons-material/AccessTime'
import LockIcon from '@mui/icons-material/Lock'
import LogoutIcon from '@mui/icons-material/Logout'
import MenuIcon from '@mui/icons-material/Menu'
import {
  NavLink,
  Link as RouterLink,
  Outlet,
  useNavigate,
} from 'react-router-dom'

import { useSalesAuth } from '../contexts/SalesAuthContext'
import { useClockStatus } from './useClockStatus'

export default function SalesLayout() {
  const { user, logout, lock, idleWarning } = useSalesAuth()
  const navigate = useNavigate()
  const { status: clockStatus } = useClockStatus()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)

  const isPunchedIn = clockStatus?.state === 'in'

  function closeDrawer() {
    setDrawerOpen(false)
  }

  function handleSignOutRequest() {
    if (isPunchedIn) {
      setConfirmOpen(true)
      return
    }
    doSignOut()
  }

  function doSignOut() {
    setConfirmOpen(false)
    logout()
    navigate('/login', { replace: true })
  }

  function handleLock() {
    // No clocked-in confirm: locking does not punch out (no
    // token_version bump, no attendance write). The stylist stays
    // on the clock; the next stylist enters their PIN to swap.
    lock('manual')
  }

  const navItems = [
    { to: '/', label: 'Dashboard', end: true },
    { to: '/schedule', label: 'Schedule' },
    { to: '/time-off', label: 'Time off' },
    { to: '/my-attendance', label: 'My attendance' },
    { to: '/notifications', label: 'Notifications' },
  ]

  return (
    <Box sx={{ minHeight: '100vh', bgcolor: 'background.default' }}>
      <AppBar position="sticky" color="default" elevation={1}>
        <Toolbar sx={{ justifyContent: 'space-between', gap: 1 }}>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <IconButton
              aria-label="Open navigation menu"
              edge="start"
              onClick={() => setDrawerOpen(true)}
              size="small"
              sx={{ display: { xs: 'inline-flex', md: 'none' } }}
            >
              <MenuIcon />
            </IconButton>
            <Stack direction="row" spacing={1.5} alignItems="baseline">
              <Typography variant="h6" component="div" sx={{ fontWeight: 600 }}>
                Bellas XV
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Sales
              </Typography>
            </Stack>
          </Stack>
          <Stack direction="row" spacing={1.5} alignItems="center">
            {navItems.map((item) => (
              <Button
                key={item.to}
                component={RouterLink}
                to={item.to}
                size="small"
                variant="text"
                sx={{ display: { xs: 'none', md: 'inline-flex' } }}
              >
                {item.label}
              </Button>
            ))}
            {clockStatus && (
              <Tooltip
                title={
                  isPunchedIn
                    ? 'Tap to clock out'
                    : 'Tap to clock in'
                }
              >
                <Chip
                  size="small"
                  icon={<AccessTimeIcon fontSize="small" />}
                  label={isPunchedIn ? 'On the clock' : 'Off'}
                  color={isPunchedIn ? 'success' : 'default'}
                  variant={isPunchedIn ? 'filled' : 'outlined'}
                  component={RouterLink}
                  to="/clock"
                  clickable
                />
              </Tooltip>
            )}
            <Typography
              variant="body2"
              sx={{ display: { xs: 'none', md: 'block' } }}
            >
              {user?.full_name || user?.username}
            </Typography>
            <Button
              variant="text"
              size="small"
              startIcon={<LockIcon fontSize="small" />}
              onClick={handleLock}
              sx={{ display: { xs: 'none', md: 'inline-flex' } }}
            >
              Lock / Switch
            </Button>
            <Button
              variant="text"
              size="small"
              onClick={handleSignOutRequest}
              sx={{ display: { xs: 'none', md: 'inline-flex' } }}
            >
              Sign out
            </Button>
          </Stack>
        </Toolbar>
      </AppBar>

      <Drawer
        anchor="left"
        open={drawerOpen}
        onClose={closeDrawer}
        sx={{ display: { xs: 'block', md: 'none' } }}
        PaperProps={{ sx: { width: 280 } }}
      >
        <Box sx={{ p: 2 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            {user?.full_name || user?.username}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Sales floor
          </Typography>
        </Box>
        <Divider />
        <List>
          {navItems.map((item) => (
            <ListItem key={item.to} disablePadding>
              <ListItemButton
                component={NavLink}
                to={item.to}
                end={item.end}
                onClick={closeDrawer}
                sx={{
                  '&.active': {
                    bgcolor: 'action.selected',
                    '& .MuiListItemText-primary': { fontWeight: 600 },
                  },
                }}
              >
                <ListItemText primary={item.label} />
              </ListItemButton>
            </ListItem>
          ))}
        </List>
        <Divider />
        <List>
          <ListItem disablePadding>
            <ListItemButton
              onClick={() => {
                closeDrawer()
                handleLock()
              }}
            >
              <ListItemIcon>
                <LockIcon />
              </ListItemIcon>
              <ListItemText
                primary="Lock / Switch"
                secondary="Hand off to the next stylist. You stay clocked in."
              />
            </ListItemButton>
          </ListItem>
          <ListItem disablePadding>
            <ListItemButton
              onClick={() => {
                closeDrawer()
                handleSignOutRequest()
              }}
              sx={{ color: 'error.main' }}
            >
              <ListItemIcon sx={{ color: 'error.main' }}>
                <LogoutIcon />
              </ListItemIcon>
              <ListItemText
                primary="Sign out"
                secondary="End your session on this tablet."
              />
            </ListItemButton>
          </ListItem>
        </List>
      </Drawer>
      <Box component="main" sx={{ p: { xs: 2, sm: 3 }, maxWidth: 720, mx: 'auto' }}>
        <Outlet />
      </Box>

      <Snackbar
        open={idleWarning}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
        message="Still there? This tablet will lock in a few minutes. Tap anywhere to stay signed in."
      />

      <Dialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Sign out while clocked in?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            You're still on the clock. Signing out does NOT clock you
            out automatically. The owner will need to manually adjust
            your hours later if you forget.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmOpen(false)}>Cancel</Button>
          <Button
            variant="text"
            onClick={() => {
              setConfirmOpen(false)
              navigate('/clock')
            }}
          >
            Go clock out
          </Button>
          <Button color="error" onClick={doSignOut}>
            Sign out anyway
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
