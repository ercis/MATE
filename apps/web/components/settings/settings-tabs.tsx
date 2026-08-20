"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/cn";
import { useAnalyticsConfig } from "@/lib/analytics-queries";

const TABS = [
  { href: "/settings/general", label: "General" },
  { href: "/settings/privacy", label: "Privacy" },
  { href: "/settings/ai", label: "AI" },
  { href: "/settings/about", label: "About" },
];

export function SettingsTabs() {
  const pathname = usePathname() ?? "";
  const cfgQuery = useAnalyticsConfig();
  // Under `force`, tracking is mandated and not user-configurable, so the
  // Privacy tab is hidden entirely (the page itself also redirects away).
  const tabs = TABS.filter(
    (t) =>
      !(t.href === "/settings/privacy" && cfgQuery.data?.onboarding_mode === "force"),
  );
  return (
    <nav className="flex gap-1 border-b border-border" aria-label="Settings sections">
      {tabs.map((t) => {
        const active = pathname.startsWith(t.href);
        return (
          <Link
            key={t.href}
            href={t.href}
            className={cn(
              "border-b-2 px-3 py-2 text-sm transition-colors cursor-pointer",
              active
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
