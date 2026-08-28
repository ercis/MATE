"use client"

import * as React from "react"
import { cn } from "@/lib/cn"

export interface BorderBeamProps extends React.HTMLAttributes<HTMLDivElement> {
  duration?: number
  delay?: number
  /** Comet arc width, in degrees of the conic sweep. */
  size?: number
  colorFrom?: string
  colorTo?: string
  /** Ring thickness in px. */
  borderWidth?: number
  borderRadius?: string
}

export const BorderBeam = React.forwardRef<HTMLDivElement, BorderBeamProps>(
  (
    {
      className,
      duration = 6,
      delay = 0,
      size = 100,
      colorFrom = "var(--primary)",
      colorTo = "transparent",
      borderWidth = 1.5,
      borderRadius,
      style,
      ...props
    },
    ref
  ) => (
    // A conic-gradient comet rotated via the registered `--border-beam-angle`
    // (globals.css) so it interpolates smoothly and glides the rounded corners.
    // The padding-box mask (`mask-composite: exclude`) shows only the `borderWidth`
    // ring, so the sweep rides the exact card border — full size, correct radius —
    // instead of bleeding across the face like the old two-layer intersect mask.
    <div
      ref={ref}
      className={cn(
        "pointer-events-none absolute inset-0 m-0 rounded-[inherit] motion-reduce:hidden",
        "motion-safe:animate-[border-beam_var(--border-beam-duration,6s)_linear_infinite]",
        className
      )}
      style={
        {
          "--border-beam-duration": `${duration}s`,
          padding: `${borderWidth}px`,
          borderRadius,
          animationDelay: `${delay}s`,
          background: `conic-gradient(from var(--border-beam-angle, 0deg), ${colorTo} 0deg, ${colorFrom} ${size}deg, ${colorTo} ${size * 2}deg)`,
          WebkitMask: "linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)",
          WebkitMaskComposite: "xor",
          mask: "linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)",
          maskComposite: "exclude",
          ...style,
        } as React.CSSProperties
      }
      {...props}
    />
  )
)

BorderBeam.displayName = "BorderBeam"
