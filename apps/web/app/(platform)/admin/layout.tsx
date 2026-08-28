import { AdminTabs } from "@/components/admin/admin-tabs";
import { PageContainer } from "@/components/page";

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <PageContainer>
      <AdminTabs />
      <div className="pt-6">{children}</div>
    </PageContainer>
  );
}
