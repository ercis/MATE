import * as React from "react"

import { cn } from "@/lib/cn"

// Card sections are spaced by the outer Card's `gap-5` and the outer
// `py-6` / horizontal `px-6` on each section. This means no section needs
// its own vertical padding, so consumers should NOT add `pb-3`, `p-6`,
// etc. – those layered on top of the defaults and produced the uneven
// padding callers had to patch around. If you want a compact card,
// override the outer Card with `className="py-0 gap-0"` and put your own
// padding on the inner section (see components/processes/module-card.tsx).

type CardVariant = "default" | "elevated"

// Surface treatments – both opaque. The translucent `glass`/`frosted`/`liquid`
// surfaces were removed: cards read as solid panels everywhere (settings,
// admin, modules, process detail). Don't reintroduce backdrop-blur here; the
// blur belongs to floating chrome (dialogs, popovers, the sidebar), not to
// cards sitting in page flow.
const cardSurfaces: Record<CardVariant, string> = {
  default: "border bg-card shadow-md",
  elevated: "border bg-surface-elevated shadow-lg",
}

function Card({
  className,
  variant = "default",
  ...props
}: React.ComponentProps<"div"> & { variant?: CardVariant }) {
  return (
    <div
      data-slot="card"
      data-variant={variant}
      className={cn(
        "flex flex-col gap-5 rounded-[18px] py-6 text-card-foreground",
        cardSurfaces[variant],
        className,
      )}
      {...props}
    />
  )
}

function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-header"
      className={cn(
        "@container/card-header grid auto-rows-min grid-rows-[auto_auto] items-start gap-1.5 px-6 has-data-[slot=card-action]:grid-cols-[1fr_auto]",
        className,
      )}
      {...props}
    />
  )
}

function CardTitle({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-title"
      className={cn("text-base font-bold leading-tight tracking-tight", className)}
      {...props}
    />
  )
}

function CardDescription({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-description"
      className={cn("text-sm font-light text-muted-foreground", className)}
      {...props}
    />
  )
}

function CardAction({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-action"
      className={cn(
        "col-start-2 row-span-2 row-start-1 self-start justify-self-end",
        className,
      )}
      {...props}
    />
  )
}

function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div data-slot="card-content" className={cn("px-6", className)} {...props} />
  )
}

function CardFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-footer"
      className={cn("flex items-center justify-end gap-3 px-6", className)}
      {...props}
    />
  )
}

export {
  Card,
  CardHeader,
  CardFooter,
  CardTitle,
  CardAction,
  CardDescription,
  CardContent,
}
