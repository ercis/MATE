/**
 * Privacy-signal detection. Returns true when the browser indicates the
 * user does NOT want to be tracked – we honour both legacy DNT and modern
 * Global Privacy Control. When this returns true the provider forces the
 * store off regardless of opt-in state.
 */
export function shouldRespectPrivacySignal(): boolean {
  if (typeof navigator === "undefined") return false;
  const nav = navigator as Navigator & {
    globalPrivacyControl?: boolean;
    msDoNotTrack?: string;
  };
  if (nav.doNotTrack === "1" || nav.doNotTrack === "yes") return true;
  if (nav.msDoNotTrack === "1") return true;
  if (nav.globalPrivacyControl === true) return true;
  return false;
}

/** Coarse-grained device class derived without exposing the raw UA. */
export function deriveUaClass(): "desktop" | "tablet" | "mobile" {
  if (typeof window === "undefined") return "desktop";
  const w = window.innerWidth || 0;
  if (w < 640) return "mobile";
  if (w < 1024) return "tablet";
  return "desktop";
}
