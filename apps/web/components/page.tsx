import * as React from "react"

import { cn } from "@/lib/cn"

// Unified page chrome. Every page under (platform) renders its content inside
// a PageContainer so padding, width and heading sizes are identical across the
// app. See apps/web/UI.md for the full system. One width everywhere: fluid
// full-width, capped at a wide ceiling (max-w-[1760px]) so text/tables don't
// stretch on ultra-wide monitors. Padding is responsive and intentionally
// low-margin (px-4 → px-8, py-6).

function PageContainer({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="page-container"
      className={cn(
        "mx-auto w-full max-w-[1760px] px-4 py-6 sm:px-6 lg:px-8",
        className,
      )}
      {...props}
    />
  )
}

function PageHeader({ className, ...props }: React.ComponentProps<"header">) {
  return (
    <header
      data-slot="page-header"
      className={cn(
        "flex flex-wrap items-start justify-between gap-4 pb-6",
        className,
      )}
      {...props}
    />
  )
}

function PageTitle({ className, ...props }: React.ComponentProps<"h1">) {
  return (
    <h1
      data-slot="page-title"
      className={cn("text-2xl font-semibold tracking-tight", className)}
      {...props}
    />
  )
}

function PageDescription({ className, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      data-slot="page-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

function PageActions({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="page-actions"
      className={cn("flex items-center gap-2", className)}
      {...props}
    />
  )
}

export { PageContainer, PageHeader, PageTitle, PageDescription, PageActions }
