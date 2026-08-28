import { PanelSkeleton } from "@/components/skeletons";

// This route mounts a module panel inside a bare PageContainer - there is no
// tab bar or KPI row here (that's the process detail route one level up), so a
// DetailSkeleton would promise chrome the page never renders.
export default function Loading() {
  return <PanelSkeleton />;
}
