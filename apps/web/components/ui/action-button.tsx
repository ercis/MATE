"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";

interface ActionButtonProps extends React.ComponentProps<typeof Button> {
  isPending?: boolean;
  isSuccess?: boolean;
}

/**
 * Button with built-in mutation feedback: a spinner while `isPending`, then a
 * ~1.2s check-mark flash when the action lands. Pass TanStack's
 * `isPending`/`isSuccess` straight through – the mutation logic (optimistic
 * updates included) stays entirely with the caller.
 */
export function ActionButton({
  isPending = false,
  isSuccess = false,
  disabled,
  children,
  ...props
}: ActionButtonProps) {
  const [flash, setFlash] = useState(false);
  const wasPending = useRef(false);

  useEffect(() => {
    const was = wasPending.current;
    wasPending.current = isPending;
    if (was && !isPending && isSuccess) {
      setFlash(true);
      const id = window.setTimeout(() => setFlash(false), 1200);
      return () => window.clearTimeout(id);
    }
  }, [isPending, isSuccess]);

  return (
    <Button disabled={disabled || isPending} {...props}>
      {isPending ? (
        <Loader2 className="animate-spin" />
      ) : flash ? (
        <Check className="animate-in zoom-in-50 duration-200" />
      ) : null}
      {children}
    </Button>
  );
}
