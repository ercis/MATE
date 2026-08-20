// Each module page renders its own PageContainer (listing, detail and import
// each have distinct loading/empty states), so this layout is a pass-through.
export default function ModulesLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
