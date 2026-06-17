import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'

import { searchGlobal } from '../services/api'

// Phase 2 contract: 2-character minimum, ~150ms debounce,
// AbortController-cancelled supersession, and last-good results
// preserved across keystrokes via placeholderData. The hook owns
// only the debounce; the React Query layer owns cancellation and
// stale handling.
const DEBOUNCE_MS = 150
const MIN_QUERY_LENGTH = 2

export function useSearch(query) {
  const trimmed = query.trim()
  const [debounced, setDebounced] = useState(trimmed)

  useEffect(() => {
    if (trimmed === debounced) return
    const handle = setTimeout(() => setDebounced(trimmed), DEBOUNCE_MS)
    return () => clearTimeout(handle)
    // We intentionally do not include `debounced` in the dep list:
    // re-running the effect on each debounced settle would clobber
    // the next pending debounce. Comparing trimmed === debounced
    // above gives the same short-circuit without that loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trimmed])

  const enabled = debounced.length >= MIN_QUERY_LENGTH

  const result = useQuery({
    queryKey: ['search', debounced],
    queryFn: ({ signal }) => searchGlobal({ q: debounced, signal }),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: false,
  })

  return {
    debouncedQuery: debounced,
    enabled,
    isFetching: result.isFetching,
    isError: result.isError,
    error: result.error,
    data: enabled ? result.data : undefined,
  }
}
