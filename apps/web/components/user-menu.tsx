"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { UserRound } from "lucide-react";
import { useSession } from "next-auth/react";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";

export function initials(name?: string | null, email?: string | null): string {
  const source = (name || email || "").trim();
  if (!source) return "?";
  const parts = source.split(/[\s.@]+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return source.slice(0, 2).toUpperCase();
}

export function UserMenu({ collapsed }: { collapsed: boolean }) {
  const { data: session, status } = useSession();
  const isLoading = status === "loading";
  const user = session?.user;
  const label = user?.name || user?.email || "Account";
  const pathname = usePathname();
  const active = pathname === "/profile";

  const trigger = (
    <Button
      asChild
      variant="ghost"
      size="icon"
      aria-label="Profile"
      aria-current={active ? "page" : undefined}
      className={cn(
        "h-8 w-8 cursor-pointer text-sidebar-foreground/70 hover:text-sidebar-foreground",
        active && "text-sidebar-foreground",
        isLoading && "opacity-50",
      )}
    >
      <Link href="/profile">
        {user ? (
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/15 text-[10px] font-medium text-primary">
            {initials(user.name, user.email)}
          </span>
        ) : (
          <UserRound className="h-4 w-4" />
        )}
      </Link>
    </Button>
  );

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>{trigger}</TooltipTrigger>
        <TooltipContent side="right">{label}</TooltipContent>
      </Tooltip>
    );
  }
  return trigger;
}
