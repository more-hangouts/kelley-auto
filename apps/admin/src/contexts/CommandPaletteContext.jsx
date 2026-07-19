import { createContext, useContext } from 'react'

// Lifted out of DashboardLayout so child screens (QuickActionsBar, etc.)
// can open the palette without prop-drilling through the layout tree.
//
// `openNewLead` / `closeNewLead` toggle the walk-in-lead dialog mounted
// in DashboardLayout. The context only owns open/close — the dialog's
// form state stays local to NewLeadDialog so opening it does not pull
// every consumer of this context into the dialog's re-render shape.
const CommandPaletteContext = createContext({
  open: () => {},
  close: () => {},
  openNewLead: () => {},
  closeNewLead: () => {},
})

export function CommandPaletteProvider({ value, children }) {
  return (
    <CommandPaletteContext.Provider value={value}>
      {children}
    </CommandPaletteContext.Provider>
  )
}

export function useCommandPalette() {
  return useContext(CommandPaletteContext)
}
