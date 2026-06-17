import {
  Card,
  CardContent,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Typography,
} from '@mui/material'
import BusinessIcon from '@mui/icons-material/Business'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep'
import GroupsIcon from '@mui/icons-material/Groups'
import TuneIcon from '@mui/icons-material/Tune'
import { Link as RouterLink } from 'react-router-dom'

const SECTIONS = [
  {
    to: '/settings/business-profile',
    icon: BusinessIcon,
    title: 'Business profile',
    description: 'Legal name, address, logo, default tax rate, and invoice defaults.',
  },
  {
    to: '/settings/widget',
    icon: TuneIcon,
    title: 'Widget settings',
    description: 'Theme, copy, availability, blackout dates, and embed code for the public booking widget.',
  },
  {
    to: '/settings/staff',
    icon: GroupsIcon,
    title: 'Staff management',
    description: 'Stylist profiles, schedules and time off, boutique locations, and attendance review.',
  },
  {
    to: '/settings/recycle-bin',
    icon: DeleteSweepIcon,
    title: 'Recycle Bin',
    description: 'Archived contacts, events, participants, and special orders. Restore from here.',
  },
]

export default function Settings() {
  return (
    <Card>
      <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
        <Typography variant="h4" gutterBottom>
          Settings
        </Typography>
        <List sx={{ p: 0 }}>
          {SECTIONS.map(({ to, icon: Icon, title, description }) => (
            <ListItem key={to} disablePadding sx={{ mb: 0.5 }}>
              <ListItemButton component={RouterLink} to={to} sx={{ borderRadius: 2 }}>
                <ListItemIcon>
                  <Icon />
                </ListItemIcon>
                <ListItemText primary={title} secondary={description} />
                <ChevronRightIcon color="action" />
              </ListItemButton>
            </ListItem>
          ))}
        </List>
      </CardContent>
    </Card>
  )
}
