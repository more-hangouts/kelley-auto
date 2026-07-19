import { useState } from 'react'
import {
  AppBar,
  Box,
  Button,
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
  Typography,
} from '@mui/material'
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

export default function SalesLayout() {
  const { user, logout, lock, idleWarning } = useSalesAuth()
  const navigate = useNavigate()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)

  function closeDrawer() {
    setDrawerOpen(false)
  }

  function handleSignOutRequest() {
    doSignOut()
  }

  function doSignOut() {
    setConfirmOpen(false)
    logout()
    navigate('/login', { replace: true })
  }

  function handleLock() {
    // Lock keeps the shared tablet available for the next salesperson
    // without ending the current session server-side.
    lock('manual')
  }

  const navItems = [
    { to: '/', label: 'Dashboard', end: true },
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
                Kelley Autoplex
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
                secondary="Hand off to the next salesperson."
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
        <DialogTitle>Sign out?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This ends your session on this tablet. Use Lock / Switch when the
            next salesperson needs to take over quickly.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmOpen(false)}>Cancel</Button>
          <Button color="error" onClick={doSignOut}>
            Sign out
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
