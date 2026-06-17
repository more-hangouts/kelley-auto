import { createTheme } from '@mui/material/styles'

const theme = createTheme({
  palette: {
    mode: 'light',
    primary: {
      main: '#B76E79',
      light: '#D4A5AB',
      dark: '#8B4D58',
      contrastText: '#FFFFFF',
    },
    secondary: {
      main: '#5D3A6B',
      light: '#8B6699',
      dark: '#3D1F4B',
      contrastText: '#FFFFFF',
    },
    success: {
      main: '#2E7D5C',
      contrastText: '#FFFFFF',
    },
    error: {
      main: '#A12B3F',
      contrastText: '#FFFFFF',
    },
    warning: {
      main: '#C58940',
      contrastText: '#FFFFFF',
    },
    info: {
      main: '#1E3A5F',
      contrastText: '#FFFFFF',
    },
    background: {
      default: '#FAF6F4',
      paper: '#FFFFFF',
    },
    text: {
      primary: '#2D1B2E',
      secondary: '#5D5560',
    },
    divider: '#E8DCD8',
  },
  shape: {
    borderRadius: 10,
  },
  typography: {
    fontFamily: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h1: { fontFamily: '"Playfair Display", Georgia, serif', fontWeight: 600 },
    h2: { fontFamily: '"Playfair Display", Georgia, serif', fontWeight: 600 },
    h3: { fontFamily: '"Playfair Display", Georgia, serif', fontWeight: 600 },
    h4: { fontFamily: '"Playfair Display", Georgia, serif', fontWeight: 600 },
    h5: { fontFamily: '"Playfair Display", Georgia, serif', fontWeight: 500 },
    h6: { fontFamily: '"Playfair Display", Georgia, serif', fontWeight: 500 },
    button: { textTransform: 'none', fontWeight: 500 },
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: { borderRadius: 8 },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: { backgroundImage: 'none' },
      },
    },
  },
})

export default theme
