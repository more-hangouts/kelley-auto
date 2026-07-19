import { Box, Button, Card, CardContent, Stack, Typography } from '@mui/material'
import AccessTimeOutlinedIcon from '@mui/icons-material/AccessTimeOutlined'
import CalendarMonthOutlinedIcon from '@mui/icons-material/CalendarMonthOutlined'
import PersonAddAltOutlinedIcon from '@mui/icons-material/PersonAddAltOutlined'
import PersonSearchOutlinedIcon from '@mui/icons-material/PersonSearchOutlined'
import ReceiptLongOutlinedIcon from '@mui/icons-material/ReceiptLongOutlined'
import { Link as RouterLink } from 'react-router-dom'

import { useCommandPalette } from '../../contexts/CommandPaletteContext'

export default function QuickActionsBar() {
  const palette = useCommandPalette()

  return (
    <Card>
      <CardContent>
        <Typography variant="overline" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
          Quick actions
        </Typography>
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={1.5}
          sx={{ flexWrap: 'wrap', '& > *': { flexGrow: 1, flexBasis: 0 } }}
        >
          <Button
            variant="contained"
            onClick={palette.openNewLead}
            startIcon={<PersonAddAltOutlinedIcon />}
          >
            <Box component="span">New walk-in lead</Box>
          </Button>
          <Button
            variant="outlined"
            onClick={palette.open}
            startIcon={<PersonSearchOutlinedIcon />}
          >
            <Box component="span">Find / create contact</Box>
          </Button>
          <Button
            variant="outlined"
            component={RouterLink}
            to="/calendar"
            startIcon={<CalendarMonthOutlinedIcon />}
          >
            <Box component="span">Open calendar</Box>
          </Button>
          <Button
            variant="outlined"
            component={RouterLink}
            to="/settings/staff/attendance"
            startIcon={<AccessTimeOutlinedIcon />}
          >
            <Box component="span">Attendance review</Box>
          </Button>
          <Button
            variant="outlined"
            component={RouterLink}
            to="/invoices"
            startIcon={<ReceiptLongOutlinedIcon />}
          >
            <Box component="span">Browse invoices</Box>
          </Button>
        </Stack>
      </CardContent>
    </Card>
  )
}
