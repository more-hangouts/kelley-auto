import { useCallback, useEffect, useMemo, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import {
  Avatar,
  Box,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
} from '@mui/material'
import CalendarMonthOutlinedIcon from '@mui/icons-material/CalendarMonthOutlined'
import CheckroomOutlinedIcon from '@mui/icons-material/CheckroomOutlined'
import DashboardOutlinedIcon from '@mui/icons-material/DashboardOutlined'
import SettingsOutlinedIcon from '@mui/icons-material/SettingsOutlined'
import ViewKanbanOutlinedIcon from '@mui/icons-material/ViewKanbanOutlined'
import LogoutIcon from '@mui/icons-material/Logout'
import MenuIcon from '@mui/icons-material/Menu'
import SearchIcon from '@mui/icons-material/Search'

import CommandPalette from './CommandPalette'
import NewLeadDialog from './dashboard/NewLeadDialog'
import { useAuth } from '../contexts/AuthContext'
import { CommandPaletteProvider } from '../contexts/CommandPaletteContext'

// Returns true when a keyboard event originated from a real text-
// editing target. Used by the global "/" shortcut so typing a slash
// inside a TextField never steals focus into the palette.
function isEditableTarget(target) {
  if (!target) return false
  const tag = target.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (target.isContentEditable) return true
  return false
}

const SIDEBAR_WIDTH = 240
const TOPBAR_HEIGHT = 64

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: DashboardOutlinedIcon, end: true },
  { to: '/pipeline', label: 'Pipeline', icon: ViewKanbanOutlinedIcon },
  { to: '/calendar', label: 'Calendar', icon: CalendarMonthOutlinedIcon },
  { to: '/products', label: 'Products', icon: CheckroomOutlinedIcon },
  { to: '/settings', label: 'Settings', icon: SettingsOutlinedIcon },
]

function initials(fullName, username) {
  const source = (fullName || username || '').trim()
  if (!source) return '?'
  const parts = source.split(/\s+/).slice(0, 2)
  return parts.map((p) => p[0]?.toUpperCase() ?? '').join('') || '?'
}

function SidebarContent({ onNavigate }) {
  return (
    <>
      <Box
        sx={{
          height: TOPBAR_HEIGHT,
          display: 'flex',
          alignItems: 'center',
          px: 3,
          borderBottom: '1px solid',
          borderColor: 'divider',
        }}
      >
        <Typography variant="h5" sx={{ color: 'primary.main', letterSpacing: 0.3 }}>
          Bellas XV
        </Typography>
      </Box>

      <List sx={{ px: 1.5, py: 2 }}>
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <ListItem key={to} disablePadding sx={{ mb: 0.5 }}>
            <ListItemButton
              component={NavLink}
              to={to}
              end={end}
              onClick={onNavigate}
              sx={{
                borderRadius: 2,
                position: 'relative',
                color: 'text.secondary',
                '&:hover': {
                  bgcolor: 'rgba(93, 58, 107, 0.06)',
                },
                '&.active': {
                  bgcolor: 'rgba(93, 58, 107, 0.10)',
                  color: 'secondary.dark',
                  fontWeight: 600,
                  '&::before': {
                    content: '""',
                    position: 'absolute',
                    left: 0,
                    top: 8,
                    bottom: 8,
                    width: 3,
                    borderRadius: 2,
                    bgcolor: 'primary.main',
                  },
                  '& .MuiListItemIcon-root': {
                    color: 'secondary.dark',
                  },
                },
              }}
            >
              <ListItemIcon sx={{ minWidth: 36, color: 'inherit' }}>
                <Icon fontSize="small" />
              </ListItemIcon>
              <ListItemText
                primary={label}
                primaryTypographyProps={{ fontSize: 14, fontWeight: 'inherit' }}
              />
            </ListItemButton>
          </ListItem>
        ))}
      </List>
    </>
  )
}

export default function DashboardLayout() {
  const { user, logout } = useAuth()
  const [anchorEl, setAnchorEl] = useState(null)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [newLeadOpen, setNewLeadOpen] = useState(false)
  const menuOpen = Boolean(anchorEl)

  const openPalette = useCallback(() => setPaletteOpen(true), [])
  const closePalette = useCallback(() => setPaletteOpen(false), [])
  const openNewLead = useCallback(() => setNewLeadOpen(true), [])
  const closeNewLead = useCallback(() => setNewLeadOpen(false), [])
  const paletteContextValue = useMemo(
    () => ({
      open: openPalette,
      close: closePalette,
      openNewLead,
      closeNewLead,
    }),
    [openPalette, closePalette, openNewLead, closeNewLead],
  )

  // Global keyboard shortcuts. Cmd/Ctrl-K is unconditional; "/" only
  // fires when no editable element holds focus so it does not steal
  // a literal slash typed into a notes textarea or a search field.
  useEffect(() => {
    function handler(e) {
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key === 'k') {
        e.preventDefault()
        setPaletteOpen((v) => !v)
        return
      }
      if (e.key === '/' && !isEditableTarget(e.target) && !paletteOpen) {
        e.preventDefault()
        setPaletteOpen(true)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [paletteOpen])

  const drawerPaperSx = {
    width: SIDEBAR_WIDTH,
    boxSizing: 'border-box',
    borderRight: '1px solid',
    borderColor: 'divider',
    bgcolor: 'background.paper',
  }

  return (
    <CommandPaletteProvider value={paletteContextValue}>
    <Box sx={{ display: 'flex', minHeight: '100vh', bgcolor: 'background.default' }}>
      <Drawer
        variant="permanent"
        sx={{
          display: { xs: 'none', md: 'block' },
          width: SIDEBAR_WIDTH,
          flexShrink: 0,
          '& .MuiDrawer-paper': drawerPaperSx,
        }}
      >
        <SidebarContent />
      </Drawer>

      <Drawer
        variant="temporary"
        open={mobileOpen}
        onClose={() => setMobileOpen(false)}
        ModalProps={{ keepMounted: true }}
        sx={{
          display: { xs: 'block', md: 'none' },
          '& .MuiDrawer-paper': drawerPaperSx,
        }}
      >
        <SidebarContent onNavigate={() => setMobileOpen(false)} />
      </Drawer>

      <Box sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <Box
          sx={{
            height: TOPBAR_HEIGHT,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: { xs: 2, md: 4 },
            borderBottom: '1px solid',
            borderColor: 'divider',
            bgcolor: 'background.paper',
          }}
        >
          <IconButton
            onClick={() => setMobileOpen(true)}
            sx={{ display: { xs: 'inline-flex', md: 'none' }, color: 'text.primary' }}
            aria-label="open navigation"
          >
            <MenuIcon />
          </IconButton>
          <Box
            role="button"
            tabIndex={0}
            onClick={openPalette}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                openPalette()
              }
            }}
            aria-label="Open global search"
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              flexGrow: 1,
              maxWidth: 420,
              mx: { xs: 1, md: 2 },
              px: 1.5,
              py: 0.75,
              borderRadius: 2,
              border: '1px solid',
              borderColor: 'divider',
              bgcolor: 'background.default',
              color: 'text.secondary',
              cursor: 'pointer',
              userSelect: 'none',
              transition: 'border-color 120ms ease, background-color 120ms ease',
              '&:hover, &:focus-visible': {
                borderColor: 'primary.main',
                outline: 'none',
              },
            }}
          >
            <SearchIcon fontSize="small" />
            <Typography variant="body2" sx={{ flexGrow: 1 }}>
              Search events, contacts, invoices…
            </Typography>
            <Typography
              variant="caption"
              sx={{
                px: 0.75,
                py: 0.25,
                borderRadius: 1,
                bgcolor: 'action.hover',
                color: 'text.secondary',
                fontFamily: 'monospace',
                fontSize: 11,
                display: { xs: 'none', sm: 'inline-block' },
              }}
            >
              {/Mac|iPhone|iPad/.test(navigator.userAgent) ? '⌘K' : 'Ctrl+K'}
            </Typography>
          </Box>
          <Avatar
            onClick={(e) => setAnchorEl(e.currentTarget)}
            sx={{
              cursor: 'pointer',
              bgcolor: 'primary.main',
              color: 'common.white',
              width: 38,
              height: 38,
              fontSize: 14,
              fontWeight: 600,
            }}
          >
            {initials(user?.full_name, user?.username)}
          </Avatar>
          <Menu
            anchorEl={anchorEl}
            open={menuOpen}
            onClose={() => setAnchorEl(null)}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
            transformOrigin={{ vertical: 'top', horizontal: 'right' }}
            slotProps={{ paper: { sx: { mt: 1, minWidth: 220 } } }}
          >
            <Box sx={{ px: 2, py: 1.25 }}>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {user?.full_name || user?.username}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {user?.email}
              </Typography>
            </Box>
            <Divider />
            <MenuItem
              onClick={() => {
                setAnchorEl(null)
                logout()
              }}
            >
              <ListItemIcon>
                <LogoutIcon fontSize="small" />
              </ListItemIcon>
              <ListItemText>Logout</ListItemText>
            </MenuItem>
          </Menu>
        </Box>

        <Box component="main" sx={{ flexGrow: 1, p: { xs: 2, md: 4 } }}>
          <Outlet />
        </Box>
      </Box>

      <CommandPalette
        open={paletteOpen}
        onClose={closePalette}
        onNewLead={openNewLead}
      />
      <NewLeadDialog open={newLeadOpen} onClose={closeNewLead} />
    </Box>
    </CommandPaletteProvider>
  )
}
