import { ModuleDetailClient } from "./module-detail-client";

export default async function ModuleDetailPage({
  params,
}: {
  params: Promise<{ moduleId: string }>;
}) {
  const { moduleId } = await params;
  return <ModuleDetailClient moduleId={moduleId} />;
}
