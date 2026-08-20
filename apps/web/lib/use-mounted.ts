"use client";

import { useEffect, useState } from "react";

/**
 * Returns `false` on the server and on the first client render, then `true`
 * after the component has mounted in the browser.
 *
 * Use this to gate anything that only the client knows (resolved theme,
 * `window`, `localStorage`, locale-dependent formatting). Rendering that
 * client-only value before mount makes the server HTML differ from the first
 * client render → React hydration mismatch (minified error #418). Render a
 * stable, server-safe default until `useMounted()` is true.
 */
export function useMounted(): boolean {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}
