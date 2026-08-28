"use client";

import { useEffect, useState } from "react";

/**
 * Debounce a fast-changing value (search inputs driving server queries).
 *
 * The Events/Variants tabs key TanStack queries off free-text inputs; without
 * debouncing every keystroke fires a DuckDB scan on the API. Returns the value
 * only after it has been stable for `delayMs` (the initial value is returned
 * immediately).
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    if (Object.is(value, debounced)) return;
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs, debounced]);

  return debounced;
}
