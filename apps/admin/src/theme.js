import { createTheme } from '@mui/material/styles'

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#E8C46A',
      light: '#F3DA92',
      dark: '#B99232',
      contrastText: '#111318',
    },
    secondary: {
      main: '#4EA1D3',
      light: '#7EC4EA',
      dark: '#1D628A',
      contrastText: '#FFFFFF',
    },
    success: {
      main: '#3FA66B',
      contrastText: '#FFFFFF',
    },
    error: {
      main: '#E05A47',
      contrastText: '#FFFFFF',
    },
    warning: {
      main: '#F0A83A',
      contrastText: '#111318',
    },
    info: {
      main: '#56A6D6',
      contrastText: '#FFFFFF',
    },
    background: {
      default: '#0D1016',
      paper: '#151A22',
    },
    text: {
      primary: '#F4F7FA',
      secondary: '#A7B0BF',
    },
    divider: '#27313F',
  },
  shape: {
    borderRadius: 8,
  },
  typography: {
    fontFamily: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h1: { fontWeight: 700 },
    h2: { fontWeight: 700 },
    h3: { fontWeight: 700 },
    h4: { fontWeight: 700 },
    h5: { fontWeight: 650 },
    h6: { fontWeight: 650 },
    button: { textTransform: 'none', fontWeight: 650 },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          backgroundColor: '#0D1016',
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: { borderRadius: 8 },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          borderColor: '#27313F',
        },
      },
    },
  },
})

export default theme
