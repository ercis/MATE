import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"

import { cn } from "@/lib/cn"

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-all outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        destructive:
          "bg-destructive text-white hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:bg-destructive/60 dark:focus-visible:ring-destructive/40",
        outline:
          "border bg-background shadow-xs hover:bg-accent hover:text-accent-foreground dark:border-input dark:bg-input/30 dark:hover:bg-input/50",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        ghost:
          "hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/50",
        link: "text-primary underline-offset-4 hover:underline",
        // ── GlinUI Liquid Glass surface variants (additive) ──
        glass:
          "relative isolate overflow-hidden border border-neutral-300/60 [border-top-color:var(--glass-refraction-top)] bg-white/50 text-[var(--foreground)] backdrop-blur-xl backdrop-saturate-[180%] shadow-[0_0_0_1px_rgb(255_255_255_/_0.4)_inset,0_10px_22px_-14px_rgb(15_23_42_/_0.18)] before:pointer-events-none before:absolute before:inset-x-0 before:top-0 before:h-px before:bg-gradient-to-r before:from-transparent before:via-white/70 before:to-transparent hover:-translate-y-px hover:bg-white/60 hover:shadow-[0_0_0_1px_rgb(255_255_255_/_0.5)_inset,0_14px_26px_-14px_rgb(15_23_42_/_0.22)] dark:border-white/[0.12] dark:bg-[linear-gradient(155deg,rgb(255_255_255_/_0.1),rgb(255_255_255_/_0.04))] dark:shadow-[0_0_0_1px_rgb(255_255_255_/_0.08)_inset,0_8px_20px_-12px_rgb(0_0_0_/_0.5)] dark:before:via-white/20 dark:hover:bg-[linear-gradient(155deg,rgb(255_255_255_/_0.14),rgb(255_255_255_/_0.06))]",
        frosted:
          "relative isolate overflow-hidden border border-neutral-300/70 [border-top-color:var(--glass-refraction-top)] bg-white/70 text-[var(--foreground)] backdrop-blur-[40px] backdrop-saturate-[200%] shadow-[0_0_0_1px_rgb(255_255_255_/_0.5)_inset,0_0_16px_rgb(255_255_255_/_0.15)_inset,0_10px_22px_-14px_rgb(15_23_42_/_0.22)] before:pointer-events-none before:absolute before:inset-x-0 before:top-0 before:h-px before:bg-gradient-to-r before:from-transparent before:via-white/80 before:to-transparent hover:-translate-y-px hover:bg-white/80 dark:border-white/[0.16] dark:bg-[linear-gradient(155deg,rgb(255_255_255_/_0.16),rgb(255_255_255_/_0.06))] dark:shadow-[0_0_0_1px_rgb(255_255_255_/_0.1)_inset,0_0_16px_rgb(255_255_255_/_0.04)_inset,0_8px_20px_-12px_rgb(0_0_0_/_0.5)] dark:before:via-white/25",
        liquid:
          "relative isolate overflow-hidden border border-neutral-400/40 [border-top-color:var(--glass-refraction-top)] bg-[radial-gradient(circle_at_16%_12%,rgb(255_255_255_/_0.92),transparent_44%),linear-gradient(168deg,rgb(255_255_255_/_0.72),rgb(236_236_236_/_0.42))] text-[var(--foreground)] backdrop-blur-xl backdrop-saturate-[180%] shadow-[0_1px_3px_rgb(0_0_0_/_0.08),0_0_0_1px_rgb(255_255_255_/_0.5)_inset,0_14px_28px_-16px_rgb(15_23_42_/_0.22)] before:pointer-events-none before:absolute before:inset-0 before:bg-[radial-gradient(circle_at_80%_72%,rgb(255_255_255_/_0.5),transparent_50%)] before:opacity-85 hover:-translate-y-px hover:bg-[radial-gradient(circle_at_16%_12%,rgb(255_255_255_/_0.98),transparent_44%),linear-gradient(168deg,rgb(255_255_255_/_0.82),rgb(232_232_236_/_0.52))] dark:border-white/[0.14] dark:bg-[linear-gradient(168deg,rgb(255_255_255_/_0.14),rgb(255_255_255_/_0.05))] dark:shadow-[0_0_0_1px_rgb(255_255_255_/_0.1)_inset,0_10px_24px_-12px_rgb(0_0_0_/_0.5)] dark:before:opacity-55",
        matte:
          "relative isolate overflow-hidden border border-black/10 bg-[linear-gradient(180deg,rgb(250_250_250),rgb(234_234_236))] text-neutral-900 shadow-[0_1px_0_rgb(255_255_255_/_0.92)_inset,0_8px_18px_-14px_rgb(15_23_42_/_0.3)] before:pointer-events-none before:absolute before:inset-0 before:bg-[linear-gradient(180deg,rgb(255_255_255_/_0.62),transparent)] backdrop-saturate-150 hover:-translate-y-px hover:bg-[linear-gradient(180deg,rgb(255_255_255),rgb(238_238_240))] dark:border-white/[0.14] dark:bg-[linear-gradient(180deg,rgb(53_58_67_/_0.92),rgb(34_38_46_/_0.92))] dark:text-neutral-100 dark:shadow-[0_1px_0_rgb(255_255_255_/_0.12)_inset,0_10px_24px_-14px_rgb(0_0_0_/_0.62)] dark:before:bg-[linear-gradient(180deg,rgb(255_255_255_/_0.12),transparent)]",
        glow: "border border-white/20 bg-neutral-900 text-white shadow-[0_0_0_1px_rgb(255_255_255_/_0.12)_inset,0_10px_18px_-12px_rgb(2_6_23_/_0.55),0_0_24px_rgb(255_255_255_/_0.2)] hover:-translate-y-px hover:shadow-[0_0_0_1px_rgb(255_255_255_/_0.14)_inset,0_14px_24px_-12px_rgb(2_6_23_/_0.6),0_0_36px_rgb(255_255_255_/_0.34)] dark:border-white/40 dark:bg-neutral-100 dark:text-neutral-950 dark:shadow-[0_0_0_1px_rgb(255_255_255_/_0.55),0_0_20px_rgb(255_255_255_/_0.3)]",
      },
      size: {
        default: "h-9 px-4 py-2 has-[>svg]:px-3",
        xs: "h-6 gap-1 rounded-md px-2 text-xs has-[>svg]:px-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: "h-8 gap-1.5 rounded-md px-3 has-[>svg]:px-2.5",
        lg: "h-10 rounded-md px-6 has-[>svg]:px-4",
        icon: "size-9",
        "icon-xs": "size-6 rounded-md [&_svg:not([class*='size-'])]:size-3",
        "icon-sm": "size-8",
        "icon-lg": "size-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
  }) {
  const Comp = asChild ? Slot.Root : "button"

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
