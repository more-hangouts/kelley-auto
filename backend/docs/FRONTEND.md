# Frontend (admin SPA)

React 19 + MUI 6 + Vite, served as static files from `frontend/dist/` by
nginx at admin.shopbellasxv.com. Single-page app behind JWT auth.

## Layout

```
frontend/
├── package.json
├── vite.config.js
├── eslint.config.js
└── src/
    ├── main.jsx                  Vite entry
    ├── App.jsx                   Router + providers (Theme, QueryClient, Auth)
    ├── theme.js                  MUI theme overrides
    ├── services/api.js           Single axios client + named API functions
    ├── contexts/
    │   └── AuthContext.jsx       Token storage + login/logout + user state
    ├── components/
    │   ├── DashboardLayout.jsx   Sidebar + topbar + outlet
    │   ├── ProtectedRoute.jsx    Auth gate
    │   └── EventQuickViewDrawer.jsx  Card click drawer on the kanban
    └── pages/
        ├── Login.jsx
        ├── Dashboard.jsx         Landing
        ├── Pipeline.jsx          Kanban — drag-drop events between statuses
        ├── EventDetail.jsx       Full-page view at /events/:id with linked appointments
        ├── AppointmentsCalendar.jsx
        ├── AdminCatalog.jsx      Products — gallery/list browse, vendor + color-family filters
        ├── BookingWidgetSettings.jsx
        └── Settings.jsx
```

## Stack notes

- **React 19** — function components only; no class components, no
  `defaultProps` (deprecated). Use default parameter values.
- **MUI 6** — Material UI v6, emotion-styled. Theme in `src/theme.js`.
- **react-router-dom 6** — file-based-ish routing in `App.jsx` is the source
  of truth. Don't use the data router APIs yet.
- **@tanstack/react-query 5** — wraps every API call. Default `staleTime`
  30s; window-focus refetch off (it was annoying on the kanban).
- **@dnd-kit/core + @dnd-kit/sortable** — drag-drop. Pointer sensor with 5px
  activation distance so a click under that fires the drawer instead.
- **dayjs** + `relativeTime` plugin for "in 138d" / "today" badges.
- **axios** — single configured instance in `services/api.js`. Bearer token
  injected via interceptor; 401s auto-redirect to `/login`.

## Routing

```jsx
/login                  Login (public)
/                       Dashboard
/pipeline               Kanban (Pipeline.jsx)
/events/:eventId        Event detail (EventDetail.jsx)
/calendar               AppointmentsCalendar
/widget-settings        BookingWidgetSettings
/settings               Settings
```

All routes except `/login` are wrapped in `ProtectedRoute`.

## API client

[services/api.js](../frontend/src/services/api.js) is the only place that
touches the network. All HTTP calls are named exports — never `axios.get(...)`
inline in a component.

```js
import { getEventBoard, patchEventStatus, promoteAppointmentToEvent } from '../services/api'
```

This makes mocking trivial (when we eventually need it) and keeps URL strings
in one file.

## React-query patterns

### List read

```js
const { data, isLoading, error, refetch } = useQuery({
  queryKey: ['events', 'board', eventType],
  queryFn: () => getEventBoard(eventType),
})
```

### Optimistic mutation (the kanban)

The optimistic update lives in a helper, not in `onMutate`. The mutation hook
stays narrow — only rollback + invalidate. The helper is the single entry
point shared by drag-drop and the drawer's status dropdown.

```js
const changeStatus = useMutation({
  mutationFn: ({ eventId, newStatus }) => patchEventStatus(eventId, newStatus),
  onError: (_err, vars) => {
    if (vars?.previous) queryClient.setQueryData(queryKey, vars.previous)
  },
  onSettled: () => queryClient.invalidateQueries({ queryKey }),
})

async function commitStatusChange(eventId, newStatus) {
  await queryClient.cancelQueries({ queryKey })
  const previous = queryClient.getQueryData(queryKey)
  if (previous) {
    queryClient.setQueryData(queryKey, moveCardOptimistic(previous, eventId, newStatus))
  }
  changeStatus.mutate({ eventId, newStatus, previous })
}
```

Why split it: keeping the optimistic move outside `onMutate` lets `handleDragEnd`
order the cache update **before** clearing the active drag overlay, which is
what avoids the "card snaps back to old column for a frame" flicker on drop.

Keys are arrays, hierarchical — invalidating `['events']` invalidates both
`['events', 'board', ...]` and `['event', id]`.

## Drag-drop pattern

[pages/Pipeline.jsx](../frontend/src/pages/Pipeline.jsx) uses:

- `<DndContext>` at the page level
- `useDroppable({ id: 'column-...', data: { columnCode } })` per column
- `useDraggable({ id: 'card-...', data: { card } })` per card — the whole card
  is the grab target
- `<DragOverlay dropAnimation={null}>` for the floating preview during drag

Pointer sensor: `{ activationConstraint: { distance: 5 } }`. Below 5px the
event is treated as a click and opens the quick-view drawer. Above 5px it's a
drag.

Collision detection is `pointerWithin` with a `rectIntersection` fallback
(see `columnCollisionDetection`). Pointer-based targeting keeps "which column
am I over?" stable when cards overlap; the rect fallback handles edges where
the pointer isn't inside any column.

Source-card opacity goes to `0` while dragging (the visible card is the
overlay), and `dropAnimation={null}` skips the snap-back animation. Combined
with applying the optimistic cache update **before** clearing the active drag
state in `handleDragEnd`, this is what eliminates the on-drop flicker.

## MUI conventions

- Use the theme (`primary.main`, `text.secondary`, `divider`) over hard-coded
  colors.
- `Stack` for vertical layout; `Box` for one-off positioning.
- `Section` and `KV` helpers are repeated in a few pages — fine for now,
  promote to `components/` if a fourth use shows up.
- Cards on the kanban are intentionally lightweight (`Card` with subtle
  border + no elevation when at rest). JN-style with Bellas palette.

## Build

```bash
cd frontend && npm run build
```

Output goes to `frontend/dist/`. Nginx serves it directly. New bundle hashes
on every build, so cache busting is automatic.

`VITE_API_URL` must be set at build time. Production: `https://api.shopbellasxv.com/api`.
Dev: `http://localhost:8000/api`.

## Lint

```bash
cd frontend && npm run lint
```

ESLint config in `frontend/eslint.config.js`. Includes `react-hooks` and
`react-refresh` plugins.

## What we are NOT using (yet)

- TypeScript. The codebase is JS+JSX. Adding TS is a future migration.
- A test runner. No Jest/Vitest yet — we lean on smoke tests against the real
  backend. UI testing comes when the surface stabilizes more.
- Storybook. Components live where they're used.
- Form libraries (react-hook-form, formik). Plain controlled inputs are
  enough at this scale.
