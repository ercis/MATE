import { cn } from "@/lib/cn";

/**
 * The Mate brand mark: a 2×2 grid of rounded tiles (three outlined, the
 * bottom-right filled). Rendered inline with `currentColor`, so it inherits the
 * surrounding text color — put it on a `text-foreground`/`text-sidebar-foreground`
 * surface and it comes out black on light, white on dark, matching the two
 * source assets in `public/pm-mate-icon-{black,white}.svg`. Shared component
 * (no "use client"): usable from server and client trees alike.
 *
 * `animated` runs a clockwise "loader" pulse — each tile scales up in turn
 * (top-left → top-right → bottom-right → bottom-left), looping. The active
 * window per tile (0→55%) is wider than the per-tile stagger (25% = D/4), so
 * neighbours overlap and the highlight reads as one traveling wave rather than
 * four discrete blinks. Used on the first-load splash.
 *
 * `animateOnHover` runs the exact same pulse, but only while the logo is
 * hovered — at rest no animation applies, so the mark stays crisp at full
 * opacity. Used on the sidebar mark. Both respect `prefers-reduced-motion`.
 */
export function MateLogo({
  className,
  animated = false,
  animateOnHover = false,
}: {
  className?: string;
  animated?: boolean;
  animateOnHover?: boolean;
}) {
  const motion = animated || animateOnHover;
  // DOM order is already clockwise (TL, TR, BR, BL); a quarter-cycle stagger
  // (duration / 4 = 0.6s) per tile makes the highlight travel around the ring.
  const delay = (i: number): React.CSSProperties | undefined =>
    motion ? { animationDelay: `${i * 0.6}s` } : undefined;

  return (
    <svg
      viewBox="0 0 64 64"
      fill="none"
      aria-hidden
      className={cn(
        animated && "mate-logo-anim",
        animateOnHover && "mate-logo-hover",
        className,
      )}
    >
      <rect x="8" y="8" width="20" height="20" rx="5.5" stroke="currentColor" strokeWidth="2.5" style={delay(0)} />
      <rect x="36" y="8" width="20" height="20" rx="5.5" stroke="currentColor" strokeWidth="2.5" style={delay(1)} />
      <rect x="36" y="36" width="20" height="20" rx="5.5" fill="currentColor" style={delay(2)} />
      <rect x="8" y="36" width="20" height="20" rx="5.5" stroke="currentColor" strokeWidth="2.5" style={delay(3)} />
      {motion && (
        <style>{`
          .mate-logo-anim rect,
          .mate-logo-hover rect {
            transform-box: fill-box;
            transform-origin: center;
          }
          .mate-logo-anim rect,
          .mate-logo-hover:hover rect {
            animation: mate-tile-pulse 2.4s cubic-bezier(0.4, 0, 0.2, 1) infinite;
          }
          @keyframes mate-tile-pulse {
            0%   { transform: scale(1);    opacity: 0.9; }
            18%  { transform: scale(1.16); opacity: 1;   }
            55%  { transform: scale(1);    opacity: 0.9; }
            100% { transform: scale(1);    opacity: 0.9; }
          }
          @media (prefers-reduced-motion: reduce) {
            .mate-logo-anim rect { animation: none; opacity: 1; }
            .mate-logo-hover:hover rect { animation: none; }
          }
        `}</style>
      )}
    </svg>
  );
}
