"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/cn";

const TABS = [
  { href: "/admin/overview", label: "Overview" },
  { href: "/admin/logs", label: "Event logs" },
  { href: "/admin/jobs", label: "Jobs" },
  { href: "/admin/controls", label: "Controls" },
  { href: "/admin/modules", label: "Modules" },
  { href: "/admin/users", label: "Users" },
  { href: "/admin/teams", label: "Teams" },
  { href: "/admin/system", label: "System" },
  { href: "/admin/export", label: "Data export" },
];

/** Sub-navigation shared by the admin pages (mirrors SettingsTabs). Visibility
 * of the whole admin area is gated by the `admin` role in the sidebar; each
 * page + API also enforces it server-side. */
export function AdminTabs() {
  const pathname = usePathname() ?? "";
  return (
    <nav className="flex gap-1 border-b border-border" aria-label="Admin sections">
      {TABS.map((t) => {
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
