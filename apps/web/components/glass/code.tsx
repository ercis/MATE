import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/cn"

const codeVariants = cva(
  "inline-flex items-center rounded-md border px-1.5 py-0.5 font-mono transition-colors duration-fast ease-standard motion-reduce:transition-none",
  {
    variants: {
      variant: {
        default: "border-[var(--border)] bg-[var(--surface)] text-[var(--foreground)] dark:border-white/15",
        glass:
          "border-white/20 [border-top-color:var(--glass-refraction-top)] bg-[var(--glass-1-surface)] text-[var(--foreground)] dark:border-white/[0.14] dark:bg-white/[0.05]",
        outline: "border-[var(--border)] bg-transparent text-[var(--foreground)] dark:border-white/20",
        ghost: "border-transparent bg-[var(--foreground)]/[0.07] text-[var(--foreground)] dark:bg-white/[0.1]"
      },
      size: {
        sm: "text-[10px]",
        md: "text-xs",
        lg: "text-sm"
      }
    },
    defaultVariants: {
      variant: "default",
      size: "md"
    }
  }
)

export type CodeProps = React.HTMLAttributes<HTMLElement> & VariantProps<typeof codeVariants>

export const Code = React.forwardRef<HTMLElement, CodeProps>(
  ({ className, variant, size, ...props }, ref) => {
    return <code ref={ref} className={cn(codeVariants({ variant, size }), className)} {...props} />
  }
)

Code.displayName = "Code"
