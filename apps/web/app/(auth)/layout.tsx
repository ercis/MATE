import { AuroraBackground } from "@/components/glass/aurora-background";

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background px-6 py-12">
      <AuroraBackground className="opacity-70 dark:opacity-50" />
      {children}
    </div>
  );
}
