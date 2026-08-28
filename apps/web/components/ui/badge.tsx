import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"

import { cn } from "@/lib/cn"

const badgeVariants = cva(
  "inline-flex w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-full border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&>svg]:pointer-events-none [&>svg]:size-3",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground [a&]:hover:bg-primary/90",
        secondary:
          "bg-secondary text-secondary-foreground [a&]:hover:bg-secondary/90",
        destructive:
          "bg-destructive text-white focus-visible:ring-destructive/20 dark:bg-destructive/60 dark:focus-visible:ring-destructive/40 [a&]:hover:bg-destructive/90",
        outline:
          "border-border text-foreground [a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
        ghost: "[a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
        link: "text-primary underline-offset-4 [a&]:hover:underline",
        // ── GlinUI Liquid Glass surface variants (additive) ──
        glass:
          "relative isolate border-white/20 [border-top-color:var(--glass-refraction-top)] bg-[linear-gradient(155deg,rgb(255_255_255_/_0.62),rgb(245_245_245_/_0.36))] text-[var(--foreground)] backdrop-blur-md backdrop-saturate-[180%] shadow-[0_0_0_1px_rgb(255_255_255_/_0.2)_inset,0_4px_12px_-6px_rgb(2_6_23_/_0.25)] before:pointer-events-none before:absolute before:inset-x-0 before:top-0 before:h-px before:bg-gradient-to-r before:from-transparent before:via-white/45 before:to-transparent dark:border-white/[0.12] dark:bg-[linear-gradient(155deg,rgb(255_255_255_/_0.1),rgb(255_255_255_/_0.04))] dark:shadow-[0_0_0_1px_rgb(255_255_255_/_0.08)_inset,0_4px_12px_-6px_rgb(0_0_0_/_0.4)] dark:before:via-white/20",
        liquid:
          "relative isolate border-white/20 [border-top-color:var(--glass-refraction-top)] bg-[radial-gradient(circle_at_18%_16%,rgb(255_255_255_/_0.88),transparent_40%),linear-gradient(148deg,rgb(255_255_255_/_0.74),rgb(232_232_232_/_0.5))] text-[var(--foreground)] backdrop-blur-xl backdrop-saturate-[180%] shadow-[0_0_0_1px_rgb(255_255_255_/_0.2)_inset,0_6px_16px_-8px_rgb(2_6_23_/_0.3)] before:pointer-events-none before:absolute before:inset-0 before:bg-[radial-gradient(circle_at_80%_72%,rgb(255_255_255_/_0.34),transparent_48%)] before:opacity-80 dark:border-white/[0.12] dark:bg-[linear-gradient(145deg,rgb(255_255_255_/_0.14),rgb(255_255_255_/_0.05))] dark:shadow-[0_0_0_1px_rgb(255_255_255_/_0.1)_inset,0_6px_16px_-8px_rgb(0_0_0_/_0.45)] dark:before:opacity-55",
        matte:
          "relative isolate border-black/10 bg-[linear-gradient(180deg,rgb(250_250_250),rgb(234_234_236))] text-neutral-900 shadow-[0_1px_0_rgb(255_255_255_/_0.92)_inset,0_4px_10px_-8px_rgb(15_23_42_/_0.25)] before:pointer-events-none before:absolute before:inset-0 before:bg-[linear-gradient(180deg,rgb(255_255_255_/_0.62),transparent)] dark:border-white/[0.14] dark:bg-[linear-gradient(180deg,rgb(53_58_67_/_0.92),rgb(34_38_46_/_0.92))] dark:text-neutral-100 dark:shadow-[0_1px_0_rgb(255_255_255_/_0.12)_inset,0_4px_10px_-8px_rgb(0_0_0_/_0.5)] dark:before:bg-[linear-gradient(180deg,rgb(255_255_255_/_0.12),transparent)]",
        glow: "border-white/20 bg-neutral-900 text-white shadow-[0_0_0_1px_rgb(255_255_255_/_0.12)_inset,0_0_16px_rgb(255_255_255_/_0.15)] dark:border-white/40 dark:bg-neutral-100 dark:text-neutral-950 dark:shadow-[0_0_0_1px_rgb(255_255_255_/_0.55),0_0_16px_rgb(255_255_255_/_0.25)]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span"

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
