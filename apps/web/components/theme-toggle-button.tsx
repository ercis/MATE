"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import { useMounted } from "@/lib/use-mounted";

export function ThemeToggleButton({ className }: { className?: string }) {
  const { resolvedTheme, setTheme } = useTheme();
  // next-themes only resolves the theme on the client, so the server and the
  // first client render must not depend on it – otherwise the icon/label differ
  // and React throws a hydration mismatch. Render a stable default until mounted.
  const mounted = useMounted();

  const isDark = mounted && resolvedTheme === "dark";
  const Icon = isDark ? Moon : Sun;
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={
        mounted ? (isDark ? "Switch to light mode" : "Switch to dark mode") : "Toggle theme"
      }
      onClick={() => setTheme(isDark ? "light" : "dark")}
      className={className}
    >
      <Icon className="h-4 w-4" />
    </Button>
  );
}
