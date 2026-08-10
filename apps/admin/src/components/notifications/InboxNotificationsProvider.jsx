import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import { getInboxUnreadCount } from '../../services/api'
import { classifyPoll, TOAST } from '../../utils/inboxArrival'
import NewMessageToast from './NewMessageToast'

// App-wide unread state for the inbox: the count behind the sidebar badge,
// the browser-tab count, and the toast that fires when something lands while
// you are on another page. One poll feeds all three, so they can never
// disagree.
//
// Modelled on SoftphoneProvider — a provider mounted once in DashboardLayout
// that owns a timer and renders its own overlay.

const InboxNotificationsContext = createContext(null)

// Matches the Inbox list's own cadence. Fast enough that a visitor typing on
// the storefront is not left waiting; slow enough that a dozen idle browser
// tabs do not become a steady load on a 4 GB box.
const POLL_MS = 20000

// Which channels are allowed to interrupt with a toast. `null` = all of them,
// which is what Luis picked. Narrowing later is this one line:
//   const TOAST_CHANNELS = ['web_chat']
// (the badge and tab count always cover every channel regardless).
const TOAST_CHANNELS = null

// The hook ships alongside the provider, same trade-off SoftphoneProvider and
// CommandPaletteContext make — two consumers, and a separate file would be
// pure indirection.
/* eslint-disable react-refresh/only-export-components */

export function useInboxNotifications() {
  const ctx = useContext(InboxNotificationsContext)
  if (ctx === null) {
    throw new Error(
      'useInboxNotifications must be used inside <InboxNotificationsProvider>',
    )
  }
  return ctx
}

export default function InboxNotificationsProvider({ children }) {
  const [unread, setUnread] = useState(0)
  const [toast, setToast] = useState(null)

  // The newest inbound timestamp this session has already accounted for.
  // Seeded (not toasted) on the first poll so signing in with a backlog of
  // unread threads does not fire a toast for each one.
  //
  // `seededRef` is separate from `seenAtRef` on purpose: an empty inbox at
  // sign-in gives the first poll nothing to take a timestamp from, and
  // treating "no watermark yet" as "not seeded" would swallow the very next
  // arrival as its seed — silencing the first message, which is the one that
  // matters most.
  const seededRef = useRef(false)
  const seenAtRef = useRef(0)
  // The thread the user currently has open. A message arriving in the thread
  // you are already reading needs no toast — you can see it.
  const activeIdRef = useRef(null)

  const refresh = useCallback(async () => {
    let data
    try {
      data = await getInboxUnreadCount()
    } catch {
      // Transient — a failed poll must not clear a badge that is still true.
      return
    }

    setUnread(data?.unread || 0)

    const latest = data?.latest
    const { action, seenAt } = classifyPoll({
      seeded: seededRef.current,
      seenAt: seenAtRef.current,
      latest,
      now: Date.now(),
      activeId: activeIdRef.current,
      toastChannels: TOAST_CHANNELS,
    })

    seededRef.current = true
    seenAtRef.current = seenAt
    if (action === TOAST) setToast(latest)
  }, [])

  // Poll only while the tab is visible, and catch up the moment it comes
  // back — a hidden tab is a screen nobody is watching.
  useEffect(() => {
    let timer = null

    const stop = () => {
      if (timer !== null) {
        clearInterval(timer)
        timer = null
      }
    }

    const start = () => {
      stop()
      refresh()
      timer = setInterval(refresh, POLL_MS)
    }

    const onVisibility = () => {
      if (document.visibilityState === 'visible') start()
      else stop()
    }

    onVisibility()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [refresh])

  // Unread count in the browser tab, so a minimised window still carries it.
  // The base title is captured once and always restored, so this never
  // compounds into "(2) (1) Kelley…".
  const baseTitleRef = useRef(null)
  useEffect(() => {
    if (baseTitleRef.current === null) {
      baseTitleRef.current = document.title.replace(/^\(\d+\)\s*/, '')
    }
    const base = baseTitleRef.current
    document.title = unread > 0 ? `(${unread}) ${base}` : base
    return () => {
      document.title = base
    }
  }, [unread])

  const setActiveConversationId = useCallback((id) => {
    activeIdRef.current = id
  }, [])

  const value = useMemo(
    () => ({ unread, refresh, setActiveConversationId }),
    [unread, refresh, setActiveConversationId],
  )

  return (
    <InboxNotificationsContext.Provider value={value}>
      {children}
      <NewMessageToast toast={toast} onClose={() => setToast(null)} />
    </InboxNotificationsContext.Provider>
  )
}
